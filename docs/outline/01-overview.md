# Homelab 2.0 — overview

## Goals

- Provide reliable services to friends and family (Teamspeak, Factorio, Immich)
- Learn and demonstrate modern infrastructure concepts (Kubernetes, GitOps, IaC, network segmentation)
- Build something resume-worthy at a senior/principal infrastructure level

## Principles

- Everything defined as code before it exists in production
- Complexity only where it serves a purpose
- Two-tier design: must-run (boring, stable) and can-run (learning environment)
- Self-healing at every layer
- Full audit trail — every change traceable to a human or an automated system

## Hardware

| Device | CPU | RAM | Role |
|--------|-----|-----|------|
| MINISFORUM DeskMini JB95 | Celeron N5095 | 32 GB | Proxmox node 1 — skadi |
| Beelink MINI-S12 | N100 | 16 GB | Proxmox node 2 — sigyn |
| Beelink MINI-S12 | N100 | 16 GB | Proxmox node 3 — sylvi |
| Synology DS223J | Realtek RTD1619B | 1 GB | NAS — storage and backups |
| GL.iNet GL-MT2500 | — | — | Router, firewall, OOB device |

## Power consumption (estimated)

| Device | Idle watts |
|--------|-----------|
| skadi (Celeron JB95) | ~15W |
| sigyn (N100 Beelink) | ~10W |
| sylvi (N100 Beelink) | ~10W |
| Synology DS223J | ~16W active, ~4W hibernation |
| GL-MT2500 | ~4W |
| **Total** | **~55W** |

At Dutch electricity rates (~€0.40/kWh): approximately **€192/year** (~€16/month).

## Architecture summary

Two logical tiers share the same three physical nodes:

**Must-run tier** — LXCs managed by Proxmox HA. Services with real consequences if down. Factorio, Teamspeak, Tailscale, Pi-hole, Zabbix, PBS. Boring, stable, minimal moving parts.

**Can-run tier** — K3s cluster (3 control plane + 3 worker VMs). Learning environment. All modern tooling: Flux GitOps, Vault secrets, AWX automation, Authentik identity. Failure here has no consequence to must-run services.

## Navigation

- [Network — physical topology](./02-network-topology.md)
- [Network — VLAN design](./03-network-vlans.md)
- [Network — firewall rules](./04-network-firewall.md)
- [Infrastructure — Proxmox cluster](./05-proxmox.md)
- [Infrastructure — must-run tier](./06-must-run.md)
- [Infrastructure — K3s cluster](./07-k3s.md)
- [Storage — Synology layout](./08-storage-layout.md)
- [Storage — migration steps](./09-storage-migration.md)
- [Identity — user model](./10-identity-users.md)
- [Identity — Authentik & SSSD](./11-identity-authentik.md)
- [Identity — SSH & OOB access](./12-identity-ssh-oob.md)
- [Secrets — architecture](./13-secrets-architecture.md)
- [Secrets — bootstrap sequence](./14-secrets-bootstrap.md)
- [IaC — Terraform](./15-iac-terraform.md)
- [IaC — Ansible & AWX](./16-iac-ansible-awx.md)
- [IaC — Flux & Renovate](./17-iac-flux-renovate.md)
- [IaC — repo structure](./18-iac-repo.md)
- [Services — must-run](./19-services-must-run.md)
- [Services — can-run (K3s)](./20-services-can-run.md)
- [Build sequence](./21-build-sequence.md)
- [Decision log](./22-decision-log.md)
