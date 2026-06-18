<!-- docs/outline/hardware.md -->

# Hardware

The physical layer: three small Proxmox nodes, one NAS, one router/firewall, and two dumb switches. Everything else in the homelab — every VM, LXC, cluster, and service — is virtual and runs on top of these few boxes.

*Three nodes small enough that any one can be lost and rebuilt — resilience comes from the cluster, not from any single machine.*

---

## The physical kit

| Device | Class | Role | Name |
|---|---|---|---|
| MSI Cubi 5 (NUC-class) | i3-1215u / 32 GB DDR4 | Proxmox node | **Urd** |
| MSI Cubi 5 (NUC-class) | i3-1215u / 32 GB DDR4 | Proxmox node | **Verd** |
| MSI Cubi 5 (NUC-class) | i3-1215u / 32 GB DDR4 | Proxmox node | **Skuld** |
| Synology DS223J | 2-bay NAS, ~3.5 TB RAID 1 | iSCSI target + NFS server | **Munin** |
| UniFi Cloud Gateway Ultra | — | Router, firewall, VLAN routing | UCG-Ultra |
| KPN Experia Box | — | ISP modem, DMZ pass-through | — |
| Dumb switch ×2 | — | VLAN-transparent fan-out | — |

All three nodes are identical MSI Cubi 5's (i3-1215u, 32 GB DDR4) — failover is symmetric and any node can stand in for any other. The whole footprint is NUC-class mini-PCs plus a 2-bay NAS: low power, low noise, one site. The one place the nodes still differ is their NVMe drives — see the storage tier below.

---

## Physical topology

The cabling view — everything hangs off the UCG-Ultra, which sits behind the ISP box in DMZ mode:

```
Internet
  └── KPN Experia Box (192.168.2.0/24) — DMZ → UCG-Ultra WAN
        ├── Family devices, set-top box — untouched, on the KPN LAN
        └── UCG-Ultra (static WAN IP) — router, firewall, VLAN routing
              ├── Dumb switch (room)
              │     ├── MacBook dock   (VLAN 60)
              │     ├── Game PC        (VLAN 60)
              │     └── Hue bridge     (VLAN 60)
              └── Dumb switch (spare bedroom)
                    ├── Urd   (10.0.254.11)
                    ├── Verd  (10.0.254.12)
                    ├── Skuld (10.0.254.13)
                    └── Munin (10.0.254.20)
```

The switches are unmanaged — they pass VLAN tags transparently and do no routing. All policy lives on the UCG.

---

## Link layer

Everything runs at **1 GbE**. No 2.5 GbE is planned — the workloads that would benefit (iSCSI, NFS, replication) are all sized to stay comfortable inside a gigabit link, and the design accounts for it (local LVM-thin for write-heavy VM disks rather than network-backed storage).

A single VLAN-aware bridge on each node carries every VLAN tag to every VM and LXC; the dumb switches forward those tags without inspecting them.

---

## The storage tier

The three nodes do not have identical NVMe, and the difference is deliberate to know about: etcd (the K3s control-plane datastore) is fsync-sensitive, and the drives differ in how cleanly they handle sustained syncs. The short version — DRAM-equipped Gen 3 drives beat the DRAM-less Gen 4 drive for that workload. The **Hypervisors** and **Storage** subpages carry the detail.

---

## Where to go deeper

- **Hypervisors** — per-node CPU / RAM / NVMe, the storage-tier ranking that informs etcd placement, disk layout, QuickSync.
- **Networking** — UCG-Ultra, the dumb switches, the KPN DMZ boundary, and out-of-band access.
- **Storage** — the Synology NAS, its drives and RAID, and the per-node NVMe specs.

---

## See also

- **Compute & hypervisors** (Components & interactions) — the Proxmox cluster, K3s, and the VM/LXC split that runs on this hardware.
- **Network** (Components & interactions) — the logical VLAN / IP / firewall model layered over the physical gear.
- **Storage & data** (Components & interactions) — how the physical drives are consumed (Synology CSI, iSCSI, LVM-thin, NFS).
