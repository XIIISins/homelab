# Homelab design document
*Last updated: 2026-05-13 — draft v3*

---

## Purpose

A ground-up homelab rebuild demonstrating senior-level infrastructure design.

### Goals
- Provide reliable services to friends and family (Teamspeak, Factorio, Immich)
- Learn and demonstrate modern infrastructure concepts (Kubernetes, GitOps, IaC, network segmentation)
- Portfolio/resume project at senior/principal infrastructure level

### Principles
- Everything defined as code before it exists in production
- Complexity only where it serves a purpose
- Self-healing at every layer
- Full audit trail — every change traceable to a human or automated system

---

## Hardware

| Device | CPU | RAM | Role | Norse name |
|--------|-----|-----|------|------------|
| MINISFORUM DeskMini JB95 | Celeron N5095 | 32 GB | Proxmox node 1 | **Urd** |
| Beelink MINI-S12 | N100 | 16 GB, 1TB disk | Proxmox node 2 | **Verd** |
| Beelink MINI-S12 | N100 | 16 GB, ~450GB LVM-thin | Proxmox node 3 | **Skuld** |
| Synology DS223J | Realtek RTD1619B | 1 GB | NAS | **Munin** |

### Naming convention
- **Proxmox cluster:** `niflheim`
- **Hypervisor nodes:** Urd (eldest/primary, 32GB), Verd, Skuld — the three Norns
- **NAS:** Munin — Odin's raven of memory
- **DNS zones:** `midgard.xiiisins.com` (public) + `niflheim.xiiisins.com` (internal)
- **Must-run K3s CP:** Göndul, Hlökk, Sigrún (Valkyries)
- **Must-run K3s workers:** Einherjar-urd/verd/skuld (army of the Norns)
- **Can-run K3s CP:** Rota, Hildr, Kára (Valkyries)
- **Can-run K3s workers:** Drengr-urd/verd/skuld (heroes of the Norns)
- **AdGuard Home:** Saga (primary), Mimir (replica), Kvasir (replica)

### Network hardware

| Device | Purpose |
|--------|---------|
| UniFi Cloud Gateway Ultra (UCG-Ultra) | Router, firewall, VLAN routing, zone-based firewall |
| Tailscale on Synology (Docker) | OOB access — survives complete homelab failure |
| Existing dumb switches (×2) | Pass VLAN tags transparently |

### Physical topology

```
Internet
  └── KPN Experia Box (192.168.2.0/24) — untouched
        ├── Settop box, family devices — unchanged
        └── UCG-Ultra WAN
              ├── LAN 1 → Dumb switch (your room)
              │             ├── MacBook dock (VLAN 60, static 10.0.60.10)
              │             ├── Game PC (VLAN 60)
              │             └── Hue bridge (VLAN 60)
              └── LAN 2 → Dumb switch (spare bedroom)
                            ├── Urd   (10.0.254.11 → now 10.0.1.11)
                            ├── Verd  (10.0.254.12 → now 10.0.1.12)
                            ├── Skuld (10.0.254.13 → now 10.0.1.13)
                            └── Munin (10.0.254.20 → now 10.0.1.20)
```

---

## Network design

### VLAN table

| VLAN | Subnet | UCG name | Purpose |
|------|--------|----------|---------|
| 1 | `10.0.1.0/24` | HL-MGMT | Management — nodes, NAS, UCG-Ultra |
| 10 | `10.0.10.0/24` | HL-CORE-VIP | Must-run VIPs (keepalived) |
| 11 | `10.0.11.0/24` | HL-CORE-SVC | Must-run LXCs |
| 20 | `10.0.20.0/24` | HL-CORE-K3S-VIP | Must-run K3s MetalLB pool |
| 21 | `10.0.21.0/24` | HL-CORE-K3S-WRK | Must-run K3s nodes |
| 30 | `10.0.30.0/24` | HL-CR-K3S-VIP | Can-run K3s MetalLB pool |
| 31 | `10.0.31.0/24` | HL-CR-K3S-WRK | Can-run K3s nodes |
| 60 | `10.0.60.0/24` | HL-CLIENT | Personal devices |
| 100 | `10.0.100.0/24` | HL-STOR | Storage / NFS — stable |
| 222 | `10.0.222.0/24` | Untrusted | Quarantine |

### IP assignments

**Management VLAN 1 (10.0.1.0/24):**

| Address | Role |
|---------|------|
| `10.0.1.1` | UCG-Ultra (gateway) |
| `10.0.1.11` | Urd |
| `10.0.1.12` | Verd |
| `10.0.1.13` | Skuld |
| `10.0.1.20` | Munin (Synology) |

**Must-run VIP VLAN 10 (10.0.10.0/24):**

| Address | Role |
|---------|------|
| `10.0.10.200` | AdGuard Home VIP (keepalived) |
| `10.0.10.201` | AdGuard eth1 — Saga (Urd) |
| `10.0.10.202` | AdGuard eth1 — Mimir (Verd) |
| `10.0.10.203` | AdGuard eth1 — Kvasir (Skuld) |
| `10.0.10.210` | HAProxy VIP — PostgreSQL frontend |

**Must-run LXC VLAN 11 (10.0.11.0/24):**

| Address | LXC ID | Node | Role |
|---------|--------|------|------|
| `10.0.11.20` | 1101 | Skuld | PBS ✅ |
| `10.0.11.21` | 1102 | Skuld | Zabbix |
| `10.0.11.201` | 1110 | Urd | AdGuard Home — Saga ✅ |
| `10.0.11.202` | 1111 | Verd | AdGuard Home — Mimir ✅ |
| `10.0.11.203` | 1112 | Skuld | AdGuard Home — Kvasir ✅ |
| `10.0.11.213` | 1113 | Urd | Tailscale 1 |
| `10.0.11.214` | 1114 | Verd | Tailscale 2 |
| `10.0.11.215` | 1115 | Skuld | Tailscale 3 |
| `10.0.11.220` | 1120 | Urd | Factorio + SFTPGo |
| `10.0.11.221` | 1121 | Verd | Teamspeak |
| `10.0.11.230` | 1130 | Urd | PostgreSQL 1 |
| `10.0.11.231` | 1131 | Verd | PostgreSQL 2 |
| `10.0.11.232` | 1132 | Skuld | PostgreSQL 3 |
| `10.0.11.233` | 1133 | Urd | HAProxy 1 |
| `10.0.11.234` | 1134 | Verd | HAProxy 2 |
| `10.0.11.235` | 1135 | Skuld | HAProxy 3 |

**Must-run K3s MetalLB VLAN 20 (10.0.20.0/24):**
`10.0.20.11–.99` — LoadBalancer pool

**Must-run K3s nodes VLAN 21 (10.0.21.0/24):**

| Address | VM ID | Node | Role | Name |
|---------|-------|------|------|------|
| `10.0.21.11` | 2001 | Urd | K3s CP | Göndul |
| `10.0.21.12` | 2002 | Verd | K3s CP | Hlökk |
| `10.0.21.13` | 2003 | Skuld | K3s CP | Sigrún |
| `10.0.21.21` | 2101 | Urd | K3s Worker | Einherjar-urd |
| `10.0.21.22` | 2102 | Verd | K3s Worker | Einherjar-verd |
| `10.0.21.23` | 2103 | Skuld | K3s Worker | Einherjar-skuld |

**Can-run K3s MetalLB VLAN 30 (10.0.30.0/24):**
`10.0.30.11–.99` — LoadBalancer pool

**Can-run K3s nodes VLAN 31 (10.0.31.0/24):**

| Address | VM ID | Node | Role | Name |
|---------|-------|------|------|------|
| `10.0.31.11` | 3001 | Urd | K3s CP | Rota |
| `10.0.31.12` | 3002 | Verd | K3s CP | Hildr |
| `10.0.31.13` | 3003 | Skuld | K3s CP | Kára |
| `10.0.31.21` | 3101 | Urd | K3s Worker | Drengr-urd |
| `10.0.31.22` | 3102 | Verd | K3s Worker | Drengr-verd |
| `10.0.31.23` | 3103 | Skuld | K3s Worker | Drengr-skuld |

**Personal VLAN 60 (10.0.60.0/24):**
- `10.0.60.1` — UCG-Ultra gateway
- `10.0.60.10` — MacBook (static)
- `10.0.60.11+` — Game PC, Hue bridge (DHCP)

**Storage VLAN 100 (10.0.100.0/24):**
NFS traffic only — no static assignments needed.

### Resource ID scheme

| Range | Type |
|-------|------|
| 1101–1199 | Must-run LXCs (sub-grouped by function) |
| 2001–2999 | Must-run K3s VMs |
| 3001–3999 | Can-run K3s VMs |
| 10001+ | Templates |

### LXC ID grouping

| Range | Group |
|-------|-------|
| 1101–1109 | Backup & monitoring (PBS, Zabbix) |
| 1110–1119 | Network infrastructure (AdGuard ×3) |
| 1113–1119 | Tailscale ×3 |
| 1120–1129 | Services (Factorio, Teamspeak) |
| 1130–1139 | Database (PostgreSQL ×3, HAProxy ×3) |

### DNS naming

**External (Cloudflare):**
`outline.xiiisins.com`, `immich.xiiisins.com`, `ts3.xiiisins.com` etc.

**Internal (AdGuard Home):**
```
midgard.xiiisins.com     — publicly reachable services
niflheim.xiiisins.com    — internal infrastructure only

Examples:
  saga.niflheim.xiiisins.com      → 10.0.11.201
  mimir.niflheim.xiiisins.com     → 10.0.11.202
  kvasir.niflheim.xiiisins.com    → 10.0.11.203
  adguard.niflheim.xiiisins.com   → 10.0.11.201 (admin alias)
  adguard-vip.niflheim.xiiisins.com → 10.0.10.200
  urd.niflheim.xiiisins.com       → 10.0.1.11
  verd.niflheim.xiiisins.com      → 10.0.1.12
  skuld.niflheim.xiiisins.com     → 10.0.1.13
  munin.niflheim.xiiisins.com     → 10.0.1.20
  pbs.niflheim.xiiisins.com       → 10.0.11.20
```

TLS: wildcard cert via Let's Encrypt DNS-01 using Cloudflare API. cert-manager manages lifecycle.

### Firewall (UCG-Ultra zone-based)

| From | To | Policy |
|------|----|--------|
| Management | Any | Allow |
| Personal | Management | Allow |
| Personal | Core services | Allow |
| Personal | Internet | Allow |
| Core services | Storage | Allow |
| Core services | Management | Allow |
| Core services | Internet | Allow |
| Can-run | Storage | Allow |
| Can-run | Core services | Allow |
| Can-run | Management | Allow |
| Can-run | Internet | Allow |
| Storage | Management | Allow |
| Untrusted | Internet | Allow |
| Any | Any | Deny |

---

## Proxmox cluster

- **Version:** PVE 9.x (Debian 13 Trixie)
- **Cluster name:** `niflheim`
- **No-subscription repo** on all nodes
- **VLAN-aware bridge** (`vmbr0`) on all nodes
- **Proxmox root LV:** 20GB — leaves ~94GB LVM-thin on Urd/Skuld, ~950GB on Verd
- **VM/LXC disks:** local LVM-thin

---

## Synology (Munin)

Factory reset. Fresh DSM. Two volumes on single RAID 1 pool.

### Volume 1 — data (~553GB)

| Folder | Protocol | Purpose |
|--------|----------|---------|
| `proxmox-backup` | NFS | PBS datastore |
| `k3s-core-data` | NFS | Must-run K3s PVs |
| `k3s-data` | NFS | Can-run K3s PVs |
| `db-backups` | NFS | DB dumps |
| `uploads` | NFS+SMB | Factorio SFTP |
| `hyper-backup` | Internal | Hyper Backup destination |

### Volume 2 — media (~2TB)

| Folder | Protocol | Purpose |
|--------|----------|---------|
| `media` | NFS+SMB | Movies, TV |
| `manga` | NFS+SMB | Manga — Komga |
| `downloads` | NFS | sabnzbd landing zone |
| `immich` | NFS | Photos/videos (~500GB reserved) |

~1.1TB unallocated buffer.

**OOB:** Tailscale Docker container, subnet router for `10.0.0.0/8`.
**K8s user:** `kubernetes` (admin) for Synology CSI driver.

---

## Two-tier service design

### Must-run LXCs

| LXC | ID | Node | IP | Role | Status |
|-----|----|------|----|------|--------|
| PBS | 1101 | Skuld | `10.0.11.20` | Proxmox Backup Server | ✅ |
| Zabbix | 1102 | Skuld | `10.0.11.21` | Infrastructure monitoring | 🔲 |
| Saga (AdGuard 1) | 1110 | Urd | `10.0.11.201` | DNS primary | ✅ |
| Mimir (AdGuard 2) | 1111 | Verd | `10.0.11.202` | DNS replica | ✅ |
| Kvasir (AdGuard 3) | 1112 | Skuld | `10.0.11.203` | DNS replica | ✅ |
| Tailscale 1 | 1113 | Urd | `10.0.11.213` | Subnet router | 🔲 |
| Tailscale 2 | 1114 | Verd | `10.0.11.214` | Subnet router | 🔲 |
| Tailscale 3 | 1115 | Skuld | `10.0.11.215` | Exit node | 🔲 |
| Factorio | 1120 | Urd | `10.0.11.220` | Game server + SFTPGo | 🔲 |
| Teamspeak | 1121 | Verd | `10.0.11.221` | Voice + PostgreSQL | 🔲 |
| PostgreSQL 1 | 1130 | Urd | `10.0.11.230` | Database node | 🔲 |
| PostgreSQL 2 | 1131 | Verd | `10.0.11.231` | Database node | 🔲 |
| PostgreSQL 3 | 1132 | Skuld | `10.0.11.232` | Database node | 🔲 |
| HAProxy 1 | 1133 | Urd | `10.0.11.233` | Load balancer | 🔲 |
| HAProxy 2 | 1134 | Verd | `10.0.11.234` | Load balancer | 🔲 |
| HAProxy 3 | 1135 | Skuld | `10.0.11.235` | Load balancer | 🔲 |
| Jellyfin | TBD | Urd | TBD | Media + QuickSync LXC | 🔲 |

**AdGuard Home:** VIP at `10.0.10.200`. Sync via `adguardhome-sync` binary on Saga. ✅

### Must-run K3s cluster

True core services — cascade failures if down. Resiliency > simplicity.

| Service | Replicas | Why |
|---------|----------|-----|
| Vault | 3 (Raft HA) | Secrets — no local fallback |
| Authentik server | 3 (pod anti-affinity) | OIDC + LDAP cascade |
| Authentik worker | 1 | Background tasks |
| Redis | 1 | Session cache, re-auth acceptable |
| MetalLB | 1 | Stable IPs for Vault + Authentik |
| Synology CSI (core) | 1 | PV provisioning |

**VMs:**

| Name | VM ID | Node | IP | Role |
|------|-------|------|----|------|
| Göndul | 2001 | Urd | `10.0.21.11` | K3s CP |
| Hlökk | 2002 | Verd | `10.0.21.12` | K3s CP |
| Sigrún | 2003 | Skuld | `10.0.21.13` | K3s CP |
| Einherjar-urd | 2101 | Urd | `10.0.21.21` | K3s Worker |
| Einherjar-verd | 2102 | Verd | `10.0.21.22` | K3s Worker |
| Einherjar-skuld | 2103 | Skuld | `10.0.21.23` | K3s Worker |

**Fallback documentation:** static HTML file on Munin with recovery procedures, IPs, and commands. Accessible even if both K3s clusters are down.

### Can-run K3s cluster

Learning environment. Services that don't cause cascade failures.

**VMs:**

| Name | VM ID | Node | IP | Role |
|------|-------|------|----|------|
| Rota | 3001 | Urd | `10.0.31.11` | K3s CP |
| Hildr | 3002 | Verd | `10.0.31.12` | K3s CP |
| Kára | 3003 | Skuld | `10.0.31.13` | K3s CP |
| Drengr-urd | 3101 | Urd | `10.0.31.21` | K3s Worker |
| Drengr-verd | 3102 | Verd | `10.0.31.22` | K3s Worker |
| Drengr-skuld | 3103 | Skuld | `10.0.31.23` | K3s Worker |

**Can-run services:** AWX, Netbox, Outline, n8n, Immich, Grafana, VictoriaMetrics, VictoriaLogs, Traefik, cert-manager, ESO, Cloudflared, Arr stack, Homepage, Komga, Privatebin, Startpage, Wallpaper gallery, SMTP relay, Synology CSI (can-run)

---

## Identity and access management

- **Personal user** — Authentik LDAP + SSSD. SSH key only.
- **`ansible` user** — local, AWX service account. Passwordless sudo.
- **`recovery` user** — break-glass, Proxmox nodes only. SSH key in 1Password.
- **`kubernetes` user** — Synology admin for CSI driver.
- **Proxmox API token** — scoped for Terraform only.

Local admin accounts on all web services (Vault, AWX, Grafana, Netbox, Outline) as Authentik break-glass fallback. Stored in 1Password.

---

## Secrets management

| Layer | Tool |
|-------|------|
| Vault auto-unseal | AWS KMS eu-west-1 (~$1/month) |
| K8s secrets | External Secrets Operator → Vault |
| Bootstrap | Sealed Secrets (one-time) |
| Terraform/Ansible | Ansible Vault |
| Must-run LXCs | Ansible Vault (permanent) |

Cloudflare API tokens split by consumer (Terraform, cert-manager, cloudflared).

---

## IaC

| Tool | Responsibility |
|------|---------------|
| Terraform (`bpg/proxmox`) | VMs, LXCs, DNS, AWS KMS |
| Ansible + AWX | OS config, drift correction, audit trail |
| Flux CD | K3s workload lifecycle |
| Renovate | Dependency version PRs |

---

## Build sequence

| Phase | Status | Description |
|-------|--------|-------------|
| 1 — Design | ✅ | Complete |
| 2 — UCG-Ultra | ✅ | VLANs, zones, firewall |
| 3 — Synology | ✅ | Factory reset, volumes, NFS |
| 4 — Proxmox cluster | ✅ | Urd/Verd/Skuld, cluster niflheim formed |
| 5a — PBS | ✅ | LXC 1101 on Skuld, connected |
| 5b — AdGuard Home | ✅ | Saga/Mimir/Kvasir + keepalived VIP + sync |
| 5c — Must-run K3s | 🔲 | Terraform VMs, K3s, Vault, Authentik |
| 5d — Remaining LXCs | 🔲 | Tailscale, Factorio, Teamspeak, PostgreSQL, HAProxy, Zabbix, Jellyfin |
| 6 — Can-run K3s | 🔲 | Terraform VMs, Flux, services |
| 7 — Observability | 🔲 | VictoriaMetrics + Logs + Grafana + Zabbix |

---

## Key decisions log

| Decision | Choice | Reason |
|----------|--------|--------|
| Orchestrator | K3s only | Single orchestrator |
| Two K3s clusters | Must-run (core) + Can-run | Core services isolated from experimental |
| Core K3s services | Vault, Authentik, Redis, MetalLB, Synology CSI | Cascade failure criterion |
| GitOps | Flux CD | Terminal-native |
| Identity | Authentik (must-run K3s) | OIDC + LDAP, cascade risk |
| DNS | AdGuard Home (not Pi-hole) | More polished, fully free, self-hosted sync |
| Galera | Dropped | Nothing requires MySQL — all on PostgreSQL |
| Database | PostgreSQL LXC cluster only | Zabbix migrated to PostgreSQL |
| IPAM | Netbox (replaces phpIPAM) | Enterprise standard, API-driven |
| Log aggregation | VictoriaLogs | Better performance, lower resources than Loki |
| Metrics | VictoriaMetrics | PromQL compatible, lower resources than Prometheus |
| Jellyfin | Privileged LXC on Urd | QuickSync /dev/dri passthrough |
| Router | UCG-Ultra | Zone-based firewall, polished UI |
| OOB | Tailscale on Synology | Independent of Proxmox |
| Storage VLAN | VLAN 100 | High number = stable, won't conflict |
| VM naming | Valkyries (CP) + Einherjar/Drengr (workers) | Norse theme, conceptually fits K3s |
| Node naming | Urd, Verd, Skuld (Norns) | Fate controllers = hypervisors |
| NAS naming | Munin | Raven of memory |
| Secrets | Vault + AWS KMS | Production pattern |
| PBS type | Privileged LXC | NFS mount requires it |
| Repo | Public | Secrets never in Git |

---

## Open questions

- [ ] Terraform setup for must-run K3s VMs
- [ ] K3s version to use (latest stable)
- [ ] VM specs finalised — CP: 2vCPU/1GB/10GB, Worker: 2vCPU/4GB/30GB
- [ ] Tailscale LXC naming (Heimdall, Bifrost, Ratatoskr candidates)
- [ ] Factorio + Teamspeak DRBD/LINSTOR for sub-30s recovery
- [ ] Create AWS KMS key + vault-unseal IAM user
- [ ] Cloudflare Tunnel — which services get external exposure
- [ ] Can-run K3s worker naming (Drengr confirmed, individual names TBD)
- [ ] Fallback static HTML doc on Munin for core K3s recovery
- [ ] AdGuard DNS records for new VMs as they're provisioned
- [ ] VLAN 100 NFS exports configured on Munin
- [ ] Proxmox HA for must-run LXCs

---

*This document is a living reference. Update it as decisions change during the build.*