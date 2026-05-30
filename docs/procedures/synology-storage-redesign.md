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
| **NFS** | `nfs-client` | Synology share `/volume1/k8s-nfs` via `csi-driver-nfs` | file-class: append/large-file, fsync-tolerant | **0** |
| **local-path** | `local-path` *(provisioner TBD)* | Proxmox VM local disk | app-replicated state (Raft/quorum) | **0** |
| **iSCSI** | `synology-csi-iscsi-retain-vol2` | Synology LUN on Volume2 | block-critical single-instance: mmap/fsync (LMDB, BoltDB) | **1 each** |
| **emptyDir** | — | pod ephemeral | pure cache (rebuilds on restart) | **0** |

- **NFS** is live + validated (csi-driver-nfs 4.13.2, one `pvc-<uuid>` subdir per PV inside the single `k8s-nfs` share — **no shared-folder-per-PV pollution**; that's synology-csi's NFS mode, deliberately not used).
- **local-path** needs a provisioner deployed — K3s's built-in `local-storage` is **disabled** in our CP config, so deploy Rancher `local-path-provisioner` (or static `local` PVs). Prerequisite for the Vault tier.
- **iSCSI** stays only where block semantics are load-bearing (memory-mapped DBs hate NFS).

---

## Per-workload tier assignment

| Workload | Now | → Tier | Why / method |
|----------|-----|--------|--------------|
| `vault` ×3 | iSCSI 5Gi | **local-path** | Raft replicates across 3 nodes → node-local is ideal + lower latency; frees 3 LUNs. Per-node recreate, Raft re-syncs. |
| `garage/data` | iSCSI 200Gi | **NFS** | S3 object blocks = large files, NFS-fine. rsync-copy. |
| `garage/meta` | iSCSI 10Gi | **iSCSI (vol2)** | LMDB (mmap) — MUST stay block. Right-size + move to Volume2. |
| `teamspeak` | iSCSI 5Gi | **NFS** | config/identity files. rsync-copy. |
| `victorialogs` | iSCSI 50Gi | **NFS** | append-mostly logs, tolerate NFS. rsync-copy (preserve, operator chose). |
| `victoriametrics` | iSCSI 50Gi | **NFS** | metrics, tolerate NFS. rsync-copy + retention 6mo→1mo (already committed). |
| `authentik-redis` | iSCSI 1Gi | **emptyDir** | session cache — rebuilds. |
| `netbox-valkey` | iSCSI 2Gi | **emptyDir** | cache — rebuilds. |

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
For garage-data, teamspeak, VL, VM. Cross-StorageClass copy.
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
# 0. Deploy local-path-provisioner first.
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
- ✅ Deploy local-path-provisioner (2026-05-30) — Rancher v0.0.36 via Flux (`WaitForFirstConsumer`, `Delete` reclaim), `nodePathMap → /data`. Dedicated 50G `scsi1` data disk per worker (xfs, blanket `context=container_file_t` mount, ssd+discard+fstrim) via `ansible/roles/local-path-disk`. Rolled out urd (canary) → verd → skuld, each reboot-validated (Vault self-healed 3/3 every time); end-to-end PVC bind+write confirmed. Vault Class-L step now unblocked.
- ⛔ ~~PBS baseline restore test~~ — out of scope (2026-05-30): PBS restore confirmed working out-of-band. Stage 3's own post-recreate restore-verify still applies.

### Stage 1 — Vacate LUNs (workload migrations, lowest-stakes first)
Order: **emptyDir** (redis, valkey — frees 2 LUNs, trivial) → **Class N** NFS (teamspeak → VL → VM → garage-data) → **Class L** Vault (needs local-path-provisioner). Each frees LUN(s); verify before deleting old LUNs.

### Stage 2 — garage-meta → Volume2 (Class M)
Once slots are free, move the one remaining block LUN to the right-sized Volume2. Delete the old Volume1 garage-meta LUN.

### Stage 3 — Volume1 → backup (shrink)
Synology can't shrink in place → delete+recreate. `k8s-nfs` is already off Volume1 (on Volume2), so no share-relocation needed. **Gate: all 10 iSCSI LUNs must be offloaded + verified first** (Stages 1–2) — the live LUNs sit on Volume1; recreating it before they're vacated destroys live data. Then: relocate the PBS datastore → **Volume3 staging** (online folder "location" change — long-running, safe to start any time) → delete Volume1 → recreate ~125 GB → restore PBS → verify restore → retire Volume3. (Operator opted for the full shrink.)

### Stage 4 — Cleanup + docs
- Delete all old Volume1 iSCSI LUNs (verify each workload healthy first).
- Drop the default StorageClass (explicit per-workload SC naming).
- Docs: CLAUDE.md storage invariant → tiering model; architecture/hardware NFS tier; decisions row.

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
- 🔲 Next on resume: Stage 1 (emptyDir caches → NFS file-class → Vault Class-L onto local-path).
