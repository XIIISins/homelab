# Infrastructure — Proxmox cluster

## Setup

Fresh install of latest stable Proxmox VE on all three nodes. Cluster formed for HA fencing, live migration, and unified management. No Ceph — Synology handles shared storage via NFS.

## Nodes

| Node | Hardware | RAM | Management IP | Role |
|------|---------|-----|---------------|------|
| skadi | MINISFORUM JB95 (Celeron N5095) | 32 GB | `10.0.254.100` | Primary — most RAM, heavy workloads |
| sigyn | Beelink MINI-S12 (N100) | 16 GB | `10.0.254.101` | Worker |
| sylvi | Beelink MINI-S12 (N100) | 16 GB | `10.0.254.102` | Worker — hosts PBS + Zabbix |

## Networking in Proxmox

Each node has a VLAN-aware Linux bridge (`vmbr0`). VMs and LXCs are assigned VLAN tags on their virtual NICs — Proxmox tags traffic in software. The physical NIC carries all VLANs as a trunk to the dumb switch, which passes tags transparently to the GL-MT2500.

```
Physical NIC (trunk, all VLANs tagged)
  └── vmbr0 (VLAN-aware bridge)
        ├── VLAN 10 → must-run LXCs
        ├── VLAN 20 → K3s VMs
        ├── VLAN 30 → storage interfaces
        └── VLAN 254 → Proxmox host management IP
```

## High availability

Proxmox HA monitors all must-run LXCs and K3s VMs. If a node dies, HA restarts workloads on surviving nodes. Recovery time: 60–120 seconds for standard LXCs.

Factorio and Teamspeak use DRBD (via LINSTOR) for synchronous block replication — recovery target under 30 seconds. See [Infrastructure — must-run tier](./06-must-run.md) for detail.

## Proxmox Backup Server

PBS runs as an LXC on sylvi (`10.0.10.128`). Datastore on Synology NFS (`/volume1/proxmox-backup`). All VMs and LXCs backed up nightly, 7-day retention. Hyper Backup on Synology snapshots the PBS datastore to a separate folder for point-in-time recovery.

## API access (Terraform)

A dedicated Proxmox API token scoped to VM/LXC create/modify/delete and storage operations. Not tied to a personal login. Revocable independently. Shows in Proxmox audit logs separately from human logins.

Token stored in Ansible Vault during bootstrap, rotated into HashiCorp Vault after K3s is up.
