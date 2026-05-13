# Homelab — Claude Code context
*Read this before touching anything. Full design at `docs/homelab-design.md`.*

---

## What this is

A ground-up homelab rebuild on 3 physical nodes. Goals:
- Reliable services for friends/family (Teamspeak, Factorio, Immich)
- Learning environment for Kubernetes and modern infrastructure
- Portfolio/resume project at senior/principal infrastructure level

Owner has 10+ years Ansible experience, career in IT/sysadmin/platform engineering. Kubernetes is the primary learning goal.

---

## Hardware

| Host | CPU | RAM | Disk | Norse name |
|------|-----|-----|------|------------|
| MINISFORUM DeskMini JB95 | Celeron N5095 | 32 GB | 120GB SSD (~94GB LVM-thin) | **Urd** |
| Beelink MINI-S12 | N100 | 16 GB | 1TB (~950GB LVM-thin) | **Verd** |
| Beelink MINI-S12 | N100 | 16 GB | ~450GB LVM-thin | **Skuld** |
| Synology DS223J | — | — | 3.5TB RAID1 | **Munin** |

All 1 GbE. No 2.5 GbE planned.

---

## Naming convention

| Thing | Name | Reason |
|-------|------|--------|
| Proxmox cluster | `niflheim` | Hidden realm |
| Proxmox node 1 | Urd | Eldest Norn, most powerful |
| Proxmox node 2 | Verd | Second Norn |
| Proxmox node 3 | Skuld | Third Norn |
| NAS | Munin | Odin's raven of memory |
| Must-run K3s CP | Göndul, Hlökk, Sigrún | Valkyries (choosers/directors) |
| Must-run K3s workers | Einherjar-urd/verd/skuld | Army of the Norns |
| Can-run K3s CP | Rota, Hildr, Kára | Valkyries |
| Can-run K3s workers | Drengr-urd/verd/skuld | Heroes of the Norns |
| AdGuard primary | Saga | Goddess of wisdom/seeing |
| AdGuard replica 1 | Mimir | Keeper of wisdom |
| AdGuard replica 2 | Kvasir | Wisest being |
| Public DNS zone | `midgard.xiiisins.com` | The known world |
| Private DNS zone | `niflheim.xiiisins.com` | The hidden realm |

---

## Critical architectural decisions — never second-guess without asking

**Two K3s clusters:**
- Must-run K3s (VLAN 21) — core services, cascade failure criterion, resiliency > simplicity
- Can-run K3s (VLAN 31) — learning environment, experimental, failure acceptable
- Do NOT suggest merging them

**Core K3s services (must-run K3s only):**
Vault, Authentik (server ×3 + worker ×1), Redis, MetalLB, Synology CSI. These are the ONLY services in must-run K3s. Everything else goes in can-run. The criterion is cascade failure — if this service going down causes other core services to fail.

**No Docker Swarm. K3s only.**

**No Galera. PostgreSQL only.**
Zabbix migrated to PostgreSQL. Nothing requires MySQL.

**GitOps: Flux CD.**
Push to Git → exists. No ArgoCD. No manual `kubectl apply` for production.

**DNS: AdGuard Home (not Pi-hole).**
Three LXCs, keepalived VIP at `10.0.10.200`. AGH Sync binary on Saga. Do not suggest Pi-hole.

**Identity: Authentik in must-run K3s.**
OIDC for web apps, LDAP for SSH via SSSD. Local admin accounts on all services as break-glass. Do not suggest Authelia.

**Secrets: Vault + AWS KMS.**
AWS KMS eu-west-1 auto-unseal. ESO for K8s secrets. Ansible Vault for must-run LXCs permanently.

**Jellyfin: privileged LXC on Urd.**
Intel QuickSync via /dev/dri passthrough. Not in K3s.

**Storage: Synology CSI driver (official).**
Two instances — one per K3s cluster. Not democratic-csi.

**Monitoring: Zabbix LXC (outside K3s) + VictoriaMetrics/Logs/Grafana (can-run K3s).**
Zabbix stays as LXC for monitoring independence. VictoriaLogs replaces Loki. VictoriaMetrics replaces Prometheus.

**Ansible: AWX in can-run K3s.**
30-minute scheduled reconciliation. Vault-backed credentials.

**VM disks: local LVM-thin.**
Faster than NFS at 1 GbE.

**PBS: privileged LXC on Skuld.**
NFS bind-mounted via Proxmox host.

**Repo: public.**
Secrets never in Git.

---

## Network

```
KPN Experia Box (192.168.2.0/24, untouched)
  └── UCG-Ultra WAN
        ├── LAN 1 → Dumb switch (your room)
        │             ├── MacBook dock (VLAN 60, 10.0.60.10 static)
        │             ├── Game PC (VLAN 60)
        │             └── Hue bridge (VLAN 60)
        └── LAN 2 → Dumb switch (spare bedroom)
                      ├── Urd   (10.0.1.11)
                      ├── Verd  (10.0.1.12)
                      ├── Skuld (10.0.1.13)
                      └── Munin (10.0.1.20)
```

### VLANs

| VLAN | Subnet | Name | Purpose |
|------|--------|------|---------|
| 1 | `10.0.1.0/24` | HL-MGMT | Management |
| 10 | `10.0.10.0/24` | HL-CORE-VIP | Must-run VIPs |
| 11 | `10.0.11.0/24` | HL-CORE-SVC | Must-run LXCs |
| 20 | `10.0.20.0/24` | HL-CORE-K3S-VIP | Must-run K3s MetalLB |
| 21 | `10.0.21.0/24` | HL-CORE-K3S-WRK | Must-run K3s nodes |
| 30 | `10.0.30.0/24` | HL-CR-K3S-VIP | Can-run K3s MetalLB |
| 31 | `10.0.31.0/24` | HL-CR-K3S-WRK | Can-run K3s nodes |
| 60 | `10.0.60.0/24` | HL-CLIENT | Personal devices |
| 100 | `10.0.100.0/24` | HL-STOR | Storage/NFS (stable) |
| 222 | `10.0.222.0/24` | Untrusted | Quarantine |

### Key IPs
- `10.0.1.1` — UCG-Ultra
- `10.0.1.11/12/13` — Urd/Verd/Skuld
- `10.0.1.20` — Munin (Synology)
- `10.0.10.200` — AdGuard VIP ✅
- `10.0.11.20` — PBS (LXC 1101) ✅
- `10.0.11.201/202/203` — Saga/Mimir/Kvasir ✅
- `10.0.21.11/12/13` — Must-run K3s CP (Göndul/Hlökk/Sigrún)
- `10.0.21.21/22/23` — Must-run K3s workers
- `10.0.20.11–.99` — Must-run K3s MetalLB pool
- `10.0.31.11/12/13` — Can-run K3s CP (Rota/Hildr/Kára)
- `10.0.31.21/22/23` — Can-run K3s workers
- `10.0.30.11–.99` — Can-run K3s MetalLB pool

### Resource IDs
- `1101–1199` — Must-run LXCs (sub-grouped by function)
- `2001–2999` — Must-run K3s VMs
- `3001–3999` — Can-run K3s VMs
- `10001+` — Templates

---

## Current build status

- ✅ UCG-Ultra — all VLANs, zones, firewall
- ✅ Synology (Munin) — factory reset, volumes, NFS, kubernetes user
- ✅ Proxmox cluster — Urd/Verd/Skuld on PVE 9.x, cluster niflheim
- ✅ PBS — LXC 1101 on Skuld, NFS datastore, connected to cluster
- ✅ AdGuard Home — Saga/Mimir/Kvasir, keepalived VIP 10.0.10.200, AGH Sync
- 🔲 Must-run K3s — Terraform VMs next
- 🔲 Remaining must-run LXCs (Tailscale, Factorio, Teamspeak, PostgreSQL, HAProxy, Zabbix, Jellyfin)
- 🔲 Can-run K3s
- 🔲 Services

---

## Repo structure

```
homelab/
├── CLAUDE.md
├── renovate.json
├── terraform/
│   ├── proxmox/         # VM and LXC definitions
│   │   ├── must-run-k3s/
│   │   └── can-run-k3s/
│   ├── dns/             # Cloudflare DNS
│   └── aws/             # KMS key + IAM
├── ansible/
│   ├── inventory/
│   ├── roles/
│   ├── playbooks/
│   └── group_vars/      # Ansible Vault encrypted
├── k8s/
│   ├── must-run/        # Must-run K3s Flux manifests
│   │   ├── flux-system/
│   │   ├── infrastructure/
│   │   └── apps/
│   └── can-run/         # Can-run K3s Flux manifests
│       ├── flux-system/
│       ├── infrastructure/
│       └── apps/
└── docs/
    ├── homelab-design.md
    ├── recovery/        # Static fallback docs for core K3s
    └── outline/
```

---

## Conventions

- Terraform provider: `bpg/proxmox`
- All IPs static
- Image versions pinned, Renovate updates
- Conventional commits
- Norse mythology naming
- Never commit secrets

---

## What the owner wants to learn

Kubernetes is the primary goal. Explain the *why* behind K8s design choices, not just manifests. Owner knows Linux, Ansible, networking, enterprise infrastructure patterns. K8s-specific concepts are the knowledge gap.