<!-- docs/outline/hardware/storage.md -->

# Storage

The physical storage: one NVMe drive in each Proxmox node, and one Synology NAS. The interesting part is not the drives but a hard limit in the NAS that forced the whole storage design to be tiered.

---

## Local NVMe

Each node has a single NVMe drive (Urd 1 TB, Verd 1 TB, Skuld 512 GB). It holds a small Proxmox root volume and an LVM-thin pool carrying every VM and LXC disk on that node. This is the right place for write-heavy workloads: PostgreSQL's backend is local LVM-thin, never NFS, because fsync and WAL latency over 1 GbE is the wrong shape for a database. K3s workers carry an extra 50 GB data disk (`/data`, xfs) on this same pool, which backs the `local-path` storage class.

Local disks are not shared. Surviving the loss of a node is done by replication in the application (Patroni, Vault Raft) and by PBS backups, not by the hypervisor.

---

## Munin (Synology DS223J)

A two-bay NAS with an ARM SoC and 1 GB of RAM, running two drives in RAID 1 (about 3.5 TB usable). All traffic reaches it over 1 GbE. It is carved into DSM volumes by purpose:

| Volume | Holds |
|---|---|
| **Volume 1** | Backups: the PBS datastore (mounted inside the PBS LXC over NFS) and Hyper Backup. |
| **Volume 2** | All K8s storage: the `k8s-nfs` share behind every `nfs-client` volume, and the one remaining iSCSI LUN class. |
| **Volume 5** | The media library (`media`), exported over NFS: read-only to Jellyfin, read/write to the media-automation pods. |

DSM's NFS share sizes are advisory: the DSM volume is the real ceiling.

---

## The LUN ceiling

The DS223J allows roughly **ten iSCSI LUNs in total, across the whole device**. The limit is enforced in DSM for this model, cannot be raised, and is not per-volume: adding volumes adds no slots. Synology's CSI driver creates one LUN per persistent volume, so a cluster that used iSCSI for everything hits the wall at about ten volumes.

Block storage is also single-mount by nature, so packing more workloads onto one LUN would need a filesystem layer on top, which is just NFS. The way out was to stop using iSCSI for everything:

| Tier | Backed by | Used for |
|---|---|---|
| **iSCSI** | Synology LUN (one per PVC) | Block-critical single-instance stores that memory-map their data (LMDB, BoltDB). Today: Garage's metadata. |
| **NFS** | `k8s-nfs` share on Munin | Large or append-style file data that tolerates NFS: the Immich library, Garage's object data, MicroBin, media apps. |
| **local-path** | 50 GB xfs `/data` disk on each worker | App-replicated or refillable state: Vault Raft, VictoriaMetrics, VictoriaLogs. |
| **emptyDir** | Pod-local | Pure caches (Authentik's Redis, NetBox's Valkey). |

There is deliberately no default StorageClass in the cluster: every PVC must name its tier or it will not bind, which protects the LUN budget from an accidental claim.

---

## Failure and recovery shape

- **Munin is a single point of failure** for NFS-backed volumes, the media library and the PBS datastore. Its RAID 1 protects against a drive, not the unit. Offsite copies of the critical state (etcd snapshots, Vault Raft snapshots, the Calico datastore export) go to S3 for exactly this reason.
- **A local-path volume is pinned to its worker.** If that worker is lost, the pod cannot reschedule elsewhere until the VM is restored from PBS. Only replicated or refillable data is allowed on this tier.
- The DS223J has no console: a stuck volume operation that survives every visible check ends with a NAS reboot.

---

## See also

- **Hardware** (this section): the physical overview.
- **Hypervisors** (this section): the local NVMe drives.
- **Storage & data** (Components): how Postgres, PBS, Garage and the K8s tiers are used.
- **Storage & iSCSI** troubleshooting (Troubleshooting): mount failures, orphan sessions, the LUN cap.
