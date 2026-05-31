<!-- docs/outline/hardware/hypervisors.md -->

# Hypervisors

The three physical boxes that run Proxmox. This page is about the metal — CPUs, memory, drives, and the one hardware difference that actually matters operationally. The software that runs on them (Proxmox cluster, K3s, VMs vs LXCs) lives in **Compute & hypervisors**.

The nodes are named for the Norns: **Urd**, **Verd**, **Skuld**.

---

## The three nodes

| Node | Model | CPU | RAM | NVMe |
|---|---|---|---|---|
| **Urd** | MSI Cubi 5 (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | Lexar NM790 1 TB (Gen 4, DRAM-less) |
| **Verd** | MSI Cubi 5 (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | Samsung 970 EVO 1 TB (Gen 3, DRAM) |
| **Skuld** | MSI Cubi 5 (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | SK Hynix PC300 512 GB (Gen 3, DRAM) |

All three nodes are identical MSI Cubi 5's — same CPU, same memory. Failover is symmetric: any node can run any other's workload, and control-plane sizing is uniform everywhere. The only hardware that differs between them is the NVMe, and it matters for exactly one thing — etcd.

---

## Storage tier — why it matters for etcd

The drives differ in a way that's invisible most of the time but real for one workload: **etcd**, the K3s control-plane datastore, fsyncs on every write and is sensitive to sync latency.

For sustained fsync consistency the ranking is **Verd ≈ Skuld > Urd**.

That ordering is counter-intuitive — Urd's drive is the newest and fastest on paper (Gen 4 vs Gen 3). The catch is that Urd's NM790 is **DRAM-less**: it borrows host memory over the PCIe bus (Host Memory Buffer) for its flash-translation table instead of carrying its own DRAM. That adds a PCIe round-trip of latency to every metadata sync, which is exactly the pattern etcd hammers. Verd's and Skuld's DRAM-equipped Gen 3 drives are steadier under that load.

All three are comfortably within etcd's tolerance — this is a *preference* ranking for control-plane placement, not a hard constraint. Full drive specs are on the **Storage** subpage.

---

## Disk layout

Each node carries a single internal NVMe holding both the Proxmox root LV and the local LVM-thin pool for VM/LXC disks. The root LV is 20 GB; the rest is LVM-thin.

| Node | Drive | LVM-thin (approx) |
|---|---|---|
| Urd | 1 TB | ~950 GB |
| Verd | 1 TB | ~950 GB |
| Skuld | 512 GB | ~450 GB |

The pools differ in size only because Skuld's drive is smaller (512 GB vs 1 TB) — the nodes are otherwise identical. VM and LXC disks live on local LVM-thin by design — network-backed VM disks were ruled out because fsync latency over 1 GbE is the wrong shape for write-heavy workloads like Postgres and etcd. (The full storage-backend rationale is in **Storage & data**.)

---

## QuickSync (Urd)

Every node's i3-1215u includes Intel UHD Graphics with a capable QuickSync transcode engine, so any of the three could host hardware transcoding. The long-term plan designates **Urd** as the home for a dedicated Jellyfin LXC with `/dev/dri` passthrough — a deliberate placement choice rather than a hardware-forced one, keeping the transcode workload on a known node. (Jellyfin itself: **Services and purpose**.)

---

## See also

- **Compute & hypervisors** (Components & interactions) — the Proxmox `niflheim` cluster, the K3s topology, and the VM/LXC decision that runs on these nodes.
- **Storage** (this section) — full per-node NVMe specs and the Synology NAS.
- **Networking** (this section) — the physical network gear the nodes plug into.
