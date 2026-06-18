<!-- docs/outline/components-and-interactions/storage-and-data.md -->

# Storage & data

This subpage covers where state lives. Storage is **tiered by access pattern** — block, node-local, file, object, and cache each have a home — plus one relational database cluster and one backup store. Physical drives + the Synology unit itself live in the **Hardware** section; this page is about the software stack on top.

The principle that shapes every choice on this page: **state goes on the tier its access pattern needs, not on one storage type for everything.** Memory-mapped databases (LMDB, BoltDB) need real block storage and corrupt on NFS. Replicated/quorum state wants fast node-local disk with HA solved at the app layer. Large append-heavy files are fine on NFS. Pure caches rebuild on restart and need no persistence at all. Matching each workload to its tier is what keeps the single small NAS from being a bottleneck — or a hard limit.

---

## The storage tiers

| Tier | StorageClass / mechanism | Backed by | Use for |
|------|--------------------------|-----------|---------|
| **Local LVM-thin** | Proxmox storage | per-node NVMe | VM + LXC disks |
| **iSCSI** | `synology-csi-iscsi-retain-vol2` | Synology LUN on Volume2 | block-critical single-instance (mmap/fsync DBs) |
| **local-path** | `local-path` | per-worker 50 GB `/data` xfs disk | app-replicated / quorum state (Raft) + mmap-safe single-instance |
| **NFS** | `nfs-client` (csi-driver-nfs) | Synology `k8s-nfs` share on Volume2 | file-class: large/append, fsync-tolerant |
| **emptyDir** | pod ephemeral | — | pure cache (rebuilds on restart) |

The default StorageClass posture is shifting toward **explicit per-workload naming** — no workload should land on a tier by accident.

---

## Local LVM-thin — VM and LXC disks

Every VM and LXC has its disk on the **local LVM-thin pool** of the Proxmox node it runs on. Per-node, no replication, no shared storage.

- **Why not NFS for VM disks:** fsync latency over 1 GbE is the wrong shape for any write-heavy workload — Postgres, etcd, the K3s control plane. Local NVMe gets sub-millisecond fsync; NFS-backed disks would not.
- **Per-node means single-node failure loses its disks.** Mitigation is at the application layer (Patroni replicates Postgres, K3s reschedules pods to surviving nodes) and at the backup layer (PBS).
- **Sizing:** Proxmox root LV is 20 GB on every node; the rest of the NVMe is LVM-thin storage. Per-node disk size lives in the Hardware section.

---

## iSCSI — block-critical single-instance only

The Synology NAS (Munin) is the iSCSI target via the **Synology CSI driver** (one instance per cluster). The StorageClass is `synology-csi-iscsi-retain-vol2` (LUN on Volume2), `Retain` reclaim — a released PVC leaves its LUN on the NAS for the operator to delete deliberately.

iSCSI is now reserved for the **one** thing that genuinely needs block: **memory-mapped databases**. Today that's just Garage's metadata store (LMDB). mmap over NFS corrupts; iSCSI (or node-local xfs) is the only safe home.

### Per-PVC LUN, and the DS223J cap

Synology CSI mints **one iSCSI target + LUN per PVC** — single-attach (`RWO`), no `RWX`. That's the constraint that drove the tiering: the DS223J has a **DSM-wide cap of ~10 LUNs total** (not per-Volume — a hard model limit on the 1 GB-RAM ARM unit). Putting every K8s volume on iSCSI hit that wall. The fix was to stop using iSCSI for everything and tier by access pattern — so iSCSI now carries a single LUN, with ~9 slots free for any future genuinely-block workload. File-class goes to NFS (no LUN cost), replicated state to local-path, caches to emptyDir.

---

## local-path — node-local for replicated + mmap-safe state

Rancher `local-path-provisioner` provides the `local-path` StorageClass (`WaitForFirstConsumer`, `Retain`), backed by a **dedicated 50 GB `/data` xfs disk on each worker** (separate from the OS disk, so it survives a VM rebuild). It's node-pinned: the PV lives on one worker and the pod is pinned there.

Two kinds of workload live here:

- **App-replicated / quorum state** — **Vault** (3-node Raft) is the model. HA is solved at the *app* layer (Raft re-syncs a wiped node from its peers), so node-local storage with no storage-layer replication is exactly right — lower latency, zero LUN cost.
- **mmap-safe single-instance** — **VictoriaLogs** and **VictoriaMetrics**. Both use memory-mapped engines and explicitly want local ext4/xfs over NFS. They're single-instance and node-pinned; availability is *recovery* (PBS backs up the worker `/data` disk daily), not HA. Acceptable because the data is refillable short-term observability — Zabbix backstops long-term trends.

The rule: only app-replicated data (Vault) or downtime-tolerant/refillable data (observability) goes on local-path single-instance. Availability-critical single-instance data needs app-level replication, never storage-layer tricks.

---

## NFS — file-class K8s volumes

`csi-driver-nfs` provides the `nfs-client` StorageClass, backed by **one** Synology share (`k8s-nfs` on Volume2) with a `pvc-<uuid>` subdir per PV — no shared-folder-per-PV pollution. NFS is now a first-class K8s tier for **file-class** state: large or append-heavy, fsync-tolerant, fine over 1 GbE.

- **Teamspeak** — file-transfer blobs, logs, identity files (no SQLite; PG-backed).
- **Garage data** — S3 object blocks (content-addressed, write-once, large). Stays on the NAS RAID1, so no redundancy downgrade vs the old iSCSI placement.

NFS has no LUN cost, so it absorbs unlimited file-class workloads (future Immich, jotunheim) without ever touching the cap.

---

## Garage — S3 object store

A single-node Garage instance (`garage` namespace in asgard K3s) provides S3-compatible object storage in-cluster.

- **Topology:** one replica, RF=1, "dangerous consistency" mode (acceptable for the homelab; not a multi-region setup).
- **Persistence is split by access pattern:** **metadata** (LMDB, mmap) on a 10 GiB **iSCSI** PVC — must stay block; **data** (object blocks) on a 50 GiB **NFS** PVC. The cluster identity (node key + layout) lives in metadata, so that LUN is the one piece of Garage that can never go on NFS.
- **Layout-init Job** runs once on first deploy via an alpine+curl+jq sidecar against Garage's admin API v2 — the Garage image itself is `FROM scratch` with no shell.
- **Admin API is ClusterIP-only by design.** No external LoadBalancer for the admin endpoint; operator workflow is `kubectl port-forward` from the operator workstation when Terraform needs to talk to it. The S3 endpoint itself is reachable in-cluster only.

### Bucket lifecycle

A new consumer (e.g. Outline) lands like this:

1. Operator declares the bucket + access key in `terraform/garage/` (using the `jkossis/garage` provider).
2. Terraform mints the bucket, the access key, the grant, and writes the resulting credentials to Vault at `secret/k8s/<consumer>/s3`.
3. The consumer's `ExternalSecret` materializes the Vault path into a K8s Secret.
4. The consumer reads the Secret and connects to the in-cluster S3 endpoint.

The first consumer is Outline (page attachments + uploads). Future Immich + backup targets follow the same pattern.

---

## PostgreSQL — relational state

Three-node Patroni-managed cluster (Fulla / Vör / Idunn) on dedicated LXCs. Not VMs, not in K3s.

### Why LXCs

- **Faster fsync.** LXC + local LVM-thin = same kernel, same block layer, sub-millisecond fsync. K8s + iSCSI would add a kube-proxy hop, an iSCSI session, and an extra fsync layer — measurably worse for write-heavy DB workloads.
- **Independent failure domain.** Postgres outage shouldn't be entangled with cluster outage. LXC + dedicated trio = Postgres survives K3s and vice versa.

### Topology

- **PG nodes:** Fulla / Vör / Idunn on LXCs 1130/1131/1132, IPs `10.0.11.230/231/232`. Patroni runs on each; etcd does not (see below).
- **etcd DCS + HAProxy:** a separate trio (Hlin / Eir / Snotra) on LXCs 1133/1134/1135. etcd is the consensus store Patroni uses for leader election; co-locating etcd with Postgres would couple their failure domains.
- **HAProxy VIP** at `10.0.10.210`. keepalived floats the VIP across the HAProxy trio. Every PG consumer (web apps, Ansible, ops scripts) talks to the VIP — nobody addresses a specific node directly.
- **No pgbouncer** in the connection chain. Connection counts are small enough that the leader handles them directly. Triggers for adding pgbouncer (sustained `max_connections` pressure, fan-in from many short-lived consumers) are documented; not at scale yet.

### Routing

HAProxy uses `option httpchk` against Patroni's REST API `/master` endpoint. Only the current leader returns HTTP 200 on `/master`; replicas return 503. HAProxy combines this with `balance first` so it only ever has one UP backend — the leader — and routing is deterministic.

`on-marked-down shutdown-sessions` is set so that when a leader is demoted, clients are forced to reconnect rather than carrying a stale session over a now-replica. Combined with the Patroni Hermod callback (failover → Discord notification), the operator sees the transition within seconds.

### Security

- **TLS required.** All connections are `hostssl` only. Plaintext is rejected with the same error class as a disallowed CIDR — `sslmode=require` is mandatory on every client.
- **scram-sha-256** for passwords. No `md5`.
- **Per-app DB + user**, never the postgres superuser. App credentials are minted by Terraform in `secret/ansible/postgres/<app>-password`; ESO publishes the consumer's slice to K8s.

---

## PBS — backups

Proxmox Backup Server runs in a privileged LXC on Skuld (LXC 1101). The datastore is an NFS share from Munin, on the dedicated **backup volume (Volume1)** — kept separate from the all-K8s Volume2 so the two never contend.

- **Backup target:** every VM and LXC in `niflheim` — including the K3s worker VMs, which now carry the local-path `/data` disks (Vault Raft, VL, VM). That makes PBS the *recovery* story for the node-pinned local-path tier.
- **DR posture:** not a true off-site story. Single-site failure (the whole homelab room) loses both Proxmox storage and the PBS datastore. Off-site replication is on the roadmap (likely a Garage-backed mirror, since the bucket-shaped pattern reuses the existing object-store layer).

### Volume layout on the NAS

The DS223J carves its single RAID1 pool into two logical volumes:

- **Volume1 — backup.** The PBS datastore (NFS). Dedicated; holds no live K8s storage.
- **Volume2 — all K8s.** The `k8s-nfs` share (every NFS PVC) plus the one surviving iSCSI LUN (Garage metadata).

Volume separation is organisational, not a performance boundary — both share the same two spindles. The split keeps the backup store from contending with live K8s I/O and mirrors the failure-domain thinking elsewhere.

---

## Failure surfaces worth knowing

- **Postgres leader failure.** Patroni promotes a replica; HAProxy's REST health-check flips routing within seconds. Clients reconnect. Hermod fires a `patroni` tag to Discord.
- **A worker dies.** Pods reschedule, but **local-path data is node-pinned** — a workload whose `/data` lives on the dead worker is down until that VM/node is restored. Vault rides this out (Raft quorum on the surviving two); VL/VM are single-instance and simply wait for the node. Recovery of the disk itself is PBS (daily worker-VM backup including the `/data` disk).
- **Synology unavailable.** Every iSCSI + NFS PVC stalls (Garage, teamspeak). The K3s control plane stays up (VM-local disks), and **local-path workloads (Vault, VL, VM) are unaffected** — their data is on the workers, not the NAS. PG continues (local LVM-thin). PBS is down (NFS datastore on the NAS). Pods recover when Munin returns. **Note:** a NAS reboot bounces the one iSCSI target — quiesce Garage first (clean session logout) to avoid an ext4-RO journal-abort on the metadata LUN.
- **Garage data corruption.** RF=1 means no built-in redundancy. The data blocks are on NAS RAID1 + PBS; the identity-bearing metadata LUN is the piece to guard, and it's the one kept on block storage for exactly that reason.

---

## See also

- **Hardware** section — drive specifications, NAS hardware + volume layout, per-worker `/data` disks, NVMe tier comparison.
- **Network** (this section) — Postgres HAProxy VIP routing, source-based policy routing for VRRP-bearing LXCs.
- **Identity & secrets** (this section) — Vault on local-path (Raft), Vault paths for Postgres credentials, Garage admin token, S3 access keys.
- **Troubleshooting** — iSCSI orphan sessions, DS223J LUN cap, RHEL e2fsprogs limits for Synology-formatted volumes, cross-tier migration (rsync static-rebind).
