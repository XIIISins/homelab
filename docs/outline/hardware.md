<!-- docs/outline/hardware.md -->

# Hardware

The physical layer: three identical mini-PCs running Proxmox, one small NAS, one router/firewall and two unmanaged switches. Everything above this layer is code; this is the part that is bought, racked and plugged in. The sub-pages go deeper on the hypervisors, the network gear and the storage.

---

## The kit

| Device | Spec | Role | Name |
|---|---|---|---|
| MSI Cubi (NUC-class) | i3-1215U (6 cores / 8 threads, 2 P-cores), 32 GB DDR4, 1 TB NVMe | Proxmox node 1 | **Urd** |
| MSI Cubi | i3-1215U, 32 GB DDR4, 1 TB NVMe | Proxmox node 2 | **Verd** |
| MSI Cubi | i3-1215U, 32 GB DDR4, 512 GB NVMe | Proxmox node 3 | **Skuld** |
| Synology DS223J | Realtek RTD1619B (ARM), 1 GB RAM, two drives in RAID 1 (~3.5 TB usable) | NAS: backups, K8s file storage, media | **Munin** |
| UniFi Cloud Gateway Ultra (UCG-Ultra) | Static public WAN IP | Router, VLAN routing, the only firewall policy boundary | n/a |
| KPN Experia Box | ISP modem/router in exposed-host (DMZ) mode | Pass-through to the UCG-Ultra WAN | n/a |
| Two unmanaged switches | Pass 802.1Q tags through | Layer-2 fabric | n/a |

The three Proxmox nodes are the Norns (Urd, Verd, Skuld); the NAS is Munin, Odin's raven of memory. The naming scheme is Norse throughout: the primary of a group defines the theme and its replicas expand within it. Every link in the building is **1 GbE**, and no 2.5 GbE upgrade is planned. That single fact shapes a lot of the design above this layer (where databases may live, why the NAS carries only file-class traffic).

---

## Topology

```
Internet
  └── KPN Experia Box (192.168.2.0/24, DMZ → UCG-Ultra WAN)
        ├── Set-top box, family devices (untouched)
        └── UCG-Ultra (static public IP)
              ├── LAN 1 → switch (office)
              │     ├── MacBook dock (clients VLAN)
              │     ├── Game PC
              │     └── Hue bridge
              └── LAN 2 → switch (spare room)
                    ├── Urd   10.0.254.11
                    ├── Verd  10.0.254.12
                    ├── Skuld 10.0.254.13
                    └── Munin 10.0.254.20
```

The KPN box forwards every unsolicited inbound packet (IPv4 and IPv6) to the UCG-Ultra, so the UCG-Ultra is the single place firewall policy lives. The switches do no policy: they carry VLAN tags and nothing else. VLANs, subnets and addressing live on the **Network** page (Components).

---

## What lives where

| Layer | Placement |
|---|---|
| Hypervisor | Proxmox VE 9 (Debian 13) on Urd, Verd and Skuld, clustered as `niflheim`. |
| VM and LXC disks | Local LVM-thin on each node's NVMe. |
| K3s | Six VMs (three control planes, three workers), one of each per node. |
| Services outside K3s | LXCs spread across the three nodes (DNS, Postgres, monitoring, PBS, Jellyfin, game server, AIOps hosts). |
| Backups | PBS in an LXC on Urd, datastore on Munin over NFS. |
| K8s file storage and media | Munin: an NFS share, one iSCSI LUN class and the media library. |
| Out-of-band access | Tailscale running on Munin itself, so a total hypervisor outage still leaves a way in. |

One physical node is not equal to the others in practice: **Skuld has an open hardware fault** and hard-freezes occasionally. That is why PBS was moved off it, why singleton roles (the Tailscale exit node, backups) avoid it, and why the AIOps tooling treats a Skuld freeze as a known incident shape. Details are on the **Hypervisors** page.

---

## Sub-pages

- **Hypervisors**: the three nodes, their NVMe drives (and why Urd's is the slowest for etcd), disk layout, QuickSync.
- **Networking**: the UCG-Ultra, switches, the KPN DMZ boundary, the 1 GbE link layer, out-of-band access.
- **Storage**: the Synology NAS (drives, RAID, the iSCSI LUN ceiling), the per-node NVMe, and how the backends split.

---

## See also

- **Compute & hypervisors** (Components): what runs on the hardware, VM/LXC decisions, ID scheme.
- **Network** (Components): VLANs, addressing, firewall and DNS.
- **Storage & data** (Components): how the storage backends are used by workloads.
