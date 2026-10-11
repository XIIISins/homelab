<!-- docs/outline/hardware/hypervisors.md -->

# Hypervisors

Urd, Verd and Skuld: three identical MSI Cubi mini-PCs forming the Proxmox cluster `niflheim`. They are deliberately the same machine, so any VM can run on any node and failover is symmetric.

---

## The nodes

| | Urd | Verd | Skuld |
|---|---|---|---|
| Management IP | `10.0.254.11` | `10.0.254.12` | `10.0.254.13` |
| CPU | i3-1215U (6c/8t) | same | same |
| RAM | 32 GB DDR4 | 32 GB DDR4 | 32 GB DDR4 |
| NVMe | Lexar NM790 1 TB (Gen 4, DRAM-less, HMB) | Samsung 970 EVO 1 TB (Gen 3, DRAM) | SK Hynix PC300 512 GB (Gen 3, DRAM) |
| Notable | Hosts PBS, Zabbix, Jellyfin (QuickSync) | Hosts Frigg (HA-managed) | Open hardware fault, hard-freezes |

Management traffic uses VLAN 1 (`10.0.254.0/24`). Each node has a VLAN-aware bridge (`vmbr0`) so guests attach to any VLAN by tag.

---

## Proxmox setup

- **Proxmox VE 9.x** on Debian 13, no-subscription repository, cluster name `niflheim`.
- **VLAN-aware `vmbr0`** on every node; guests choose their VLAN per NIC.
- **Small root, big thin pool.** The root logical volume is ~20 GB and the rest of the NVMe is an LVM-thin pool holding every VM and LXC disk. Local thin storage beats NFS for guest disks at 1 GbE, which matters most for Postgres (fsync and WAL latency).
- **No shared storage between nodes.** Guests are pinned to a node's local pool; moving one is a deliberate migrate (`pct migrate --restart` for LXCs), not a transparent live failover. Resilience comes from replication at the application layer (Patroni, Raft, keepalived pairs), not from the hypervisor.
- **PBS** backs up every guest daily (see **Storage & data**, Components).

---

## NVMe tier and etcd

The K3s control planes run etcd, which is sensitive to `fsync` latency. Ranking the drives for sustained sync workloads:

**Verd ≈ Skuld > Urd.**

Urd's Lexar NM790 is the newest (Gen 4) but DRAM-less: it uses host-memory-buffer (HMB), which adds PCIe round-trips per metadata update. The Gen 3 drives in Verd and Skuld have their own DRAM (Skuld's controller reports no HMB). All three are well inside etcd's tolerance, so the control planes are placed identically on all three nodes (one each) with no node excluded. The ranking only explains why Urd is the node to watch if etcd latency alerts ever fire.

---

## QuickSync

The i3-1215U's integrated UHD graphics has Intel QuickSync. Jellyfin runs in a **privileged LXC on Urd** with `/dev/dri` passed through, so transcodes are hardware-accelerated while the media library stays on Munin. Because that device passthrough needs root-ticket authentication in the Proxmox API, the Jellyfin LXC lives in the root-auth Terraform module (see **Compute & hypervisors**).

---

## Skuld

Skuld hard-freezes occasionally (an unresolved hardware fault, tracked as an incident). The operating rules that follow from it:

- Singleton roles do not sit on Skuld when they can sit elsewhere: PBS, the Tailscale exit node.
- Paired or quorum roles can: one Skuld freeze costs one member of a pair or one voter of three.
- A frozen Skuld is a recognised alert shape for the AIOps diagnosis agent.

---

## See also

- **Hardware** (this section): the physical overview and topology.
- **Storage** (this section): the NAS and how the local drives are split.
- **Compute & hypervisors** (Components): VMs, LXCs, ID scheme, bootstrap flow.
- **Troubleshooting**: what to check when a node misbehaves.
