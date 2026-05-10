# Decision log

All significant architectural decisions with reasoning. Update this as decisions are made or changed during the build.

## Infrastructure

| Decision | Choice | Reason |
|----------|--------|--------|
| Hypervisor | Proxmox VE (latest stable) | Familiar, LXC + VM support, HA built-in, good API for Terraform |
| Orchestrator | K3s only (no Docker Swarm) | Single orchestrator reduces complexity — Swarm removed deliberately |
| VM memory | Fixed allocation (no ballooning) | Predictable resource usage, no surprises under load |
| DRBD for Factorio + Teamspeak | Yes, via LINSTOR | Sub-30-second recovery target not achievable with Proxmox HA alone |
| Database clusters | LXC-based, not in K3s | Simpler ops, no operator complexity, databases in K8s adds risk at this scale |

## Network

| Decision | Choice | Reason |
|----------|--------|--------|
| Router/firewall | GL.iNet GL-MT2500 (~€50) | OOB access, VLAN routing, Tailscale, firewall in one device |
| Managed switch | Not needed | Proxmox + DSM handle VLAN tagging in software, dumb switches pass tags transparently |
| Homelab subnet | `10.0.x.0/24` per VLAN | Clean, VLAN ID = third octet, self-documenting in logs |
| Management VLAN | 254 / `10.0.254.0/24` | Non-default VLAN ID, enterprise convention |
| KPN router | Untouched | Family devices unchanged, GL-MT2500 connects as single WAN client |
| Personal devices | VLAN 60 | MacBook wired via dock, game PC wired — single cable upstairs |
| Remote access | Tailscale on GL-MT2500 + MacBook | GL-MT2500 survives homelab failure, MacBook uses KPN WiFi + Tailscale undocked |
| Game PC | VLAN 60, split-tunnel Tailscale | Full internet via GL-MT2500, homelab access via Tailscale routes only |
| 2.5 GbE | Not planned | No device supports it — Synology capped at 1 GbE |
| VPN | Tailscale | Zero-config clients, self-healing, integrates with Authentik OIDC |
| Wireguard | Dropped | Replaced by Tailscale — no manual key management, better UX |

## Storage

| Decision | Choice | Reason |
|----------|--------|--------|
| NAS | Synology DS223J (existing) | Already owned, sufficient for use case |
| Volume layout | 2 volumes (data + media) | Fewer volumes = fluid free space, no partition silos |
| Synology volumes | Btrfs | Snapshots, checksums, self-healing |
| iSCSI | Removed | Orphaned LUN deleted — NFS covers all use cases |
| K3s storage | NFS via democratic-csi | Simplest shared storage, no Ceph complexity |

## Identity

| Decision | Choice | Reason |
|----------|--------|--------|
| Identity provider | Authentik | Easier than Authelia, native OIDC + LDAP, Outline support, better user management UI |
| Linux identity | Authentik LDAP + SSSD | Unified identity — one user for SSH and all apps |
| Service account | Local `ansible` user | Cannot depend on SSSD/Authentik — needs to work even when IdP is broken |
| Break-glass | Local `recovery` user on Proxmox nodes only | Console access via `pct enter` / `qm terminal` covers everything from 3 nodes |
| Break-glass key | 1Password | Always available on phone, no homelab dependency |

## Secrets

| Decision | Choice | Reason |
|----------|--------|--------|
| Secrets management | HashiCorp Vault (self-hosted in K3s) | Production pattern, resume value, full control |
| Vault auto-unseal | AWS KMS (eu-west-1, existing account) | Always available independent of homelab, ~$1/month, replaces mystery charge |
| K8s secrets delivery | External Secrets Operator → Vault | Industry standard, decouples secrets from manifests |
| Bootstrap secrets | Sealed Secrets (one-time) + Ansible Vault | Resolves chicken-and-egg without external dependency |
| Must-run secrets | Ansible Vault permanently | Must-run cannot depend on K3s Vault |
| Repo visibility | Public | Secrets never in Git — safe to share, better for portfolio |

## IaC and GitOps

| Decision | Choice | Reason |
|----------|--------|--------|
| Provisioning | Terraform (bpg/proxmox provider) | Industry standard, natural evolution from Ansible-only |
| Configuration | Ansible | 10+ years experience, same playbooks work manually and via AWX |
| Ansible control plane | AWX | Centralised audit trail, scheduled reconciliation, Vault integration |
| Configuration drift | AWX 30-minute scheduled runs | Continuous reconciliation — idempotent, logged, self-healing |
| GitOps | Flux CD | Terminal-native, no UI dependency, aligns with existing Renovate workflow |
| Dependency updates | Renovate | Already in use, automatic PRs, human review before deploy |
| Terraform state | S3 backend (existing AWS account) | Versioned, locked, costs pennies |

## Services

| Decision | Choice | Reason |
|----------|--------|--------|
| Monitoring (infra) | Zabbix | Career familiarity, proven at enterprise scale |
| Monitoring (K3s) | Prometheus + Grafana | Native to K8s ecosystem, industry standard |
| Photo storage | Immich | Best self-hosted Google Photos alternative, active development |
| Documentation | Outline | Good editor, OIDC support, team-friendly |
| Automation | n8n | Visual workflows, good integrations, PostgreSQL backend |
| DNS filtering | Pi-hole (×3 + keepalived) | Proven, simple, excellent community |
| Ingress | Traefik | Native K8s integration, automatic TLS via cert-manager |
| Certificates | cert-manager + Let's Encrypt DNS-01 | Automatic, free, works for internal services too |
