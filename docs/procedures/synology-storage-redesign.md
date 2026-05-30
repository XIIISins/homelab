<!-- docs/procedures/synology-storage-redesign.md -->

# Synology storage redesign — tiering off the iSCSI LUN cap

Move K8s persistent storage off a single oversized, LUN-capped iSCSI volume onto a **storage tier per workload class** — NFS for file-class, local-path for app-replicated, iSCSI only for the block-critical few, emptyDir for caches. Reclaims ~290 GB of empty LUN reservation, frees the DS223j's ~10-LUN ceiling permanently, and right-sizes everything. Separately shrinks the mixed Volume1 down to a dedicated backup volume.

**Estimated wall-clock:** multi-session. The workload migration is ~per-workload data movement (minutes each); the Volume1/PBS rework is bounded by a ~74 GB chunk copy.

**Risk:** moves **live data** — the Vault Raft store and the Outline wiki's S3 content. Nothing is irreversible until old LUNs are deleted; `reclaimPolicy: Retain` keeps every source LUN intact until its migrated workload is verified. **Never delete an old LUN until its workload is confirmed healthy on the new tier.**

---

## Why this changed shape (the pivot)

The original plan was "right-size the 10 iSCSI LUNs onto a second volume." A smoke-test (2026-05-30) killed that premise:

- **The DS223j iSCSI LUN cap is DSM-WIDE (~10 total), NOT per-volume** — confirmed empirically: creating an 11th LUN on an *empty* Volume2 failed with `Number of LUN reach limit`, and DSM's UI refuses to create more LUNs at all. (Published spec is even lower — Max LUN 4 / Target 2 — yet we run 10/10; the enforced runtime ceiling is ~10 and is a **hard, non-raisable model limit** on the 1 GB-RAM ARM unit.)
- So **more volumes do NOT add LUN slots.** The ceiling is total LUNs across the whole NAS.
- iSCSI LUNs are **block devices = single-mount (RWO) by nature** — you can't share a LUN across pods without a filesystem layer on top (which is just NFS reinvented). So "pack more workloads per LUN" over iSCSI isn't viable either.

**The fix is to stop using iSCSI for everything.** Most of the 10 LUNs were file-class workloads wasting block storage. Tier them: NFS (no LUN limit) for file-class, local-path for app-replicated, iSCSI only for the genuine block-single-instance few.

CLAUDE.md gotcha corrected accordingly ("Synology DS223J iSCSI LUN cap" — was wrongly "per-Volume", is DSM-wide).

---

## Storage tiers

| Tier | StorageClass | Backed by | Use for | LUN cost |
|------|-------------|-----------|---------|----------|
| **NFS** | `nfs-client` | Synology share `/volume2/k8s-nfs` via `csi-driver-nfs` | file-class: append/large-file, fsync-tolerant | **0** |
| **local-path** | `local-path` | dedicated 50G `scsi1` `/data` disk per worker (xfs) | app-replicated state (Raft/quorum) | **0** |
| **iSCSI** | `synology-csi-iscsi-retain-vol2` | Synology LUN on Volume2 | block-critical single-instance: mmap/fsync (LMDB, BoltDB) | **1 each** |
| **emptyDir** | — | pod ephemeral | pure cache (rebuilds on restart) | **0** |

- **NFS** is live + validated (csi-driver-nfs 4.13.2, one `pvc-<uuid>` subdir per PV inside the single `k8s-nfs` share — **no shared-folder-per-PV pollution**; that's synology-csi's NFS mode, deliberately not used).
- **local-path** is live (2026-05-30) — Rancher `local-path-provisioner` v0.0.36 via Flux (`nodePathMap → /data`), backed by a dedicated 50G `scsi1` data disk per worker (xfs, blanket `context=container_file_t` SELinux mount). K3s's built-in `local-storage` stays **disabled**. Ready for the Vault tier. See CLAUDE.md "local-path-provisioner" gotchas.
- **iSCSI** stays only where block semantics are load-bearing (memory-mapped DBs hate NFS).

---

## Per-workload tier assignment

| Workload | Now | → Tier | Why / method |
|----------|-----|--------|--------------|
| `vault` ×3 | iSCSI 5Gi | **local-path** | Raft replicates across 3 nodes → node-local is ideal + lower latency; frees 3 LUNs. Per-node recreate, Raft re-syncs. |
| `garage/data` | iSCSI 200Gi | **NFS** | S3 object blocks = large files, NFS-fine. rsync-copy. |
| `garage/meta` | iSCSI 10Gi | **iSCSI (vol2)** | LMDB (mmap) — MUST stay block. Right-size + move to Volume2. |
| `teamspeak` | iSCSI 5Gi | **NFS** | config/identity files. rsync-copy. |
| `victorialogs` | iSCSI 50Gi | **local-path** (was NFS) | ⚠️ changed 2026-05-30: VL recommends local ext4, not NFS. mmap engine. → local xfs `/data`. See Stage 1 runbook "VL/VM tier deviation". |
| `victoriametrics` | iSCSI 50Gi | **local-path** (was NFS) | ⚠️ changed 2026-05-30: VM's only documented NFS panic ([#61](https://github.com/VictoriaMetrics/VictoriaMetrics/issues/61)) is on Synology-over-NFS = our hardware. mmap engine. retention 6mo→1mo committed. See Stage 1 runbook. |
| `authentik-redis` | ✅ emptyDir | **emptyDir** | session cache — rebuilds. Done 2026-05-30 (LUN dangling, see below). |
| `netbox-valkey` | ✅ emptyDir | **emptyDir** | cache — rebuilds. Done 2026-05-30 (LUN dangling, see below). |

**End state: Synology iSCSI 10 → ~1 LUN** (garage-meta), ~9 free. NFS absorbs unlimited file-class workloads (jotunheim, Immich, future) without ever touching the cap.

---

## Volume layout (two volumes for the whole homelab, excluding media)

Single RAID1 pool on the DS223j → volume separation is **logical only** (no IO isolation; all volumes share the same two spindles). The layout is organisational, not a performance boundary. Protocol-mixing on a volume (NFS share + iSCSI LUN coexisting) is therefore fine — the enterprise "separate protocols onto separate aggregates/tiers" reflex buys nothing on a single-pool unit, and the LUN cap is DSM-wide regardless of volume.

| Volume | Size | Purpose | Notes |
|--------|------|---------|-------|
| **Volume1** | recreate ~125 GB | backup — PBS datastore + Hyperbackup (all NFS) | currently oversized + still holds the live iSCSI LUNs; shrink is the LAST step (Stage 3), after every LUN is offloaded |
| **Volume2** | 100 GB (~95 effective), expandable | **all asgard k8s storage** — `k8s-nfs` share (all NFS PVCs) + the one surviving iSCSI LUN (garage-meta) | the unified k8s volume; watch free space before the VL/VM/garage-data NFS copies, expand if tight |
| **Volume3** | — | **PBS staging only** (transient) — holds the PBS datastore during the Volume1 recreate, then retired | not a k8s volume; drops out once Stage 3 completes |
| *(media)* | 2 TB | media (arr-stack) | deferred; own volume, created last |

- ✅ `k8s-nfs` already lives on **Volume2** — the "move it off Volume1 first" risk is **resolved**; no double-move.
- **Long-term:** when jotunheim lands, give it its **own k8s volume** mirroring asgard's shape (NFS-primary, iSCSI only when block semantics are load-bearing). Keeps the cluster failure-domain reflected at the storage layer too.

---

## Prerequisites

- ✅ **csi-driver-nfs** deployed (4.13.2), `nfs-client` SC, `k8s-nfs` share validated.
- ✅ **iSCSI vol2 SC** + VM retention 6mo→1mo committed (commit `62c300b`).
- ✅ **local-path-provisioner** (2026-05-30) — Rancher v0.0.36 via Flux, SC `local-path`. Backed by a dedicated 50G `scsi1` data disk per worker (xfs, mounted `/data` with a blanket `context=container_file_t` SELinux mount option; ssd+discard+fstrim). All three workers done + reboot-validated; Vault Class-L tier unblocked. See CLAUDE.md "local-path-provisioner" gotchas.
- ✅ **`k8s-nfs` final home decided** — Volume2 (its own volume, separate from the backup Volume1). No double-move.

---

## Migration classes

`reclaimPolicy: Retain` everywhere → deleting a PVC leaves the old PV/LUN intact for rollback. StatefulSet `volumeClaimTemplates` are **immutable** → changing SC requires deleting the STS (Flux recreates).

### Class N — iSCSI → NFS (preserve data)
For garage-data + teamspeak. (VL/VM were Class N in the original table but moved to local-path 2026-05-30 — see Stage 1 runbook "VL/VM tier deviation".) Cross-StorageClass copy.
```
# 1. Quiesce (scale dependents first: Outline→0 before Garage→0). Suspend Flux for the HR.
# 2. Pre-create the target PVC on nfs-client (right-sized).
# 3. Migrator pod mounts old (iSCSI, ro) + new (NFS); rsync -aHAX --numeric-ids; verify du.
# 4. Static-rebind the NFS PV under the STS-expected PVC name (clear claimRef, recreate PVC with volumeName).
# 5. Edit HR: storageClassName → nfs-client (+ size, + VM retention). Delete STS → Flux recreates → adopts rebound PVC.
# 6. Resume + verify app reads its data. Old iSCSI LUN stays Retained until cutover.
```

### Class L — iSCSI → local-path (Vault, Raft re-sync, no copy)
Per-node, keep 2/3 quorum, leader last. Each node's storage recreated empty on local-path; Raft re-syncs from leader.
```
# 0. local-path-provisioner is deployed (2026-05-30) — confirm SC `local-path` present + a worker /data mount.
# 1. Edit vault HR: storageClass → local-path. Delete STS --cascade=orphan (pods keep running).
# 2. For vault-2 → vault-1 → vault-0 (leader LAST, step-down first):
#    delete pod + delete pvc data-vault-<N> + delete old PV → STS recreates PVC on local-path
#    → KMS auto-unseal → raft join + resync. WAIT for raft list-peers (3 healthy) before next node.
```

### Class E — iSCSI → emptyDir (Redis, Valkey)
```
# Edit the StatefulSet/HR: replace the PVC volume with emptyDir. Delete STS → recreate. Cache rebuilds.
# Delete old iSCSI LUN at cutover.
```

### Class M — iSCSI right-size on Volume2 (garage-meta)
Garage-meta stays block but moves to Volume2. Use the rsync static-rebind onto `synology-csi-iscsi-retain-vol2` (keep 10 Gi — it's LMDB). Needs a free LUN slot — freed once the NFS/local-path/emptyDir migrations vacate LUNs.

---

## Stages

### Stage 0 — Prep
- ✅ NFS tier validated; vol2 SC + VM retention committed.
- ✅ `k8s-nfs` final volume = Volume2; `nfs-client` SC corrected `/volume1`→`/volume2` + smoke-tested bound on Volume2 (commit `cf00ad0`, 2026-05-30). SC `parameters` are immutable → applied via delete-SC + Flux recreate (safe: 0 NFS PVCs bound).
- ✅ Deploy local-path-provisioner (2026-05-30) — Rancher v0.0.36 via Flux (`WaitForFirstConsumer`), `nodePathMap → /data`. Dedicated 50G `scsi1` data disk per worker (xfs, blanket `context=container_file_t` mount, ssd+discard+fstrim) via `ansible/roles/local-path-disk`. Rolled out urd (canary) → verd → skuld, each reboot-validated (Vault self-healed 3/3 every time); end-to-end PVC bind+write confirmed. Vault Class-L step now unblocked.
- 🟡 **local-path SC `reclaimPolicy: Delete → Retain`** (committed `9bc2c51`, **apply pending**) — a released PVC must not wipe node-local data now that the tier backs single-replica VL/VM. **Apply = `kubectl delete sc local-path` + Flux/Helm recreate** (reclaimPolicy is immutable); **zero-risk while 0 local-path PVCs are bound** (true as of 2026-05-30). Must land before the VL/VM local-path migrations. Tradeoff: orphan `/data` dirs after a PVC delete → occasional manual sweep per worker.
- ⛔ ~~PBS baseline restore test~~ — out of scope (2026-05-30): PBS restore confirmed working out-of-band. Stage 3's own post-recreate restore-verify still applies.

### Stage 1 — Vacate LUNs (workload migrations, lowest-stakes first)
Order: **emptyDir** (redis, valkey — frees 2 LUNs, trivial) → **Class N** NFS (teamspeak → VL → VM → garage-data) → **Class L** Vault (needs local-path-provisioner). Each frees LUN(s); verify before deleting old LUNs.

- ✅ **emptyDir caches done 2026-05-30** (commit `ee12557`) — **cap freed 10/10 → 8/10**: `authentik-redis` (drop `volumeClaimTemplates` + the iSCSI-only `redis-data-chown` init → emptyDir volume) and `netbox-valkey` (`primary.persistence.enabled: false` + drop `volumePermissions`). `volumeClaimTemplates` is immutable → deleted both STSs so Flux/Helm recreated them on emptyDir (netbox HR rolled back to v7 first on the immutable-field error, then upgraded clean to v12 after the valkey STS delete). Both verified healthy (authentik `/-/health/ready/` 200, netbox `/login/` 200). Cutover: orphaned PVCs + PVs deleted in k8s (8 synology PVs), the 2 backing LUNs (`pvc-36dea395…` 1Gi authentik-redis, `pvc-deb42887…` 2Gi netbox-valkey) deleted in DSM SAN Manager by the operator, all 3 workers confirmed free of stale iSCSI node records/sessions for the deleted handles. **Method-of-record for Class E**: edit STS/HR → emptyDir, `kubectl delete sts` (immutable VCT) so Flux/Helm recreate, verify app health, then PVC+PV delete + DSM LUN delete.

### Stage 2 — garage-meta → Volume2 (Class M)
Once slots are free, move the one remaining block LUN to the right-sized Volume2. Delete the old Volume1 garage-meta LUN.

### Stage 3 — Volume1 → backup (shrink)
Synology can't shrink in place → delete+recreate. `k8s-nfs` is already off Volume1 (on Volume2), so no share-relocation needed. **Gate: all 10 iSCSI LUNs must be offloaded + verified first** (Stages 1–2) — the live LUNs sit on Volume1; recreating it before they're vacated destroys live data. Then: relocate the PBS datastore → **Volume3 staging** (online folder "location" change — long-running, safe to start any time) → delete Volume1 → recreate ~125 GB → restore PBS → verify restore → retire Volume3. (Operator opted for the full shrink.)

### Stage 4 — Cleanup + docs
- Delete all old Volume1 iSCSI LUNs (verify each workload healthy first).
- Drop the default StorageClass (explicit per-workload SC naming).
- Docs: CLAUDE.md storage invariant → tiering model; architecture/hardware NFS tier; decisions row.

---

## Stage 1 execution runbook — Class N + local-path (prepped 2026-05-30)

*Detailed, command-level runbook for the four remaining Stage-1 workload migrations, prepped read-only against live state on 2026-05-30. Nothing here has been applied. Drive one workload at a time, verify, then reclaim its LUN.*

### Decisions (this is the deviation-from-table part — read first)

| Workload | Source | → Target | Right-size | Preserve? | Notes |
|----------|--------|----------|-----------|-----------|-------|
| `teamspeak` | iSCSI 5Gi | **nfs-client** | 1Gi | yes (trivial) | hand-rolled STS `k8s/asgard/apps/teamspeak/statefulset.yaml`. `/var/ts3server` = file-transfer blobs + logs + `query_ip_*.txt`; **no SQLite** (PG backend) → NFS-safe. |
| `victorialogs` | iSCSI 50Gi | **local-path** (was NFS in table) | 20Gi | **fresh-start recommended** | HR `server.persistentVolume`. **NOT NFS** — see flag below. Node-pin **einherjar-urd**. |
| `victoriametrics` | iSCSI 100Gi (PVC 50Gi) | **local-path** (was NFS in table) | 20Gi | **fresh-start recommended** | HR `persistentVolume`, retention already 1mo (`62c300b`). **NOT NFS**. Node-pin **einherjar-verd** (balance; avoid skuld/16GB). |
| `garage` **data only** | iSCSI 200Gi | **nfs-client** | 50Gi | yes (near-empty) | hand-rolled STS, **two** VCTs — change `data` only; `meta` (10Gi LMDB) stays iSCSI → Stage 2. Quiesce **Outline → 0 first**. |

**VL/VM tier deviation (operator-approved 2026-05-30):** the per-workload table assigned VL/VM → NFS; changed to **local-path**. VictoriaLogs never endorsed NFS (recommends local ext4); VictoriaMetrics' only documented NFS panic ([issue #61](https://github.com/VictoriaMetrics/VictoriaMetrics/issues/61), `unlinkat: directory not empty` during part-merge) is **on a Synology NAS over NFS — our exact hardware**; both mmap by default. local-path = local xfs `/data` (mmap-safe, VM/VL-recommended, 0 LUN cost, faster). Tradeoff: node-pinned (data on one worker's `scsi1` disk — survives a VM rebuild, lost only on physical-node loss; acceptable for single-replica non-critical observability).

**Sizing is advisory, not reserved.** `nfs-client` (csi-driver-nfs) and Rancher `local-path` don't hard-enforce the PVC `size` — it's a request, not a quota. Real limits: NFS shares **Volume2 (~95 GiB)** across all NFS PVCs; local-path shares each worker's **50 GiB `/data`** with Vault (post-Class-L) + co-located VL/VM. At current data (<1 GiB each) there's no pressure; pin VL→urd, VM→verd so they don't stack on one disk.

**Preserve vs fresh for VL/VM:** the local-path SC is now `reclaimPolicy: Retain` (changed 2026-05-30, commit `9bc2c51` — releasing a PVC must not wipe node-local data), so a preserve-rsync is the **same clean static-rebind as NFS** — no special reclaim handling. Given the data is ~0.14 GiB (VL) / ~0.77 GiB (VM) of **refillable** short-term observability (Zabbix owns long-term trends), **fresh-start is still recommended** — simpler, loses only the current 30d/1mo window — but preserve is now equally clean if wanted.

### Ordering & rollback

- **Order:** teamspeak (standalone, trivial) → VL → VM (observability; shippers buffer during the gap) → garage-data (Outline dependency, biggest, last).
- **Rollback net:** every source iSCSI PV is `Retain`. Each migration deletes the source PVC → PV goes `Released`, LUN intact. **Delete the LUN in DSM SAN Manager only after the app is verified healthy on the new tier** (operator-manual, same as the cache step). Until then, rollback = re-point the STS/HR SC back to iSCSI + rebind the old PV.
- **Pre-flight:** `csi-nfs-node` pods showed recent restarts (worker reboots from the local-path rollout) — confirm all `Running` + stable before starting. Re-verify `VAULT_TOKEN` if the session is long.

### Shared mechanics

**Quiesce** (so the source PV detaches + Flux doesn't fight the scale-down):
- HR-managed (VL, VM): `flux suspend hr <name> -n monitoring` → `kubectl scale sts <name> -n monitoring --replicas 0`.
- Kustomization-managed hand-rolled STS (teamspeak=`apps`, garage=`infrastructure`): `flux suspend kustomization <ks>` (broad but brief — other resources just pause reconcile) → `kubectl scale sts <name> -n <ns> --replicas 0`. For garage: scale **Outline → 0 first**.

**Migrator pod** (mounts old source ro + new target rw; alpine, rsync). NFS target → runs on any node. local-path target → must set `nodeName: <pinned-worker>` (the migrator is the *first consumer* that provisions the node-local PV; that node is then the workload's permanent home). Keep this spec out of `k8s/` (it's transient ops tooling, not Flux-managed) — apply from a local file, delete after.

```yaml
# migrator.yaml — APPLY MANUALLY, NOT via Flux. Delete after rsync+verify.
apiVersion: v1
kind: Pod
metadata: { name: migrator, namespace: <ns> }
spec:
  # nodeName: <worker>        # local-path targets ONLY (pins the workload's home)
  restartPolicy: Never
  containers:
    - name: rsync
      image: alpine:3.20
      command: ["sh","-c","apk add --no-cache rsync && rsync -aHAX --numeric-ids --info=progress2 /src/ /dst/ && echo '--- du src/dst ---' && du -sh /src /dst && sleep 3600"]
      volumeMounts:
        - { name: src, mountPath: /src, readOnly: true }
        - { name: dst, mountPath: /dst }
  volumes:
    - name: src
      persistentVolumeClaim: { claimName: <OLD-pvc-name>, readOnly: true }
    - name: dst
      persistentVolumeClaim: { claimName: <NEW-pvc-name> }
```

**Static-rebind (NFS / Retain target):** migrator creates target PV via a temp PVC → after rsync, `kubectl delete pvc <temp>` (PV → Released, Retain) → `kubectl patch pv <newPV> --type=json -p '[{"op":"remove","path":"/spec/claimRef"}]'` → recreate PVC under the **STS-expected name** with `spec.volumeName: <newPV>` → binds. Then edit the STS VCT SC/size, `kubectl delete sts` (immutable VCT), resume Flux → STS recreates + adopts the pre-bound PVC.

**Static-rebind (local-path / Retain target):** identical to the NFS path above — the local-path SC is now `Retain` (commit `9bc2c51`), so deleting the temp PVC leaves its PV `Released` with the host dir intact; clear `claimRef` and recreate under the STS-expected name with `volumeName`. The PV's node-local `nodeAffinity` pins the recreated STS pod to that worker. (No reclaim-patch needed — that workaround existed only while the SC was `Delete`.) **Prereq:** the SC must already be flipped to Retain (delete+recreate, immutable) before any local-path migration — see Stage 0.

### Per-workload

**1. teamspeak → nfs-client (preserve)**
```
flux suspend kustomization apps
kubectl scale sts teamspeak -n teamspeak --replicas 0     # clients drop; reconnect after
# migrator (no nodeName): OLD=data-teamspeak-0, NEW=temp ts-nfs (create ts-nfs PVC on nfs-client, 1Gi)
# rsync, verify du, delete migrator
# static-rebind (NFS): delete ts-nfs → clear claimRef → recreate data-teamspeak-0 volumeName=<nfsPV>
# edit statefulset.yaml: storageClassName nfs-client, storage 1Gi; kubectl delete sts teamspeak -n teamspeak
flux resume kustomization apps    # Flux recreates STS, adopts NFS PVC
# verify: pod Ready, TS3 connects, files/ present. Then DSM-delete old LUN.
```
Edit: `k8s/asgard/apps/teamspeak/statefulset.yaml` VCT `data` → `storageClassName: nfs-client`, `storage: 1Gi`.

**2. VL → local-path (fresh recommended)**
```
flux suspend hr victorialogs -n monitoring
kubectl scale sts victoria-logs-single-server -n monitoring --replicas 0
kubectl delete pvc server-volume-victorialogs-victoria-logs-single-server-0 -n monitoring   # iSCSI PV → Released/Retain (rollback net)
kubectl delete sts victoria-logs-single-server -n monitoring
# edit HR: server.persistentVolume.storageClass → local-path, size → 20Gi; (to pin: add nodeSelector for einherjar-urd)
flux resume hr victorialogs -n monitoring     # recreate → empty local-path PVC provisions on scheduled node
# verify: pod Ready on the pinned worker, /insert + vmui reachable, ingest resumes. DSM-delete old LUN.
```
*Preserve variant:* migrator with `nodeName: einherjar-urd`, OLD=the iSCSI PVC, NEW=temp local-path PVC; rsync; then local-path static-rebind (patch reclaim Retain first). Edit: `k8s/asgard/apps/victorialogs/helmrelease.yaml`.

**3. VM → local-path (fresh recommended)** — identical to VL, pin **einherjar-verd**, STS `victoria-metrics-single-server`, PVC `server-volume-victoriametrics-victoria-metrics-single-server-0`, HR size → 20Gi. vmagent buffers metrics during the gap. Edit: `k8s/asgard/apps/victoriametrics/helmrelease.yaml`.

**4. garage data → nfs-client (preserve; meta stays iSCSI)**
```
kubectl scale deploy outline -n outline --replicas 0      # dependent first
flux suspend kustomization infrastructure
kubectl scale sts garage -n garage --replicas 0           # detaches data+meta
# migrator (no nodeName): OLD=data-garage-0, NEW=temp garage-data-nfs (nfs-client, 50Gi); rsync; verify du
# static-rebind (NFS): delete temp → clear claimRef → recreate data-garage-0 volumeName=<nfsPV>
# edit garage statefulset.yaml: DATA VCT only → storageClassName nfs-client, storage 50Gi (leave meta VCT on iSCSI)
kubectl delete sts garage -n garage
flux resume kustomization infrastructure                  # recreate, adopt NFS data PVC + existing iSCSI meta PVC
kubectl scale deploy outline -n outline --replicas 1
# verify: garage healthy, Outline reads documents/attachments from S3. DSM-delete old data LUN (NOT meta).
```
Edit: `k8s/asgard/infrastructure/garage/statefulset.yaml` — **`data` VCT only**.

**End of Stage 1:** iSCSI cap `8/10 → 4/10` (teamspeak, VL, VM, garage-data LUNs freed after DSM delete; remaining: 3× Vault + garage-meta). Then Class L (Vault → local-path) and Stage 2 (garage-meta → vol2).

---

## Risk register

- **Vault quorum** — never replace two nodes at once; verify `raft list-peers` (3) between nodes; leader last; KMS auto-unseal must work (test vault-2 first).
- **Garage identity** — layout/node-id is in `meta` (staying iSCSI). The `data` LUN (→NFS) is just blocks; copy + verify Outline reads documents before deleting old `data`.
- **mmap on NFS = corruption** — Garage-meta (LMDB) and Vault (BoltDB) must NOT go on NFS. iSCSI / local-path respectively. Non-negotiable.
- **local-path = node-pinned, no per-PVC HA** — fine for Vault (Raft gives HA at the app layer); do NOT use it for single-instance critical data without app replication.
- ~~**NFS share home vs Volume1 shrink**~~ — **resolved**: `k8s-nfs` is on Volume2, not Volume1, so the Volume1 recreate never touches it. No double-move.
- **PBS** — old datastore is the only backup copy until Stage 3's own post-recreate restore-verify confirms the new one. (Stage-0 baseline restore test dropped — restore confirmed working out-of-band 2026-05-30; the in-line Stage-3 verify is non-negotiable regardless.)
- **No K8s PVC backup** — the iSCSI/NFS PVC data isn't in PBS; the Retain-based rollback is the net. Verify-before-delete is mandatory.

---

## Progress

- ✅ NFS tier (csi-driver-nfs 4.13.2) deployed + smoke-tested clean (no shared-folder pollution, 0 LUNs).
- ✅ `vol2` iSCSI SC + VM retention 6mo→1mo committed.
- ⏸️ **Paused here** — workload migrations + Volume1 shrink to resume in a fresh session (data-movement-heavy).
- ✅ `nfs-client` SC pointed at Volume2 + smoke-tested; Stage-0 PBS baseline test dropped (out of scope).
- ✅ local-path-provisioner + per-worker `/data` disks deployed (2026-05-30).
- ✅ Stage 1 emptyDir caches (`authentik-redis` + `netbox-valkey`) migrated + verified + LUNs reclaimed (2026-05-30, commit `ee12557`). k8s 8 synology PVs; 2 backing LUNs deleted in DSM by operator → **cap 10/10 → 8/10**; workers free of stale iSCSI records.
- 🔲 Next on resume: Stage 1 cont. — **Class N** NFS (teamspeak → VL → VM → garage-data), then **Class L** Vault onto local-path.
