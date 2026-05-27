<!-- docs/outline/components-and-interactions/storage-and-data.md -->

# Storage & data

This subpage covers where state lives. Three tiers of storage (block, object, local), one relational database cluster, and one backup store. Physical drives + the Synology unit itself live in the **Hardware** section; this page is about the software stack on top.

The principle that shapes every choice on this page: state stays on storage that matches the workload's access pattern. Block for K8s persistent volumes that need single-attach POSIX semantics. Object for shared-access blob storage. Local LVM-thin for VM/LXC disks that need fsync latency the network can't provide.

---

## Local LVM-thin — VM and LXC disks

Every VM and LXC has its disk on the **local LVM-thin pool** of the Proxmox node it runs on. Per-node, no replication, no shared storage.

- **Why not NFS for VM disks:** fsync latency over 1 GbE is the wrong shape for any write-heavy workload — Postgres, etcd, the K3s control plane. Local NVMe gets sub-millisecond fsync; NFS-backed disks would not.
- **Per-node means single-node failure loses its disks.** Mitigation is at the application layer (Patroni replicates Postgres, K3s reschedules pods to surviving nodes) and at the backup layer (PBS).
- **Sizing:** Proxmox root LV is 20 GB on every node; the rest of the NVMe is LVM-thin storage. Per-node disk size lives in the Hardware section.

---

## Synology iSCSI — K8s persistent volumes

The Synology NAS (Munin) is the iSCSI target for every K8s persistent volume. The driver is the **Synology CSI driver** (christian-schlichtherle/synology-csi-chart); each cluster runs its own instance.

A single StorageClass, `synology-csi-iscsi-retain`, is the default. `Retain` reclaim policy means released PVCs leave their LUN on the NAS — operator deletes manually when ready, preventing accidental data loss.

### Per-PVC LUN

Each PVC gets its own iSCSI target + LUN on the NAS. There is no shared LUN. This shapes a few invariants:

- **Single-attach.** Only one node can hold the iSCSI session for a LUN at a time. K8s `RWO` semantics line up with this exactly.
- **No `RWX`.** Synology CSI is iSCSI-only, deliberately. An app that needs shared write across pods has two real options: split the writes (pod-local emptyDir for caches, persistent for canonical state) or use the object store instead.
- **Migration is operator-visible.** When a pod with a PVC moves nodes, iSCSI session cleanup on the source node matters — stale sessions are a recurring class. The Troubleshooting section covers the diagnostic.

### Why not NFS for K8s

Synology NFS shares pollute the DSM namespace — each share is a top-level Synology folder visible to anyone with NAS access. iSCSI LUNs are scoped to their target and don't leak into operator workflows. Combined with the fact that iSCSI gives stronger fsync semantics than NFS over 1 GbE, iSCSI is the right shape for K8s state.

### DS223J LUN cap

The DS223J has a per-Volume LUN cap that's lower than DSM spec sheets suggest. The trio currently sits at the cap (10 LUNs). New cache-class state (Redis, ephemeral queue state) defaults to `emptyDir` until the cap is lifted via DSM SAN Manager. This is a known constraint, not a recurring failure — it just changes the default for new workloads.

---

## Synology NFS — operator workflows + PBS

NFS shares on Munin exist for two purposes:

- **PBS datastore.** Proxmox Backup Server's LXC mounts an NFS share from Munin and uses it as the backup store. NFS fits because PBS writes are sequential, append-heavy, and tolerant of higher latency.
- **Ad-hoc operator file workflows.** Anywhere block-storage semantics are overkill (drop a file, pull a file, share a directory between hosts that aren't pods).

NFS is not a K8s StorageClass and isn't going to become one. iSCSI handles K8s.

---

## Garage — S3 object store

A single-node Garage instance (`garage` namespace in asgard K3s) provides S3-compatible object storage in-cluster.

- **Topology:** one replica, RF=1, "dangerous consistency" mode (acceptable for the homelab; not a multi-region setup).
- **Persistence:** 10 GiB metadata PVC + 200 GiB data PVC, both iSCSI-backed.
- **Layout-init Job** runs once on first deploy via an alpine+curl+jq sidecar against Garage's admin API v2 — the Garage image itself is `FROM scratch` with no shell.
- **Admin API is ClusterIP-only by design.** No external LoadBalancer for the admin endpoint; operator workflow is `kubectl port-forward` from the operator workstation when Terraform needs to talk to it. The S3 endpoint itself is reachable in-cluster only.

### Bucket lifecycle

A new consumer (e.g. Outline) lands like this:

1. Operator declares the bucket + access key in `terraform/garage/` (using the `jkossis/garage` provider).
2. Terraform mints the bucket, the access key, the grant, and writes the resulting credentials to Vault at `secret/k8s/<consumer>/s3`.
3. The consumer's `ExternalSecret` materializes the Vault path into a K8s Secret.
4. The consumer reads the Secret and connects to `http://garage-s3.garage.svc.cluster.local:3900` (in-cluster S3 endpoint).

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

Proxmox Backup Server runs in a privileged LXC on Skuld (LXC 1101). The datastore is an NFS share from Munin.

- **Backup target:** every VM and LXC in `niflheim`. K3s-side state lives on iSCSI PVCs, which Synology snapshots cover separately.
- **DR posture:** not a true off-site story. Single-site failure (the whole homelab room) loses both Proxmox storage and the PBS datastore. Off-site replication is on the roadmap (likely a Garage-backed mirror, since the bucket-shaped pattern reuses the existing object-store layer).

---

## Failure surfaces worth knowing

- **Postgres leader failure.** Patroni promotes a replica; HAProxy's REST health-check flips routing within seconds. Clients reconnect. Hermod fires a `patroni` tag to Discord.
- **Worker that holds an iSCSI session dies.** The iSCSI session times out; the pod's PVC eventually re-binds on a surviving worker. Time to recovery depends on the kubelet's iSCSI session-loss timeout. The Troubleshooting section has the diagnostic for orphan sessions.
- **Synology unavailable.** Every K8s PVC is unavailable. Pods that depend on persistent storage fail health checks. The K3s cluster itself stays up (control plane runs on VM-local disks); pods recover once Munin is back. PG and PBS continue to function (local LVM-thin + NFS datastore respectively).
- **Garage data corruption.** RF=1 means no built-in redundancy. Restore from PBS-backed VM snapshot of the K3s worker that hosted the PVC at the time of the corruption.

---

## See also

- **Hardware** section — drive specifications, NAS hardware, NVMe tier comparison.
- **Network** (this section) — Postgres HAProxy VIP routing, source-based policy routing for VRRP-bearing LXCs.
- **Identity & secrets** (this section) — Vault paths for Postgres credentials, Garage admin token, S3 access keys.
- **Troubleshooting** — iSCSI orphan sessions, DS223J LUN cap recovery, RHEL e2fsprogs limits for Synology-formatted volumes.
