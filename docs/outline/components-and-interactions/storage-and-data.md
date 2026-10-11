<!-- docs/outline/components-and-interactions/storage-and-data.md -->

# Storage & data

Where state lives and why. The rule: match the storage to the access pattern. Databases get fast local disks; replicated state gets node-local disks; big file data gets NFS; the NAS's scarce block-storage slots go to the few workloads that truly need block semantics.

---

## The tiers

| Tier | Backing | Use for | Not for |
|---|---|---|---|
| **Local LVM-thin** | NVMe in each Proxmox node | VM and LXC disks, including every Postgres node | Anything that must move between nodes |
| **local-path** | 50 GB xfs `/data` disk on each K3s worker | App-replicated state (Vault Raft) and refillable stores (VictoriaMetrics, VictoriaLogs) | Single-instance data that must survive a worker loss |
| **NFS** (`nfs-client`) | `k8s-nfs` share on Munin via `csi-driver-nfs` | Large or append-style file data: the Immich library, Garage objects, MicroBin, Teamspeak files, media apps | Memory-mapped databases |
| **iSCSI** | Synology CSI, one LUN per volume | Block-critical single-instance stores that mmap their data. Today: Garage metadata (LMDB) | Anything else: the NAS allows roughly 10 LUNs in total |
| **emptyDir** | Pod-local | Caches that rebuild: Authentik's Redis, NetBox's Valkey | Anything you would miss |

**Why it is tiered.** The Synology DS223J enforces a hard, DSM-wide limit of about ten iSCSI LUNs, and the CSI driver makes one per volume. An all-iSCSI cluster hit that wall. Rather than fight it, each workload class was assigned the tier that suits it, which took the cluster from ten LUNs to one. Memory-mapped engines are the exception to "just use NFS": LMDB and BoltDB (and VictoriaMetrics' storage engine) misbehave on NFS, so they go on block or local disk.

**No default StorageClass.** Every PVC names its tier or it does not bind. This is deliberate: a forgotten `storageClassName` would silently consume a precious iSCSI LUN.

**Reclaim policy is Retain** on the NFS, local-path and iSCSI classes. Deleting a claim never deletes data; the cost is occasional manual cleanup of orphans.

---

## PostgreSQL

All relational data lives in one Patroni-managed PostgreSQL 17 cluster, `niflheim-pg`. Consumers: Authentik, NetBox, Zabbix, Teamspeak, Outline, Semaphore and Immich (with pgvector).

| Piece | Detail |
|---|---|
| Nodes | Fulla (Skuld), Vör (Urd), Idunn (Verd): LXCs, 2 vCPU / 4 GB / 16 GB, one per Proxmox node, identical sizing so failover does not degrade. |
| Storage | Local LVM-thin. Never NFS: WAL fsync over 1 GbE makes every commit a network round trip. |
| HA | **Patroni** runs leader election and owns Postgres config. Replicas stream from the leader. |
| DCS | **etcd** lives on the HAProxy trio (Hlin, Eir, Snotra), not on the Postgres nodes, so the consensus store does not share fate with the database it arbitrates. |
| Entry point | One **HAProxy VIP** `10.0.10.210` for every consumer, read and write. HAProxy health-checks each node's Patroni REST endpoint (`/master`); only the leader answers 200, so traffic goes to the leader deterministically. On a leader change, sessions are shut so clients reconnect through the VIP. keepalived floats the VIP across the trio. |
| Connection model | Direct to the VIP over TLS (`sslmode=require`, scram-sha-256). No pgbouncer. |
| Failover | Observed 10–30 seconds end to end; apps recover through connection retry. A Patroni callback posts promotions and demotions to Discord via Hermod. |
| Provisioning | Declarative per-service databases and roles from `postgres_databases`. **Deleting an entry never drops the database**: dropping is a deliberate manual act. |

Postgres only: no MySQL or Galera anywhere.

![PostgreSQL: Patroni HA behind one address](/api/attachments.redirect?id=8c5c36f5-61db-43e7-9fd8-729d5e5eab37)

---

## Object storage: Garage

**Garage** is a single-node S3-compatible store inside the cluster, used by Outline for attachments. Metadata (LMDB) sits on the one remaining iSCSI LUN; object data sits on NFS. It runs with replication factor 1 on purpose: both tiers already live on the same NAS, so replicating inside Garage would add no safety and block startup waiting for peers. Durability is the NAS and the backups, not Garage. Buckets, keys and permissions are provisioned by Terraform, which writes each consumer's S3 credentials to its own Vault path. The admin API is ClusterIP-only, reached by port-forward.

![Garage: S3 storage inside the cluster](/api/attachments.redirect?id=0beeb37e-1f2b-43d5-b117-315a2d11a6c7)

---

## Backups

| What | How | Where |
|---|---|---|
| LXCs and VMs | **PBS** (LXC on Urd) runs a nightly job over all guests. The datastore is a Munin NFS share mounted inside the PBS container. | Munin Volume 1 |
| Postgres data | PBS captures each Postgres LXC, plus a daily logical dump taken on whichever node is the current leader and captured by PBS. Point-in-time recovery is not yet built. | Munin |
| Cluster recovery state | K3s etcd snapshots (every 12 h from each control plane), a daily Vault Raft snapshot and a daily Calico datastore export, all to an S3 bucket outside the homelab. The writer credentials can put but not read. | AWS S3 |
| Media | Not backed up; replaceable. | Munin Volume 5 |
| Immich photos | A secondary copy only; the originals live elsewhere. | Munin NFS |

Backups are only trusted because they are exercised. A weekly **restore test** restores the newest backup of a canary plus one rotating production LXC into a scratch container with no network, checks it boots, and destroys it; a silent Wednesday means the test stopped, not that all is well. A one-off drill proved all three S3 legs (etcd, Calico, Vault Raft) restore into a throwaway cluster. A worker-VM restore from PBS is the part not yet covered. An offsite node on DigitalOcean (`do1`) holds the Teamspeak failover and a watcher.

---

## Failure surfaces

| What fails | Effect |
|---|---|
| One Postgres node | Patroni elects a leader from the others; HAProxy follows in seconds. |
| Two Postgres nodes or etcd quorum | Writes stop. Etcd quorum is on the HAProxy trio, so losing it means no automatic leader election even if Postgres is up. |
| Munin | NFS-backed apps (Immich, MicroBin, Teamspeak files, media), the iSCSI-backed Garage metadata and the PBS datastore all stall. The cluster control plane and Postgres keep running. |
| A worker (local-path) | Vault continues on 2 of 3 voters; VictoriaMetrics/Logs are down until the VM is restored. |
| iSCSI session problems | A network blip can force an ext4 volume read-only; the recovery is a manual fsck from a debug pod. |
| PBS | Backups stop, running services do not. PBS lives on Urd, away from Skuld's freezes. |

---

## See also

- **Storage** (Hardware): the NAS and the LUN ceiling.
- **Compute & hypervisors** (this section): where the Postgres, HAProxy and PBS LXCs sit.
- **Identity & secrets** (this section): where each service's database password comes from.
- **Storage & iSCSI** and **Vault & Postgres** troubleshooting (Troubleshooting).
- **Procedures**: whole-cluster rebuild and Vault recovery.
- **PostgreSQL diagram** (Diagrams): the VIP, HAProxy, etcd and the Patroni members.
- **Garage diagram** (Diagrams): the doors in and the two storage tiers.
