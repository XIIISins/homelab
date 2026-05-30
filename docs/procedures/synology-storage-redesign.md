<!-- docs/procedures/synology-storage-redesign.md -->

# Synology storage redesign — volume re-layout + LUN right-sizing

Lift the Munin (DS223J) storage from one mixed Volume1 (backups + 10 oversized iSCSI LUNs) to **purpose-separated, right-sized volumes**: a dedicated backup volume, a dedicated iSCSI volume holding the 10 current workloads, and (deferred) a media volume. Reclaims ~290 GB of empty LUN reservation and establishes the **"volume = 10 LUNs"** + **provision-small-grow-on-demand** model.

**Estimated wall-clock:** multi-session. Stage 1 (PVC migration) ~2-4 h with verification; Stage 2 (PBS relocation) bounded by the 74 GB chunk copy; Stage 3 (cleanup) ~30 min. Per-workload downtime is minutes (one workload at a time).

**Risk:** this moves **live data** — the Vault Raft store and the Outline wiki's S3 content. Nothing is irreversible until Stage 3 deletes the old LUNs/volume; every prior step leaves the source LUN intact (`reclaimPolicy: Retain`). **Do not delete an old LUN until its migrated workload is verified healthy.**

---

## Why this is needed

- **DS223J = one ~3.5 TB RAID1 btrfs storage pool.** All volumes are carved from it.
- **iSCSI LUNs reserve their full size in DSM's volume accounting** (btrfs blocks are thin — actual usage is tiny — but the synology-csi LUNs carry a space reservation, so DSM debits the full size to prevent over-provisioning). DSM shows `X GB / X GB`. This is *not* in-place editable and *not* releasable per-LUN here (operator-confirmed 2026-05-30).
- **Per-volume cap = 10 iSCSI targets/LUNs.** Volume1 is at 10/10; the Outline+Garage deploy already hit this (Semaphore + Outline Redis forced to emptyDir).
- **Synology cannot shrink a volume** — DSM only *expands*. Reclaiming Volume1's overhead therefore requires **delete + recreate**, not in-place shrink.
- Result today: Volume1 carries ~333 GB of LUN reservation against ~2 GB of actual data, plus the backups, tripping critical-full warnings (it was extended to 720 GB to cope).

Reference gotchas: CLAUDE.md → "Synology DS223J iSCSI LUN cap", "First-deploy mkfs.ext4 … TRIM-bound", "Synology CSI iSCSI volumes need a chown initContainer".

---

## Target layout

| Volume | Size | Purpose | Notes |
|--------|------|---------|-------|
| **Backup** | ~120 GB | `proxmox-backup` PBS datastore | 74 GB now + dedup growth for +12 VMs; expand when hyper-backup is configured |
| **iSCSI-A** | ~100 GB | the 10 migrated, right-sized LUNs | born at **10/10**, closed to new workloads |
| **iSCSI-B** | ~50 GB | new workloads (11th+) | **deferred** — create when the next workload needs it |
| **Media** | 2 TB | arr-stack | **deferred** — create when arr-stack lands |

- DSM assigns volume numbers; the synology-csi `location: /volumeN` must match the **actual** number DSM hands out. "iSCSI-A/B" here are logical names.
- VM/LXC disks live on **local Proxmox LVM-thin**, not Synology — so jotunheim's 6 K3s VM disks do *not* consume Synology iSCSI. iSCSI demand = K8s PVCs (asgard now, jotunheim's own synology-csi later).

---

## Per-LUN sizing + migration class

| PVC | Old | New | Class | Reason |
|-----|-----|-----|-------|--------|
| `garage/data-garage-0` | 200 Gi | **20 Gi** | **C** rsync | Outline S3, ~5 GB used; grows online for Immich later |
| `garage/meta-garage-0` | 10 Gi | **10 Gi** | **C** rsync | carries Garage layout + node id — must preserve |
| `monitoring/…victorialogs…-0` | 50 Gi | **5 Gi** | **C** rsync | logs preserved (operator: keep), ~1 GB/mo at 1-mo retention |
| `monitoring/…victoriametrics…-0` | 50 Gi | **10 Gi** | **C** rsync | metrics preserved, **retention 6mo→1mo**, ~5 GB/mo |
| `authentik/data-authentik-redis-0` | 1 Gi | **1 Gi** | **A** fresh | cache — rebuilds |
| `netbox/valkey-data-…-0` | 2 Gi | **2 Gi** | **A** fresh | cache — rebuilds |
| `teamspeak/data-teamspeak-0` | 5 Gi | **5 Gi** | **C** rsync | holds TS3 identity/config files |
| `vault/data-vault-0` | 5 Gi | **5 Gi** | **B** Raft | re-syncs from leader |
| `vault/data-vault-1` | 5 Gi | **5 Gi** | **B** Raft | re-syncs from leader |
| `vault/data-vault-2` | 5 Gi | **5 Gi** | **B** Raft | re-syncs from leader |

Sum of new reservations ≈ **63 Gi** → 100 GB iSCSI-A leaves room for those 10 to grow. VM at 5 GB would sit at its steady-state ceiling, hence 10 GB.

---

## Migration classes (reusable sub-procedures)

`allowVolumeExpansion: true` only grows; `reclaimPolicy: Retain` keeps the old PV/LUN after PVC delete. StatefulSet `volumeClaimTemplates` are **immutable** — changing SC/size requires deleting the STS (`--cascade=orphan` keeps pods) and letting Flux recreate it.

### Class A — Recreate-fresh (data discarded)

For **pure cache** only — Authentik-Redis + NetBox-Valkey, which rebuild on restart. (VictoriaLogs/Metrics were moved to Class C — operator opted to preserve their history.)

```
# 1. Edit the helmrelease: storageClass → synology-csi-iscsi-retain-volA,
#    persistentVolume.size → new size (and VM: retentionPeriod "6" → "1").
#    Commit + push (Flux deploy path).
# 2. Delete the STS so Flux recreates it with the new (immutable) template:
kubectl delete sts <name> -n <ns>
# 3. Delete the old PVC + PV (Retain → PV Released, old LUN persists on Volume1):
kubectl delete pvc <pvc> -n <ns>
kubectl get pvc <pvc> -n <ns>            # confirm gone
kubectl delete pv <old-pv>               # note the name first for Stage 3 cleanup
# 4. Reconcile → STS recreated, new PVC provisioned on iSCSI-A, pod starts fresh:
flux reconcile helmrelease <name> -n <ns>
# 5. Verify: pod Ready + ingesting (VL/VM: query vmui; redis/valkey: app healthy).
```

### Class B — Vault Raft re-sync (per-node, no data loss, keep 2/3)

Vault is a 3-node Raft cluster with **required** anti-affinity (3 pods / 3 workers). A single `volumeClaimTemplate` can't mix SCs across ordinals, so all three move — but one node at a time, letting Raft re-replicate. Accept **2/3 voters** during each node's window (Vault stays fully read+write — see CLAUDE.md "Vault Helm chart uses required pod anti-affinity").

```
# 1. Edit vault helmrelease: storageClass → ...-volA (keep 5Gi). Commit + push.
# 2. Recreate the STS with the new template (adopts existing old PVCs for now):
kubectl delete sts vault -n vault --cascade=orphan   # pods keep running, quorum intact
flux reconcile helmrelease vault -n vault
# 3. For each node, NON-LEADER FIRST (do the leader last):
#    a. If it's the leader, step down cleanly first:
kubectl exec -n vault vault-<N> -c vault -- env VAULT_TOKEN=<root> vault operator step-down
#    b. Replace its storage:
kubectl delete pod vault-<N> -n vault
kubectl delete pvc data-vault-<N> -n vault
kubectl delete pv <old-pv>                            # note name for Stage 3
#    c. STS recreates pod-<N> → new PVC on iSCSI-A → KMS auto-unseal → Raft join + resync.
#    d. WAIT for healthy before the next node:
kubectl exec -n vault vault-0 -c vault -- env VAULT_TOKEN=<root> vault operator raft list-peers
#       (all 3 voters present, one is leader) — only then proceed to the next ordinal.
```

Root token: 1P item `[Bootstrap] - Manual - Vault - Root token` (CLAUDE.md "Vault root token is in 1Password"). KMS auto-unseal means new nodes unseal without manual keys.

### Class C — Rsync static-rebind (preserve data, single-replica STS)

For Garage (real wiki S3) + Teamspeak (identity files). The fiddly one — copies data to a right-sized PV, then statically rebinds it under the STS-expected PVC name.

```
# 0. Quiesce: scale dependents down first. Garage: scale Outline → 0 to stop S3
#    writes, THEN Garage → 0. Teamspeak: scale → 0 (do-ts3 SRV fallback covers).
kubectl scale deploy outline -n outline --replicas=0      # garage only
kubectl scale sts <name> -n <ns> --replicas=0             # (suspend Flux for it first)
flux suspend helmrelease <name> -n <ns>                   # or suspend the Kustomization

# 1. Pre-create the right-sized TARGET PVC (temp name) on iSCSI-A:
kubectl apply -f - <<'YAML'
apiVersion: v1
kind: PersistentVolumeClaim
metadata: { name: <data>-<sts>-0-mig, namespace: <ns> }
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: synology-csi-iscsi-retain-volA
  resources: { requests: { storage: <new-size> } }
YAML

# 2. Migrator pod mounts OLD (ro) + NEW, rsync:
kubectl apply -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata: { name: migrator, namespace: <ns> }
spec:
  restartPolicy: Never
  containers:
  - name: m
    image: alpine:3.20
    command: ["sh","-c","apk add --no-cache rsync && rsync -aHAX --numeric-ids /old/ /new/ && ls -la /new && du -sh /old /new"]
    volumeMounts: [{name: old, mountPath: /old, readOnly: true},{name: new, mountPath: /new}]
  volumes:
  - { name: old, persistentVolumeClaim: { claimName: <data>-<sts>-0 } }
  - { name: new, persistentVolumeClaim: { claimName: <data>-<sts>-0-mig } }
YAML
kubectl logs -f migrator -n <ns>     # verify rsync completed + du sizes match
kubectl delete pod migrator -n <ns>

# 3. Static rebind — give the migrated PV the STS-expected PVC name:
NEWPV=$(kubectl get pvc <data>-<sts>-0-mig -n <ns> -o jsonpath='{.spec.volumeName}')
kubectl delete pvc <data>-<sts>-0-mig -n <ns>        # temp PVC gone; PV → Released (Retain keeps data)
kubectl delete pvc <data>-<sts>-0 -n <ns>            # old PVC gone; old PV → Released (old LUN persists)
kubectl delete pv <old-pv>                           # note name for Stage 3 cleanup
kubectl patch pv $NEWPV --type merge -p '{"spec":{"claimRef":null}}'   # free the new PV
kubectl apply -f - <<YAML
apiVersion: v1
kind: PersistentVolumeClaim
metadata: { name: <data>-<sts>-0, namespace: <ns> }
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: synology-csi-iscsi-retain-volA
  volumeName: $NEWPV
  resources: { requests: { storage: <new-size> } }
YAML
kubectl get pvc <data>-<sts>-0 -n <ns>   # STATUS Bound to $NEWPV

# 4. Update the STS template (SC + size) to match, recreate, resume:
#    Edit helmrelease values (storageClass + size), commit + push.
kubectl delete sts <name> -n <ns>        # scaled to 0, no pods; Flux recreates with new template
flux resume helmrelease <name> -n <ns>
flux reconcile helmrelease <name> -n <ns>
kubectl scale sts <name> -n <ns> --replicas=1   # if not auto

# 5. Verify, then un-quiesce dependents:
#    Garage: layout intact (admin API GetClusterLayout version >= 1), Outline reads docs.
kubectl scale deploy outline -n outline --replicas=1
```

For **Garage** apply Class C to **both** `data-garage-0` (200→20) and `meta-garage-0` (10→10, carries identity) while Garage is down; bring Garage up only after both are rebound.

---

## Stage 0 — Prep (no data moves)

1. **Confirm code is pushed + Flux healthy** — pushing `main` is the deploy path; verify `flux get kustomizations` clean before starting.
2. **VM retention 6mo→1mo** — edit `k8s/asgard/apps/victoriametrics/helmrelease.yaml`: `retentionPeriod: "6"` → `"1"`. (Independent, harmless, shrinks VM's growth now. Note: tiered downsampling is VictoriaMetrics *Enterprise*-only; long-term low-res infra trends are already covered by **Zabbix trends** — no OSS equivalent needed.)
3. **Delete `uploads` share** (DSM → Control Panel → Shared Folder → `uploads` → Delete) — purpose forgotten, operator-confirmed disposable.
4. **PBS baseline restore test** — before touching backups, prove a restore works from the current datastore (PBS UI → pick a recent snapshot → File Restore / test). This is your rollback anchor for Stage 2.
5. **Snapshot current state** for the record + Stage 3 cleanup list:
   ```
   kubectl get pv,pvc -A -o wide > ~/storage-redesign-before.txt
   # DSM: screenshot SAN Manager → LUN list (the 10 names + Volume1 placement)
   # DSM: Storage Manager → note pool free space + Volume1 used
   ```
6. **DSM volume-create prep** — see "What to do in Synology (prep)" below.

**Rollback (Stage 0):** all reversible — revert the helmrelease edit; `uploads` is gone (was disposable).

---

## Stage 1 — iSCSI-A + migrate the 10 PVCs

1. **DSM: create iSCSI-A volume** (~100 GB) on the pool. Record the assigned number (e.g. `/volume3`).
2. **Add the StorageClass** — in `k8s/asgard/infrastructure/synology-csi/helmrelease.yaml`, add under `storageClasses:`:
   ```yaml
       iscsi-retain-volA:
         parameters:
           protocol: iscsi
           dsm: "10.0.254.20"
           location: /volume<N>     # the number DSM assigned
         reclaimPolicy: Retain
   ```
   Commit + push → `flux reconcile hr synology-csi -n synology-csi` → confirm `kubectl get sc synology-csi-iscsi-retain-volA`.
3. **Migrate in this order** (safest first, stateful last):
   - **Class A** (fresh): authentik-redis, netbox-valkey (pure caches).
   - **Class C** (rsync): victorialogs, victoriametrics (with retention edit), then teamspeak, then garage (meta + data together). Do VL/VM first as the easy Class-C warm-up (tiny data) before the higher-stakes Garage.
   - **Class B** (Raft): vault-2 → vault-1 → vault-0 (leader last, verify peers between each).
4. **Verify each workload** before moving to the next. Do NOT delete any old LUN yet — they stay on Volume1 as the rollback.

**Rollback (Stage 1, per workload):** old PV is `Released`, not deleted — recreate the workload's PVC bound back to the old PV (`volumeName: <old-pv>`, clear its claimRef) and revert the helmrelease.

---

## Stage 2 — Backup volume + PBS datastore relocation

1. **DSM: create the backup volume** (~120 GB) + an NFS shared folder for the PBS datastore (mirror the current `proxmox-backup` export settings — squash, allowed clients `10.0.11.20`).
2. **Stop PBS scheduled backups** (PBS UI → Datastore → disable sync/prune/GC jobs; or stop the verify/backup schedules) so the chunk store is quiescent.
3. **Copy the datastore** — rsync `proxmox-backup`'s chunk store from the old share to the new (volume-to-volume on the same NAS = internal copy). Preserve everything:
   ```
   rsync -aHAX --numeric-ids --info=progress2 /<old-mount>/proxmox-backup/ /<new-mount>/proxmox-backup/
   ```
   (Run from wherever both shares are mounted — the PBS LXC `/mnt`, or a Synology shell.)
4. **Repoint PBS at the new share** — update the NFS mount backing the PBS datastore path to the new volume's export (keeping the datastore *path* the same means PBS needs no reconfig; otherwise update the datastore in PBS). Confirm PBS → Datastore → Summary shows the same used + snapshot count.
5. **Verify a restore** from the relocated datastore (same test as Stage 0.4). **This is the gate** before deleting old Volume1.
6. **Re-enable PBS jobs.**

**Rollback (Stage 2):** the old `proxmox-backup` share on Volume1 is untouched until Stage 3 — repoint PBS back to it.

---

## Stage 3 — Cutover + cleanup (the irreversible step)

Only after **every** migrated workload is verified healthy AND PBS restore-tested on the new volume:

1. **Delete old Volume1 iSCSI LUNs** (DSM → SAN Manager) — the 10 by their `pvc-…` names recorded in Stage 0. Also `kubectl delete pv <old-pv>` for any still `Released`.
2. **Delete the old `proxmox-backup` share** on Volume1 (data now lives on the backup volume).
3. **Delete old Volume1** (DSM → Storage Manager) → reclaims ~720 GB to the pool.
4. **Drop the default StorageClass** — with iSCSI-A at 10/10, a default pointing at a full volume is a foot-gun. Set `isDefault: false` (or remove the default annotation) and rely on **explicit `storageClassName` per workload** (manifests already do this). New workloads → iSCSI-B's SC.
5. **Docs:**
   - CLAUDE.md "Storage / data" invariant: single SC → **per-volume model** (volume = 10 LUNs, explicit SC naming, no default, provision-small-grow-on-demand). Add a gotcha: Synology can't shrink volumes; LUNs reserve full size in DSM accounting.
   - `docs/operations/build-sequence.md` + `decisions.md`: record the redesign + the provision-small policy.
   - `docs/services/garage.md` / sizing comments: note the right-sized LUNs.

---

## Deferred (not in this pass)

- **iSCSI-B** (~50 GB) — create when the 11th workload lands (volume + `...-volB` SC, ~5 min).
- **Media volume** (2 TB) — create when arr-stack is deployed.
- **hyper-backup** — when configured, expand the backup volume (volumes grow online).
- **Volume1 in-place shrink** — N/A; Synology can't, hence the recreate above.

---

## Risk register

- **Vault quorum** — never replace two Vault nodes at once; verify `raft list-peers` (all 3) between nodes. KMS auto-unseal must be working (test on vault-2 first, the throwaway-safest).
- **Garage identity** — the layout/node-id is in `meta`, not `data`. If `meta` migration is botched, Garage may re-init empty and Outline loses its S3 backing. Verify `meta` rebind + Garage `GetClusterLayout` version ≥ 1 before declaring success; keep the old `meta` LUN until Outline is confirmed reading documents.
- **PBS** — the old datastore is the only backup copy until Stage 2 verifies the new one. Do not skip the restore test.
- **mkfs TRIM window** — first mount of each new LUN runs mkfs.ext4 with a TRIM pass (~10 GB/min over 1 GbE); a 20 GB LUN ≈ 2 min, expect `DeadlineExceeded` retries during formatting (CLAUDE.md gotcha). Not an error.
- **chown init** — the new LUNs are fresh ext4; the chown-init pattern on Vault/Garage/Teamspeak/Redis/Valkey still applies (already in their specs).
- **LUN-cap during migration** — iSCSI-A fills 0 → 10/10 as workloads migrate; the **old LUNs stay on Volume1** (counted there, not on iSCSI-A) until Stage 3. Class C's temp `-mig` PVC provisions the LUN that *becomes* that workload's final LUN — the rebind renames the *claim*, not the LUN — so iSCSI-A **never exceeds 10**. The only requirement is a free slot on iSCSI-A before each `-mig` create, which always holds while filling toward 10.
