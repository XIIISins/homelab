<!-- docs/procedures/netbox-initial-data-import.md -->

# NetBox initial data import

One-shot population of NetBox with the current homelab inventory. Done once per cluster lifetime (or after a wipe+rebuild). All data sourced from [`docs/architecture/network.md`](../architecture/network.md) (VLANs, prefixes, IPs) + [`terraform/proxmox/asgard-lxcs/lxcs.tf`](../../terraform/proxmox/asgard-lxcs/lxcs.tf) (LXCs) + [`ansible/inventory/hosts.yml`](../../ansible/inventory/hosts.yml) (group structure, k3s VMs) — those files remain the IaC source of truth; NetBox is the queryable view (per the Phase 5i decision row in [`docs/operations/decisions.md`](../operations/decisions.md)).

NetBox doesn't distinguish VM from LXC in its model — both are `VirtualMachine` objects. We use the `role` field to differentiate.

## Prerequisites

- [ ] Logged into NetBox as a superuser (ghost, post-manual-elevation per the OIDC → permissions caveat)
- [ ] Fresh DB or known-clean state (no overlapping entries from prior import attempts)

## 1. Site

- [ ] **Site**: `home` — name `home`, slug `home`. No rack/location/region (minimal model agreed in 5i scope).

## 2. Cluster

- [ ] **Cluster Type**: `Proxmox`
- [ ] **Cluster**: `niflheim`, type `Proxmox`, site `home`

## 3. Manufacturers + Device Types

NetBox devices require a manufacturer + device type. Minimal set:

- [ ] **Manufacturer**: `MSI` → **Device Type**: `Cubi` (model: Cubi N)
- [ ] **Manufacturer**: `Beelink` → **Device Type**: `MINI-S12`
- [ ] **Manufacturer**: `Synology` → **Device Type**: `DS223J`
- [ ] **Manufacturer**: `Ubiquiti` → **Device Type**: `UCG-Ultra`

## 4. Roles

NetBox has separate Device Roles and (in 4.x) shares them with VirtualMachine. Define both intents in one role list:

- [ ] `proxmox-host`
- [ ] `nas`
- [ ] `firewall`
- [ ] `k3s-control-plane`
- [ ] `k3s-worker`
- [ ] `db`
- [ ] `dns`
- [ ] `tailscale-gateway`
- [ ] `game-server`
- [ ] `backup-server`
- [ ] `service-frontend` (HAProxy/etcd trio)
- [ ] `monitoring` (future Zabbix)

## 5. VLANs

All scoped to site `home`. Names match `docs/architecture/network.md`.

| VID | Name            | Description                      |
| --- | --------------- | -------------------------------- |
| 1   | HL-MGMT         | Management                       |
| 10  | HL-ASG-VIP      | Asgard VIPs (keepalived)         |
| 11  | HL-ASG-SVC      | Asgard LXCs                      |
| 20  | HL-ASG-K3S-VIP  | Asgard K3s MetalLB               |
| 21  | HL-ASG-K3S-NODE | Asgard K3s nodes (CPs + workers) |
| 30  | HL-JOT-K3S-VIP  | Jotunheim K3s MetalLB            |
| 31  | HL-JOT-K3S-NODE | Jotunheim K3s nodes              |
| 60  | HL-CLIENT       | Personal devices                 |
| 100 | HL-STOR         | Storage / NFS                    |
| 222 | Untrusted       | Quarantine                       |

- [ ] Create all 10 VLANs above

## 6. Prefixes

One prefix per VLAN, linked to the corresponding VLAN. Status: `Active`.

- [ ] `10.0.254.0/24` → VLAN 1 (HL-MGMT)
- [ ] `10.0.10.0/24` → VLAN 10 (HL-ASG-VIP)
- [ ] `10.0.11.0/24` → VLAN 11 (HL-ASG-SVC)
- [ ] `10.0.20.0/24` → VLAN 20 (HL-ASG-K3S-VIP)
- [ ] `10.0.21.0/24` → VLAN 21 (HL-ASG-K3S-NODE)
- [ ] `10.0.30.0/24` → VLAN 30 (HL-JOT-K3S-VIP)
- [ ] `10.0.31.0/24` → VLAN 31 (HL-JOT-K3S-NODE)
- [ ] `10.0.60.0/24` → VLAN 60 (HL-CLIENT)
- [ ] `10.0.100.0/24` → VLAN 100 (HL-STOR)
- [ ] `10.0.222.0/24` → VLAN 222 (Untrusted)

## 7. Physical Devices

For each: create the Device, add an interface, assign a primary IP to that interface. NIC names matter — see CLAUDE.md "Networking" gotchas for the Beelink `nic0` vs Cubi `enp45s0` distinction.

### 7.1 Urd

- [ ] **Device**: `Urd`, role `proxmox-host`, type `MSI Cubi`, site `home`
- [ ] **Interface**: `enp45s0` (1GbE)
- [ ] **IP**: `10.0.254.11/24` on `enp45s0`, set as Primary IPv4
- [ ] **Description**: "Proxmox host. Hardware refresh Phase 4a (2026-05-21) — was Beelink, now MSI Cubi i3-1215u/32GB. No IaC config — Proxmox installed manually."

### 7.2 Verd

- [ ] **Device**: `Verd`, role `proxmox-host`, type `MSI Cubi`, site `home`
- [ ] **Interface**: `enp45s0`
- [ ] **IP**: `10.0.254.12/24` primary
- [ ] **Description**: "Proxmox host. Hardware refresh Phase 4c (2026-05-23) — was Beelink, now MSI Cubi i3-1215u/32GB."

### 7.3 Skuld

- [ ] **Device**: `Skuld`, role `proxmox-host`, type `Beelink MINI-S12`, site `home`
- [ ] **Interface**: `nic0` (Beelink's vendor-custom UEFI NIC name — different from Cubi's predictable `enp45s0`; see CLAUDE.md "Proxmox same-node hardware refresh" gotcha)
- [ ] **IP**: `10.0.254.13/24` primary
- [ ] **Description**: "Proxmox host. N100/16GB, the outlier vs Urd+Verd. Hosts PBS (1101) + several LXCs."

### 7.4 Munin

- [ ] **Device**: `Munin`, role `nas`, type `Synology DS223J`, site `home`
- [ ] **Interface**: `eth0`
- [ ] **IP**: `10.0.254.20/24` primary
- [ ] **Description**: "Synology NAS. RAID1 3.5TB. iSCSI provider for Synology CSI in asgard K3s. Tailscale subnet router (5e.3.f)."

### 7.5 UCG-Ultra

- [ ] **Device**: `UCG-Ultra`, role `firewall`, type `UCG-Ultra`, site `home`
- [ ] **Interface**: `eth0` (LAN side; WAN side faces KPN Experia DMZ)
- [ ] **IP**: `10.0.254.1/24` primary
- [ ] **Description**: "Sole firewall policy boundary. KPN Experia (DMZ) → UCG WAN. VLAN aggregator. Posture: Internal→Any allow / External→Internal allow-return / Any→Any deny (last)."

## 8. Virtual Machines

All assigned to cluster `niflheim`. NetBox 4.x has a `device` field on VirtualMachine to indicate which physical host runs each — use it where the placement is known.

### 8.1 Asgard K3s Control Planes (RHEL 9, 2vCPU/4GB/10GB)

- [ ] **Göndul** (`gondul`), role `k3s-control-plane`, cluster `niflheim`, device `Urd`, IP `10.0.21.11/24` primary
- [ ] **Hlökk** (`hlokk`), role `k3s-control-plane`, cluster `niflheim`, device `Verd`, IP `10.0.21.12/24` primary
- [ ] **Sigrún** (`sigrun`), role `k3s-control-plane`, cluster `niflheim`, device `Skuld`, IP `10.0.21.13/24` primary

### 8.2 Asgard K3s Workers (RHEL 9, 2vCPU/4GB/15GB, multi-homed)

Each has eth0 on VLAN 21 (K3s) + eth1 on VLAN 20 (MetalLB).

- [ ] **Einherjar-urd**, role `k3s-worker`, cluster `niflheim`, device `Urd`
  - Interface `eth0`, IP `10.0.21.21/24` (primary)
  - Interface `eth1`, IP `10.0.20.201/24`
- [ ] **Einherjar-verd**, role `k3s-worker`, cluster `niflheim`, device `Verd`
  - Interface `eth0`, IP `10.0.21.22/24` (primary)
  - Interface `eth1`, IP `10.0.20.202/24`
- [ ] **Einherjar-skuld**, role `k3s-worker`, cluster `niflheim`, device `Skuld`
  - Interface `eth0`, IP `10.0.21.23/24` (primary)
  - Interface `eth1`, IP `10.0.20.203/24`

### 8.3 Asgard LXCs

LXCs are also `VirtualMachine` objects. Tag with `lxc` for filtering.

| Name       | VMID             | Host  | Role              | Primary IP                             | Notes                                              |
| ---------- | ---------------- | ----- | ----------------- | -------------------------------------- | -------------------------------------------------- |
| PBS        | 1101             | Skuld | backup-server     | 10.0.11.20/24                          | Privileged LXC, NFS-bind to Munin                  |
| Saga       | (manual install) | ?     | dns               | 10.0.11.201/24                         | AGH primary; manually installed (5b.2 IaC pending) |
| Mimir      | (manual install) | ?     | dns               | 10.0.11.202/24                         | AGH replica                                        |
| Kvasir     | (manual install) | ?     | dns               | 10.0.11.203/24                         | AGH replica                                        |
| Bifrost    | 1113             | Urd   | tailscale-gateway | 10.0.11.213/24                         | Tailscale subnet router                            |
| Heimdall   | 1114             | Verd  | tailscale-gateway | 10.0.11.214/24                         | Tailscale subnet router                            |
| Gjallarbru | 1115             | Skuld | tailscale-gateway | 10.0.11.215/24                         | Tailscale exit node                                |
| Factorio   | 1120             | ?     | game-server       | 10.0.11.220/24                         | Operator self-service via SFTPGo                   |
| Fulla      | 1130             | Skuld | db                | 10.0.11.230/24                         | Patroni leader at adoption; PG cluster member      |
| Vör        | 1131             | Urd   | db                | 10.0.11.231/24                         | Patroni replica                                    |
| Idunn      | 1132             | Verd  | db                | 10.0.11.232/24                         | Patroni replica                                    |
| Hlin       | 1133             | Urd   | service-frontend  | 10.0.11.233/24 + 10.0.10.233/24 (eth1) | HAProxy + etcd + keepalived; eth1 VLAN 10 for VRRP |
| Eir        | 1134             | Verd  | service-frontend  | 10.0.11.234/24 + 10.0.10.234/24 (eth1) | HAProxy + etcd + keepalived                        |
| Snotra     | 1135             | Skuld | service-frontend  | 10.0.11.235/24 + 10.0.10.235/24 (eth1) | HAProxy + etcd + keepalived                        |

For each row above:

- [ ] PBS
- [ ] Saga
- [ ] Mimir
- [ ] Kvasir
- [ ] Bifrost
- [ ] Heimdall
- [ ] Gjallarbru
- [ ] Factorio
- [ ] Fulla
- [ ] Vör
- [ ] Idunn
- [ ] Hlin (add eth1 interface + IP `10.0.10.233/24` after primary)
- [ ] Eir (add eth1 + IP `10.0.10.234/24`)
- [ ] Snotra (add eth1 + IP `10.0.10.235/24`)

## 9. VIP / virtual IP records

Created as standalone IPAddress records (no owning interface; NetBox lets IPs exist without an interface for VIPs / anycast / reservation). Set `role: anycast` on each.

- [ ] **`10.0.10.200/32`** — AdGuard VIP. Description: "AGH primary VIP, keepalived-floated across Saga/Mimir/Kvasir on VLAN 10. VRID 51."
- [ ] **`10.0.10.210/32`** — PG HAProxy VIP. Description: "PostgreSQL write endpoint, keepalived-floated across Hlin/Eir/Snotra on VLAN 10. VRID 61. Routes via HAProxy → current Patroni leader."
- [ ] **`10.0.20.10/32`** — Traefik VIP. Description: "K3s edge LB, MetalLB L2 announcement on workers' eth1."

## 10. Tags (optional but useful for filtering)

Suggested taxonomy:

- [ ] `iac:terraform` — applied to TF-managed LXCs/VMs (everything in `lxcs.tf` + `asgard-k3s/`)
- [ ] `iac:manual` — applied to manually-installed nodes (AGH trio, Munin DSM config, Proxmox hosts)
- [ ] `phase:5g.2` — applied to PG cluster + HAProxy/etcd trio
- [ ] `phase:5e.3` — applied to Tailscale trio
- [ ] `function:patroni` — Fulla/Vör/Idunn
- [ ] `function:etcd` — Hlin/Eir/Snotra
- [ ] `function:keepalived-vrrp` — Saga/Mimir/Kvasir + Hlin/Eir/Snotra
- [ ] `lxc` — applied to every LXC (vs VM)

Apply tags retroactively via NetBox bulk-edit per group.

## 11. Per-entry descriptions — cross-link to IaC

For each device + VM, set the Description field to reference the IaC source. This is the manual bridge for the deferred Phase 5i.3 (TF → NetBox provider via `e-breuninger/netbox`). Examples:

- **Urd**: "Proxmox host. Hardware refresh Phase 4a (2026-05-21) — was Beelink N5095, now MSI Cubi i3-1215u/32GB. No IaC config — Proxmox installed manually."
- **Fulla**: "PG cluster member. Terraform: `terraform/proxmox/asgard-lxcs/lxcs.tf` locals.postgres_nodes.fulla (vmid 1130). Patroni-managed since 2026-05-24 (Phase 5g.2)."
- **Hlin**: "HAProxy + etcd + keepalived trio member. Terraform: `terraform/proxmox/asgard-lxcs/lxcs.tf` locals.haproxy_etcd_nodes.hlin (vmid 1133). eth0 VLAN 11 = etcd peer + Patroni REST API + service traffic; eth1 VLAN 10 = VRRP + VIP host."
- **Saga**: "AGH primary. Manually installed (Phase 5b.2 IaC pending). keepalived VIP 10.0.10.200, VRID 51. adguardhome-sync to Mimir + Kvasir on `*/1` cron."
- **Einherjar-urd**: "Asgard K3s worker. Terraform: `terraform/proxmox/asgard-k3s/main.tf`. Multi-homed: eth0 VLAN 21 (K3s) + eth1 VLAN 20 (MetalLB L2). Four landmine fixes in `roles/k3s/tasks/network.yml` (Calico autodetection pin, rp_filter, route_localnet, source-policy-routing)."

(Cross-links are this phase's manual artifact. Future Phase 5i.3 with `e-breuninger/netbox` provider will generate these from TF state, replacing the manual descriptions with TF-derived ones.)

## 12. Final sanity checks

- [ ] All 5 physical devices have primary IPv4 set
- [ ] All ~17 VMs/LXCs have primary IPv4 set
- [ ] No IPs are duplicated across multiple interfaces
- [ ] VLAN-to-prefix associations correct (browsing a Prefix shows its VLAN)
- [ ] Cluster `niflheim` shows ~17 VMs assigned
- [ ] Each Proxmox host shows the expected VMs under "Virtual Machines" tab
- [ ] Total counts roughly: 5 devices + ~17 VMs + 10 VLANs + 10 prefixes + ~30+ IPs + 3 VIP records

## What's next after import

- **Phase 5i.f** — Post-flight docs + commit bundle (all of 5i wrapped up)
- **Deferred Phase 5i.2** — Recovery LXC (fully-independent NetBox tier, local PG from PBS pg_dump)
- **Deferred Phase 5i.3** — TF → NetBox provider (`e-breuninger/netbox`); generates these device/VM records from TF state, replacing the manual descriptions
- **Deferred Phase 5i.4** — NetBox → Ansible dynamic inventory (Phase A populate → B shadow → C flip → D prune); deferred until AWX is on jotunheim K3s

## Notes

- Pending physical hardware to import later (not yet deployed): Jotunheim K3s VMs (Rota/Hildr/Kára CPs, Drengr-urd/verd/skuld workers), Zabbix LXC (1102), Jellyfin LXC, Teamspeak LXC (1121).
- AGH LXC VMIDs are unknown to docs/IaC (manual install). Fill in when you do the import if you happen to know; otherwise leave blank and revisit at Phase 5b.2 (AGH IaC).
- If you decide to use NetBox's rack model later (not now), the easiest retrofit is: create a single rack `shelf-1` in site `home`, then bulk-edit all 5 devices to assign rack `shelf-1` at position 1, 2, 3, 4, 5.
