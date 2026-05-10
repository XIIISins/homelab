# Homelab — Claude Code context

This file gives you the background and decisions behind this homelab project. Read it before touching anything. The full design document is at `docs/homelab-design.md`.

---

## What this is

A ground-up homelab rebuild on 3 physical nodes, designed to:
- Provide reliable services to friends and family (Teamspeak, Factorio, Immich)
- Serve as a learning environment for Kubernetes and modern infrastructure
- Function as a portfolio/resume project demonstrating senior-level infrastructure design

The owner has 10+ years of Ansible experience and a career background in IT/sysadmin/platform engineering. Kubernetes is the primary learning goal.

---

## Hardware

| Host | CPU | RAM | Proxmox node |
|------|-----|-----|--------------|
| MINISFORUM DeskMini JB95 | Celeron N5095 | 32 GB | skadi |
| Beelink MINI-S12 | N100 | 16 GB | sigyn |
| Beelink MINI-S12 | N100 | 16 GB | sylvi |
| Synology DS223J | — | — | NAS (1 GbE only) |

All nodes are 1 GbE. No 2.5 GbE anywhere. Synology is capped at ~115 MB/s.

---

## Key architectural decisions — never second-guess these without asking

**Single orchestrator: K3s only.**
Docker Swarm was removed deliberately. Do not suggest Swarm, Docker Compose on VMs, or any second orchestrator. Everything that can run in K3s does.

**GitOps: Flux CD.**
The owner works 100% from the terminal. Flux was chosen over ArgoCD specifically because it has no UI dependency. Do not suggest ArgoCD. All K3s workloads are deployed via Flux — never suggest `kubectl apply` for production resources.

**IaC split: Terraform provisions, Ansible configures.**
Terraform (with `bpg/proxmox` provider) provisions all Proxmox VMs and LXCs, bootstraps K3s, and manages Cloudflare DNS. Ansible handles OS-level configuration and ongoing drift. Do not suggest manual Proxmox UI steps for anything Terraform can handle.

**Identity provider: Authentik.**
Authelia was replaced by Authentik for easier user/group management and native OIDC. Do not suggest Authelia. All applications that support OIDC integrate with Authentik.

**Databases: LXC-based, not in K3s.**
MariaDB Galera (×3 LXCs) and PostgreSQL (×3 LXCs) run outside K3s deliberately. Running clustered databases in Kubernetes adds operational complexity that isn't justified at this scale. Do not suggest moving databases into K3s or using database operators.

**Storage: NFS from Synology via democratic-csi.**
No iSCSI. No Ceph. No local-path for persistent data. All K3s PersistentVolumes are NFS-backed via democratic-csi pointing at the Synology.

**No memory ballooning.**
All VMs use fixed memory allocation for predictability.

**Identity: named users, no root SSH, full audit trail.**
Every host has a named personal admin user and an `ansible` service account. Root SSH is disabled after bootstrap. When `ansible` appears in auth logs it was AWX. When the personal username appears it was a human. These two identities must always be distinguishable. Do not suggest running anything as root or using root SSH keys.

**Ansible control plane: AWX (in K3s).**
AWX replaces a dedicated Ansible LXC. It provides centralised audit trail, scheduled reconciliation every 30 minutes, and Vault-backed credential management. The `ansible` service account SSH key lives in AWX credential store backed by Vault — never on disk. During Phases 4 and 5 (before K3s exists), playbooks run directly from the workstation using the same playbooks AWX will later execute. Do not suggest Semaphore, Rundeck, or a standalone Ansible LXC — AWX is the chosen tool.

**Configuration drift: AWX scheduled reconciliation.**
AWX runs full playbooks on a 30-minute schedule. Ansible idempotency means it only changes what has actually drifted. This is the infrastructure equivalent of Flux — Git is the source of truth, AWX continuously reconciles toward it. Do not suggest manual Ansible runs as the primary workflow.

**Secrets: Vault (self-hosted in K3s) + AWS KMS auto-unseal.**
This is the production pattern — not a homelab shortcut. Vault runs as a K3s StatefulSet. AWS KMS (eu-west-1, existing account, ~$1/month) handles auto-unseal. External Secrets Operator delivers secrets to pods. Sealed Secrets is used only for the one-time Vault bootstrap. Ansible Vault handles must-run LXC secrets and Terraform secrets. The repo is public — nothing sensitive ever touches Git. Do not suggest Sealed Secrets as a long-term secrets solution, do not suggest putting secrets in ConfigMaps, do not suggest committing encrypted secrets using any method other than Ansible Vault or Sealed Secrets for the bootstrap case.

**Monitoring split: Zabbix for infra, Prometheus+Grafana for K3s.**
Zabbix (in an LXC, outside K3s) monitors the infrastructure layer — Proxmox nodes, LXCs, Synology, network. Prometheus+Grafana inside K3s monitors workloads. The owner knows Zabbix from their career — do not suggest replacing it.

---

## Two-tier service design

### Must-run tier (LXCs managed by Proxmox HA)
Services with real consequences if down. Keep these boring and simple.

- Factorio LXC (skadi) — game server + SFTPGo for friend SFTP access
- Teamspeak LXC (sigyn) — voice server + PostgreSQL
- Tailscale LXCs (×3, one per node) — subnet router + exit node
- Pi-hole LXCs (×3) + keepalived VIP — internal DNS
- Zabbix LXC (sylvi) — infrastructure monitoring
- PBS LXC (sylvi) — Proxmox Backup Server

Factorio and Teamspeak use DRBD (via LINSTOR) for synchronous block replication across two nodes. Recovery target: under 30 seconds. Proxmox HA alone (60–120s) is not sufficient.

### Can-run tier (K3s)
Learning environment. Failure here has no consequence. This is where complexity lives intentionally.

6 VMs: 3 control plane (one per node) + 3 workers (one per node), co-located. K3s embedded etcd for HA control plane.

Planned workloads: AWX, Authentik, Vault, Outline, Immich, n8n, Jellyfin, Homepage, Komga, Startpage, Wallpaper gallery, phpIPAM, Privatebin, Prometheus, Grafana.

---

## Network

Physical router: **GL.iNet GL-MT2500** (~€50) sits between KPN router and homelab. KPN router is untouched — family devices unchanged. GL-MT2500 handles routing, firewall, VLAN tagging, Tailscale OOB, and DHCP for all homelab devices. No managed switch — Proxmox and DSM handle VLAN tagging in software, dumb switches pass tagged frames transparently.

```
KPN router (192.168.2.0/24, untouched)
  └── GL-MT2500 WAN
        ├── LAN 1 → Dumb switch → Proxmox nodes + Synology
        └── LAN 2 → Dumb switch upstairs → MacBook dock + game PC
```

MacBook connects via dock (wired, VLAN 60) when at desk. Uses KPN WiFi + Tailscale when undocked. Game PC wired on VLAN 60, split-tunnel Tailscale for homelab routes only.

### VLANs — VLAN ID = third octet (self-documenting)

| VLAN | Subnet | Name | Contents |
|------|--------|------|----------|
| 10 | `10.0.10.0/24` | must-run | Must-run LXCs |
| 20 | `10.0.20.0/24` | k3s | K3s VMs |
| 30 | `10.0.30.0/24` | storage | NFS traffic |
| 40 | `10.0.40.0/24` | services | MetalLB pool |
| 60 | `10.0.60.0/24` | personal | MacBook + game PC |
| 254 | `10.0.254.0/24` | management | GL-MT2500, Proxmox nodes, Synology |

### Key IPs
- `10.0.254.1` — GL-MT2500 (gateway)
- `10.0.254.100/101/102` — skadi/sigyn/sylvi
- `10.0.254.103` — Synology
- `10.0.10.110` — Pi-hole VIP
- `10.0.10.120–.129` — must-run LXCs
- `10.0.20.150–.155` — K3s VMs (150-152 CP, 153-155 workers)
- `10.0.40.160–.179` — MetalLB pool

### OOB access
Tailscale on GL-MT2500 advertises `10.0.0.0/8`. Survives complete homelab failure. Break-glass `recovery` user on Proxmox nodes only, SSH key in 1Password. From a Proxmox node, `pct enter` / `qm terminal` reaches any LXC/VM without SSH.

### DNS naming
- `app.xiiisins.com` — user-facing apps (Cloudflare Tunnel external, Pi-hole internal)
- `infra.xiiisins.com` — internal only, never in public DNS
- `svc.xiiisins.com` — non-HTTP services (Teamspeak SRV, Factorio A record)

---

## Repo structure

```
homelab/
├── CLAUDE.md
├── terraform/
│   ├── proxmox/       # VM and LXC definitions
│   ├── k3s/           # K3s VM bootstrap
│   └── dns/           # Cloudflare DNS records
├── ansible/
│   ├── inventory/
│   ├── roles/
│   ├── playbooks/
│   └── group_vars/    # Ansible Vault encrypted vars
├── k8s/               # Flux-managed manifests
│   ├── flux-system/
│   ├── infrastructure/ # MetalLB, Traefik, Authentik, cert-manager, Vault, ESO
│   └── apps/          # Application workloads
└── docs/
    └── homelab-design.md
```

---

## Conventions

- Terraform: use `bpg/proxmox` provider, not the older `telmate` provider
- Ansible: roles for reusable logic, playbooks orchestrate roles
- K8s manifests: Helm charts via Flux HelmRelease where available, Kustomize otherwise
- Secrets: never committed to Git — use sealed-secrets or external-secrets-operator
- Naming: Norse mythology theme for hosts/LXCs (already established: skadi, sigyn, sylvi, frigg, mist, aurora, etc.)
- All IPs static — no DHCP for infrastructure resources
- Image versions always pinned — Renovate handles update PRs

---

## What the owner wants to learn

Kubernetes is the primary goal. When writing K8s resources, explain the why behind design choices — don't just produce manifests. The owner understands Linux, Ansible, networking fundamentals, and enterprise infrastructure patterns. Kubernetes-specific concepts (controllers, reconciliation loops, admission webhooks, resource requests vs limits, etc.) are the knowledge gap to fill.

---

## Current build phase

Design complete. Not yet built. Starting from scratch — all three nodes will be wiped and reinstalled. The Synology storage layout is being consolidated before the rebuild begins.

See `docs/homelab-design.md` for the full design including storage migration steps, phased build sequence, and open questions.
