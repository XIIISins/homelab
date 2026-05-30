<!-- docs/outline/hardware/storage.md -->

# Storage

The physical storage hardware: the NAS box, its drives and RAID, and the NVMe in each node. This page is about the disks themselves. How that storage is *consumed* — Synology CSI, iSCSI provisioning, LVM-thin, NFS, the Postgres tier — lives in **Storage & data** under Components & interactions.

---

## Munin — the NAS

Munin is a **Synology DS223J**, a 2-bay consumer NAS:

- **SoC:** Realtek RTD1619B (quad-core ARM).
- **RAM:** 1 GB, not expandable.
- **Disks:** two 3.5-inch drives in **RAID 1**, ~3.5 TB usable.
- **Network:** single 1 GbE, on the storage VLAN (100).

It plays two roles for the cluster:

- **iSCSI target** — block storage, provisioned by the Synology CSI driver (one LUN per PVC). Now reserved for the few volumes that genuinely need block (memory-mapped DBs); see the cap below.
- **NFS server** — file storage, for both PBS backups and **file-class K8s volumes** (via `csi-driver-nfs`).

Munin also hosts the **out-of-band Tailscale** node (see **Networking**), which is independent of the storage role but happens to live on the same box because the NAS is the one device that stays up when everything else is down. It runs **advertiser-only** (`--advertise-routes`, never `--accept-routes` — accepting routes would divert the NAS's own subnet into the tailnet and cut its LAN).

### Two volumes

The single RAID1 pool is split into two logical volumes (organisational, not a performance boundary — same two spindles):

- **Volume1 — backup.** The PBS datastore (NFS). Holds no live K8s storage.
- **Volume2 — all K8s.** The `k8s-nfs` share (every NFS PVC) + the one surviving iSCSI LUN (Garage metadata).

### The iSCSI LUN cap

The DS223J caps iSCSI LUNs at **~10 DSM-wide** — a hard limit on the 1 GB-RAM ARM unit, **not** per-Volume (adding volumes does not add slots). Because the cluster mints one LUN per PVC, putting every K8s volume on iSCSI hits the wall well before the disk is full. The structural answer is **storage tiering** (see **Storage & data**): iSCSI only for block-critical mmap DBs, NFS for file-class (no LUN cost), node-local `local-path` for replicated state, `emptyDir` for caches. That keeps iSCSI usage to a single LUN with the cap a non-issue. The cap and its diagnostics have a writeup in **Troubleshooting**.

---

## Per-node NVMe

Each Proxmox node has a single internal NVMe carrying both the Proxmox root LV and the local LVM-thin pool for VM and LXC disks.

| Node | Drive | Capacity | Generation | DRAM |
|---|---|---|---|---|
| Urd | Lexar NM790 | 1 TB | Gen 4 | DRAM-less (HMB 3.0) |
| Verd | Samsung 970 EVO | 1 TB | Gen 3 | On-board DRAM |
| Skuld | SK Hynix PC300 | 512 GB | Gen 3 | On-board DRAM |

The drives are not interchangeable in one respect that matters: Urd's Gen 4 NM790 is DRAM-less and leans on the Host Memory Buffer, which trails the two DRAM-equipped Gen 3 drives under etcd's fsync-heavy pattern. The **Hypervisors** page covers the tier ranking and the placement reasoning that follows from it.

### Per-worker `/data` disk (local-path tier)

Each K3s **worker** VM carries a **second, dedicated 50 GB disk** (a `scsi1` LVM-thin volume on the host NVMe, xfs, mounted `/data`), separate from its OS disk so it survives a VM rebuild. This backs the `local-path` StorageClass — node-local storage for Vault (Raft) and the VictoriaLogs/VictoriaMetrics mmap engines. It's on the node's NVMe, not the NAS, which is the point: replicated/mmap state gets fast local disk, and availability is solved at the app layer (Vault Raft) or via PBS (which backs up the `/data` disk daily).

---

## Storage backends at a glance

The physical disks above back five distinct logical storage tiers. The split is summarised here and detailed in **Storage & data**:

| Backed by | Logical tier | Serves |
|---|---|---|
| Per-node NVMe | Local LVM-thin | VM and LXC disks |
| Per-worker `/data` disk (NVMe) | `local-path` | Vault (Raft), VictoriaLogs, VictoriaMetrics |
| Munin Volume2 | Synology iSCSI (CSI) | block-critical mmap DBs (Garage metadata) |
| Munin Volume2 | Synology NFS (`csi-driver-nfs`) | file-class K8s volumes (teamspeak, Garage data) |
| Munin Volume1 | Synology NFS | PBS backups |
| `local-path` + iSCSI PVCs | Garage (S3) | object storage (meta block, data file) |

The design rule behind the split: **write-heavy, latency-sensitive, and memory-mapped data stays on local disk** (LVM-thin or the worker `/data` disk); replicated state uses node-local with HA at the app layer; only file-class bulk data goes over the network to Munin. Running Postgres, etcd, or an mmap DB on NFS over 1 GbE would invert that and is deliberately avoided.

---

## See also

- **Storage & data** (Components & interactions) — Synology CSI, iSCSI provisioning, LVM-thin, NFS, Garage S3, and the Postgres/Patroni tier.
- **Hypervisors** (this section) — the etcd storage-tier ranking and per-node disk layout.
- **Troubleshooting** — the DS223J iSCSI LUN cap and iSCSI session-handling playbooks.
