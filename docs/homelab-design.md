# Homelab design document
*Last updated: 2026-05-10 — draft v1*

---

## Purpose

This document captures all design decisions for a ground-up homelab rebuild. It serves as the authoritative reference during the build and as a portfolio/resume artefact afterward.

### Goals
- Provide reliable services to friends and family (Teamspeak, Factorio, photo storage)
- Learn and demonstrate modern infrastructure concepts (Kubernetes, GitOps, IaC, network segmentation)
- Build something resume-worthy at a senior/principal infrastructure level

### Principles
- Everything defined as code before it exists in production
- Complexity only where it serves a purpose
- Two-tier design: must-run (boring, stable) and can-run (learning environment)
- Self-healing at every layer

---

## Hardware

| Device | CPU | RAM | Role |
|--------|-----|-----|------|
| MINISFORUM DeskMini JB95 | Celeron N5095 | 32 GB | Proxmox node 1 (skadi) |
| Beelink MINI-S12 | N100 | 16 GB | Proxmox node 2 (sigyn) |
| Beelink MINI-S12 | N100 | 16 GB | Proxmox node 3 (sylvi) |
| Synology DS223J | Realtek RTD1619B | 1 GB | NAS — storage and backups |

### Network hardware

| Device | Purpose | Cost |
|--------|---------|------|
| GL.iNet GL-MT2500 | Router, firewall, Tailscale OOB, no WiFi | ~€50 |
| Existing dumb switch (downstairs) | Connects Proxmox nodes + Synology to GL.iNet | €0 |
| Existing dumb switch (upstairs) | MacBook dock + game PC | €0 |

No managed switch required — Proxmox and DSM handle VLAN tagging in software. Tagged trunk traffic passes transparently through dumb switches. Total new hardware spend: ~€50.

### Network speed
All devices are 1 GbE. The Synology DS223J is capped at ~115 MB/s. No 2.5 GbE upgrade planned — nothing in the setup supports it.

### Physical topology

```
Internet
  └── KPN router (192.168.2.0/24) — untouched, family devices unchanged
        ├── Family WiFi, TV, phones — unchanged
        └── GL-MT2500 WAN port
              ├── LAN 1 → Dumb switch downstairs
              │             ├── skadi  (10.0.254.100)
              │             ├── sigyn  (10.0.254.101)
              │             ├── sylvi  (10.0.254.102)
              │             └── Synology (10.0.254.103)
              └── LAN 2 → Dumb switch upstairs
                            ├── MacBook dock (10.0.60.x, wired)
                            └── Game PC (10.0.60.x, wired)
```

MacBook also connects via KPN WiFi (192.168.2.x) when undocked — uses Tailscale to reach homelab resources in that case. Game PC uses split-tunnel Tailscale for homelab access (NAS backups etc.) while on the KPN network — but is physically wired into the upstairs switch on VLAN 60 most of the time.

---

## Network design

### Subnets and VLANs

VLAN ID matches the third octet — seeing `10.0.20.x` in a log immediately tells you it's a K3s VM. No lookup needed.

| VLAN ID | Subnet | Name | What lives here |
|---------|--------|------|-----------------|
| 10 | `10.0.10.0/24` | must-run | Must-run LXCs (Factorio, Teamspeak, Tailscale, Pi-hole, Zabbix, PBS) |
| 20 | `10.0.20.0/24` | k3s | K3s control plane and worker VMs |
| 30 | `10.0.30.0/24` | storage | NFS traffic between nodes and Synology |
| 40 | `10.0.40.0/24` | services | MetalLB LoadBalancer pool — exposed K3s services |
| 60 | `10.0.60.0/24` | personal | MacBook (wired via dock) + game PC |
| 254 | `10.0.254.0/24` | management | GL.iNet, physical Proxmox nodes, Synology, OOB access |

### IP assignments

**Management VLAN (10.0.254.0/24):**

| Address | Role |
|---------|------|
| `10.0.254.1` | GL-MT2500 (gateway for all VLANs) |
| `10.0.254.100` | skadi (Celeron N5095) |
| `10.0.254.101` | sigyn (N100) |
| `10.0.254.102` | sylvi (N100) |
| `10.0.254.103` | Synology DS223J |

**Must-run VLAN (10.0.10.0/24):**

| Address | Role |
|---------|------|
| `10.0.10.110` | Pi-hole keepalived VIP |
| `10.0.10.120` | Factorio LXC |
| `10.0.10.121` | Teamspeak LXC |
| `10.0.10.122` | Tailscale LXC 1 (subnet router) |
| `10.0.10.123` | Tailscale LXC 2 (subnet router) |
| `10.0.10.124` | Tailscale LXC 3 (exit node) |
| `10.0.10.125` | Pi-hole LXC 1 |
| `10.0.10.126` | Pi-hole LXC 2 |
| `10.0.10.127` | Pi-hole LXC 3 |
| `10.0.10.128` | PBS LXC |
| `10.0.10.129` | Zabbix LXC |

**K3s VLAN (10.0.20.0/24):**

| Address | Role |
|---------|------|
| `10.0.20.150` | K3s control plane 1 (skadi) |
| `10.0.20.151` | K3s control plane 2 (sigyn) |
| `10.0.20.152` | K3s control plane 3 (sylvi) |
| `10.0.20.153` | K3s worker 1 (skadi) |
| `10.0.20.154` | K3s worker 2 (sigyn) |
| `10.0.20.155` | K3s worker 3 (sylvi) |

**Services VLAN (10.0.40.0/24):**

| Address | Role |
|---------|------|
| `10.0.40.160–.179` | MetalLB LoadBalancer pool |

### Firewall rules (GL-MT2500)

```
# Management — full access everywhere (your admin VLAN)
10.0.254.0/24 → any: ALLOW

# Personal — internet, services, management (MacBook full access by static IP)
10.0.60.0/24 → internet: ALLOW
10.0.60.0/24 → 10.0.40.0/24: ALLOW
10.0.60.0/24 → 10.0.254.0/24: ALLOW (restrict to MacBook static IP for Proxmox/GL.iNet UI)
10.0.60.0/24 → 10.0.10.0/24: DENY
10.0.60.0/24 → 10.0.20.0/24: DENY
10.0.60.0/24 → 10.0.30.0/24: DENY

# Must-run — storage and management only
10.0.10.0/24 → 10.0.30.0/24: ALLOW
10.0.10.0/24 → 10.0.254.0/24: ALLOW
10.0.10.0/24 → internet: ALLOW
10.0.10.0/24 → 10.0.20.0/24: DENY
10.0.10.0/24 → 10.0.40.0/24: DENY

# K3s — storage, services, management, internet
10.0.20.0/24 → 10.0.30.0/24: ALLOW
10.0.20.0/24 → 10.0.40.0/24: ALLOW
10.0.20.0/24 → 10.0.254.0/24: ALLOW
10.0.20.0/24 → internet: ALLOW
10.0.20.0/24 → 10.0.10.0/24: DENY

# Storage — isolated, NFS traffic only
10.0.30.0/24 → 10.0.254.0/24: ALLOW
10.0.30.0/24 → any other: DENY

# Services (MetalLB) — reachable from personal + internet via Cloudflare Tunnel
10.0.40.0/24 → 10.0.20.0/24: ALLOW
10.0.40.0/24 → any other homelab: DENY
```

### How internet access works
All VLANs route to the internet via the GL-MT2500 which NATs traffic through the KPN router. K3s pods use the K3s internal network (`10.42.0.0/16`) and worker VMs masquerade pod traffic behind their `10.0.20.x` IP. No double-NAT issues for homelab services since all external access goes through Cloudflare Tunnel or Tailscale — neither requires port forwarding through the KPN router.

### OOB access
Tailscale runs on the GL-MT2500 as a subnet router advertising `10.0.0.0/8`. Even if all three Proxmox nodes are dead, the GL-MT2500 stays up and provides remote access to the management network. From there, Proxmox console access (`pct enter`, `qm terminal`) reaches any LXC or VM without SSH. Break-glass `recovery` user exists on all three Proxmox nodes only — SSH key stored in 1Password.

### DNS
Pi-hole runs across 3 LXCs with keepalived VIP at `10.0.10.110`. The GL-MT2500 uses the Pi-hole VIP as its upstream DNS server for all VLANs.

Split-horizon DNS naming convention:
- `app.xiiisins.com` — user-facing applications (Cloudflare Tunnel for external, Pi-hole resolves internally to MetalLB IP)
- `infra.xiiisins.com` — infrastructure tooling, internal only, never in public Cloudflare DNS
- `svc.xiiisins.com` — non-HTTP services (Teamspeak SRV record, Factorio A record)

TLS: wildcard cert via Let's Encrypt DNS-01 challenge using Cloudflare API token. cert-manager in K3s manages lifecycle. Covers `*.app.xiiisins.com` and `*.infra.xiiisins.com`.

---

## Proxmox cluster

Fresh install of latest stable Proxmox VE on all three nodes. Cluster formed for HA fencing, live migration, and unified management. No Ceph — Synology handles shared storage via NFS.

Node naming convention: Norse mythology theme retained (skadi, sigyn, sylvi).

### Proxmox Backup Server
Runs as an LXC on sylvi. PBS datastore on Synology NFS (`/volume-data/proxmox-backup`). All VMs and LXCs backed up nightly, 7-day retention. Hyper Backup on Synology snapshots the PBS datastore nightly to a separate volume for point-in-time recovery.

---

## Synology storage layout

Two volumes replacing the current 11-volume mess.

### Volume 1 — data (~500 GB)
Infrastructure and application data. Fixed size so PBS and K3s storage have a guaranteed ceiling media cannot eat into.

| Shared folder | Protocol | Purpose |
|---------------|----------|---------|
| `proxmox-backup` | NFS | PBS datastore |
| `k3s-data` | NFS | K3s persistent volumes via democratic-csi |
| `docker-config` | NFS | Legacy — remove in rebuild |
| `db-backups` | NFS | MariaDB + PostgreSQL dumps (currently missing) |
| `uploads` | SMB | OD-Upload for friend, Factorio mod uploads via SFTP |
| `hyper-backup` | Internal | Hyper Backup destination |

### Volume 2 — media (~3.1 TB, remainder of pool)
All bulk media. Gets the rest of the pool so it can grow freely.

| Shared folder | Protocol | Purpose |
|---------------|----------|---------|
| `media` | NFS + SMB | Movies, TV — Jellyfin and arr stack |
| `manga` | NFS + SMB | Manga collection (currently on volume 8) |
| `downloads` | NFS | sabnzbd landing zone |
| `wallpapers` | NFS + SMB | Wallpaper gallery |
| `immich` | NFS | Immich photo library |

### Migration steps (before rebuild)
1. Delete orphaned iSCSI LUN (`sonarr`, 25 GB on volume 11) — confirmed no active sessions
2. Copy volume 1 factorio data (2.6 GB) to temporary location
3. Delete volumes 1, 3, 4, 7, 8 (reclaims ~163 GB of pool space)
4. Create new volume 1 (data, ~500 GB) from reclaimed pool space
5. Rename/repurpose current volume 2 (media) as the new volume 2 — no data movement required
6. Move manga from old volume 8 into new volume 2 `/manga` folder
7. Update PBS datastore path from volume 11 to new volume 1
8. Update Hyper Backup jobs to new volume structure

---

## Two-tier service design

### Must-run tier
Services with real consequences if down. Boring, stable, minimal complexity.

**Proxmox HA** restarts LXCs on a surviving node if a host dies. Target recovery time: under 2 minutes for most services. Factorio and Teamspeak will use **DRBD** (via LINSTOR) for synchronous block replication across two nodes, targeting under 30-second recovery.

| LXC | Node (primary) | IP | Role |
|-----|---------------|-----|------|
| factorio | skadi | `10.0.10.120` | Factorio game server + SFTPGo |
| teamspeak | sigyn | `10.0.10.121` | Teamspeak 3 + PostgreSQL |
| tailscale-1 | skadi | `10.0.10.122` | Tailscale subnet router |
| tailscale-2 | sigyn | `10.0.10.123` | Tailscale subnet router (redundant) |
| tailscale-3 | sylvi | `10.0.10.124` | Tailscale exit node |
| pihole-1 | skadi | `10.0.10.125` | Pi-hole + keepalived |
| pihole-2 | sigyn | `10.0.10.126` | Pi-hole + keepalived |
| pihole-3 | sylvi | `10.0.10.127` | Pi-hole + keepalived |
| pbs | sylvi | `10.0.10.128` | Proxmox Backup Server |
| zabbix | sylvi | `10.0.10.129` | Zabbix server (monitors all infrastructure) |

*Note: IPs above are illustrative — finalise against the full IP plan.*

### Factorio SFTP access
SFTPGo runs inside the Factorio LXC. Mounts the NFS share (`/volume-data/uploads/factorio`) for saves, mods, and config. Friend accesses via SFTP on a dedicated port. No shell access granted.

### Tailscale
Three LXCs provide redundancy. Two configured as subnet routers advertising `10.0.0.0/8`. One configured as an exit node. Tailscale also runs on the GL-MT2500 as an independent subnet router — survives complete homelab failure. If one LXC dies Tailscale automatically fails over to another subnet router.

---

## Can-run tier — K3s cluster

Learning environment. Complexity is intentional. Failure here has no consequence.

### Cluster topology
6 VMs total — one control plane VM and one worker VM per physical node, co-located.

| VM | Node | IP | Role | vCPU | RAM |
|----|------|-----|------|------|-----|
| k3s-cp-1 | skadi | `10.0.20.150` | Control plane + etcd | 2 | 2 GB |
| k3s-cp-2 | sigyn | `10.0.20.151` | Control plane + etcd | 2 | 2 GB |
| k3s-cp-3 | sylvi | `10.0.20.152` | Control plane + etcd | 2 | 2 GB |
| k3s-wk-1 | skadi | `10.0.20.153` | Worker (heavy workloads) | 2 | 8 GB |
| k3s-wk-2 | sigyn | `10.0.20.154` | Worker | 2 | 4 GB |
| k3s-wk-3 | sylvi | `10.0.20.155` | Worker | 2 | 3 GB |

skadi's worker gets more RAM because the Celeron has 32 GB — it hosts heavier workloads (Jellyfin, Immich).

### Core cluster components

| Component | Purpose |
|-----------|---------|
| K3s | Lightweight Kubernetes distribution |
| Flux CD | GitOps — push to Git, cluster reconciles |
| Renovate | Automated dependency/image version PRs |
| MetalLB | LoadBalancer IP assignment from `.160–.179` pool |
| Traefik | Ingress controller (replaces Swarm Traefik) |
| Authentik | Identity provider — OIDC/SAML for all apps |
| democratic-csi | NFS-backed PersistentVolumes from Synology |
| cert-manager | TLS certificate lifecycle |

### Planned workloads

| Service | Purpose | Notes |
|---------|---------|-------|
| Authentik | IdP, OIDC | All apps authenticate through here |
| Outline | Wiki / documentation | OIDC via Authentik, PostgreSQL backend |
| Immich | Photo/video storage for family | GPU-less transcoding on skadi worker |
| n8n | Automation | PostgreSQL backend |
| Jellyfin | Media streaming | Stateless-ish, NFS for media |
| Homepage | Dashboard | Docker socket + K8s API for service discovery |
| Komga | Manga reader | NFS for manga files |
| Startpage | New tab page | Custom Docker image, stateless |
| Wallpaper gallery | Internal image gallery | NFS for wallpapers |
| phpIPAM | IP address management | MariaDB backend |
| Privatebin | Pastebin alternative | Stateless |
| Zabbix agent | Infrastructure monitoring | Reports to Zabbix LXC |
| Prometheus + Grafana | K3s workload monitoring | Separate from Zabbix |

### GitOps workflow
```
Developer pushes to GitHub repo
  → Flux detects change (poll interval: 1 min)
    → Flux reconciles cluster state
      → New/changed resources applied automatically

Renovate scans image tags and Helm chart versions
  → Opens PR with version bump
    → Developer reviews and merges
      → Flux deploys automatically
```

No manual `kubectl apply` for production workloads. Ever.

---

## Identity and access management

Production discipline from day one. Root access is disabled over SSH on all nodes after initial bootstrap. Every action is traceable to either a named human or an automated system.

### Human identity

One named admin user per person (currently just you). Created on every Proxmox node, LXC, and K3s VM during initial Ansible provisioning.

- SSH key authentication only — password auth disabled
- Root SSH login disabled on all hosts after bootstrap
- Sudo access with logging — all sudo commands written to auth log
- Personal SSH key pair: private key stays on your workstation, never copied elsewhere

### Service account identity

| Account | Used by | Sudo scope | Notes |
|---------|---------|------------|-------|
| `ansible` | AWX | Passwordless, all commands | All automated changes traceable to AWX |
| `terraform` | Not a system user — Proxmox API token only | N/A | No shell access |

The `ansible` account appears in auth logs on every managed host whenever AWX runs a playbook. When you see your personal username in auth logs it was a human change. When you see `ansible` it was AWX. Root should never appear after initial bootstrap.

### Proxmox API token (Terraform)

A dedicated Proxmox API token scoped to exactly what Terraform needs — not your personal login, not root. Tokens are independently revocable and show up in Proxmox audit logs separately from human logins.

### SSH key strategy

```
Your personal key pair (Ed25519)
  → Authorised on: all Proxmox nodes, all LXCs, all K3s VMs
  → Lives: your workstation only, backed up to 1Password

AWX ansible service key pair (Ed25519)
  → Authorised on: all managed hosts (ansible user)
  → Lives: AWX credential store → backed by Vault
  → Private key never touches disk outside AWX
```

---

## Configuration management — AWX

AWX (open-source Ansible Tower) runs in K3s and serves as the centralised Ansible control plane. It replaces a dedicated Ansible LXC and provides:

- Full audit trail of every playbook run — who triggered it, when, what changed, full output
- Scheduled reconciliation — playbooks run on a schedule to detect and fix drift automatically
- Credential management integrated with Vault — the `ansible` SSH key and sudo credentials never sit in plaintext
- Webhook receiver — external tools can trigger runs on demand
- Job templates and workflows — complex multi-playbook operations defined once, run reliably

AWX uses your existing PostgreSQL LXC cluster as its database backend — no additional stateful workload inside K3s.

### Reconciliation model

```
Git repo (source of truth for everything)
  ↓
Flux — reconciles K3s workloads continuously (every 1 minute)
AWX  — reconciles Ansible-managed infrastructure on schedule (every 30 minutes)
         → Full playbook run against all Proxmox nodes, LXCs, K3s VMs
         → Idempotent — only changes what has actually drifted
         → Every run logged: timestamp, trigger, changed tasks, full output
```

### What AWX manages

Everything outside K3s — Proxmox node configuration, LXC configuration, K3s VM OS configuration, user accounts and SSH keys across all hosts, package versions and system configuration.

### AWX in the build sequence

AWX is deployed in Phase 6 alongside the K3s cluster. During Phases 4 and 5, Ansible playbooks are run directly from your workstation using the same playbooks AWX will later execute — no rework required.

---

## Secrets management

Production-grade secrets architecture. Nothing sensitive ever touches Git. Repo is public.

### Root of trust

**AWS KMS** (eu-west-1, existing AWS account) is the auto-unseal backend for Vault. One KMS key, one IAM user (`vault-unseal`) with a policy scoped to exactly that key. Cost: ~$1/month, replacing an existing mystery charge on the account. AWS KMS is always available independent of the homelab — exactly the same architectural pattern enterprises use with cloud KMS.

### Self-hosted Vault in K3s

Vault runs in the can-run tier as a K3s StatefulSet. On startup, an init container fetches the AWS KMS credentials from a Sealed Secret (bootstrap mechanism — see below), then Vault auto-unseals via AWS KMS. External Secrets Operator pulls secrets from Vault and creates native K8s secrets for pods.

### Secrets by layer

| Layer | Tool | Notes |
|-------|------|-------|
| Vault auto-unseal | AWS KMS | Always available, ~$1/month |
| K8s app secrets | External Secrets Operator → Vault | Industry standard pattern |
| Vault bootstrap credentials | Sealed Secrets | One-time bootstrap only |
| Terraform secrets | Ansible Vault + `TF_VAR_` env vars | State never contains secrets |
| Ansible secrets | Ansible Vault | Used for must-run tier throughout |
| Must-run LXC secrets | Ansible Vault only | Must-run cannot depend on K8s Vault |
| Git | Nothing sensitive, ever | Repo is public |

### Bootstrap sequence

There is a deliberate chicken-and-egg on day one — Vault doesn't exist yet so it can't be used during initial provisioning. The bootstrap order resolves this cleanly:

```
1. Terraform provisions K3s VMs
2. Ansible configures nodes (secrets from Ansible Vault)
3. K3s bootstrapped
4. Flux installed
5. Sealed Secrets controller deployed via Flux
6. AWS KMS credentials sealed and committed to repo
7. Vault deployed via Flux — init container unseals via AWS KMS
8. External Secrets Operator deployed, pointed at Vault
9. All secrets migrated into Vault
10. Everything else deployed via Flux using ESO-managed secrets
```

After bootstrap, Ansible Vault is only used for must-run tier LXC secrets and the AWS KMS IAM credentials (which are also stored in Vault post-bootstrap for rotation purposes).

### IAM policy (least privilege)

```json
{
  "Effect": "Allow",
  "Action": ["kms:Decrypt", "kms:Encrypt", "kms:DescribeKey"],
  "Resource": "arn:aws:kms:eu-west-1:ACCOUNT_ID:key/KEY_ID"
}
```

Nothing else. A compromised credential can only unseal Vault — useless without also having access to Vault's encrypted storage.

### Repo safety

Repo is public. Safe because:
- Ansible Vault encrypts all secrets at rest before committing
- Sealed Secrets are encrypted with the cluster's public key — useless without the cluster
- AWS credentials never committed — injected via environment variables at Terraform runtime
- `terraform.tfvars` is gitignored — `terraform.tfvars.example` committed with placeholders

---

## IaC structure

### Tool responsibilities

| Tool | Responsibility |
|------|---------------|
| Terraform | Proxmox VMs and LXCs, K3s bootstrap, DNS records |
| Ansible | OS-level configuration, package installation, ongoing drift correction |
| Flux | K3s workload lifecycle |
| Renovate | Dependency version automation |

### Terraform providers
- `bpg/proxmox` — Proxmox VE provider for VM/LXC provisioning
- `cloudflare/cloudflare` — DNS record management

### Suggested Git repo structure
```
homelab/
├── terraform/
│   ├── proxmox/          # VM and LXC definitions
│   ├── k3s/              # K3s VM bootstrap
│   └── dns/              # Cloudflare DNS records
├── ansible/
│   ├── inventory/        # Node inventory
│   ├── roles/            # Reusable roles
│   └── playbooks/        # Node configuration playbooks
├── k8s/                  # Flux-managed manifests
│   ├── flux-system/      # Flux bootstrap output
│   ├── infrastructure/   # MetalLB, Traefik, Authentik, cert-manager
│   └── apps/             # Application workloads
│       ├── outline/
│       ├── immich/
│       ├── jellyfin/
│       └── ...
└── docs/                 # This document and architecture diagrams
```

---

## Build sequence (phased)

### Phase 1 — Design (complete)
All design decisions documented. Network topology, IP plan, VLAN layout, storage layout, service placement finalised before touching hardware.

### Phase 2 — Hardware prep (one evening)
- Purchase GL.iNet GL-MT2500 (~€50)
- Connect GL-MT2500 WAN to KPN router
- Connect downstairs dumb switch to GL-MT2500 LAN 1
- Connect upstairs cable to GL-MT2500 LAN 2
- Configure Tailscale on GL-MT2500 as subnet router
- Configure basic firewall rules
- Verify all devices reachable on new subnets

### Phase 3 — Synology cleanup (one evening)
- Delete orphaned iSCSI LUN
- Consolidate volumes per new layout
- Configure NFS exports for each shared folder

### Phase 4 — Proxmox (one weekend)
- Fresh install all three nodes
- Form cluster
- Configure VLAN-aware bridges
- Set up PBS
- Terraform provider configured and tested

### Phase 5 — Must-run tier (one weekend)
- LINSTOR/DRBD setup on skadi + sigyn
- Factorio LXC + SFTPGo
- Teamspeak LXC + PostgreSQL
- Tailscale LXCs (×3)
- Pi-hole LXCs (×3) + keepalived VIP
- Zabbix LXC
- PBS LXC
- Proxmox HA configured for all must-run LXCs

### Phase 6 — K3s cluster (2–3 weekends)
- Bootstrap 6 VMs via Terraform
- Install K3s with embedded etcd (HA control plane)
- Flux bootstrap pointing at GitHub repo
- MetalLB, Traefik, cert-manager, Authentik deployed via Flux
- democratic-csi connected to Synology NFS
- First app deployed via GitOps (Homepage as smoke test)

### Phase 7 — Services (ongoing)
- Deploy workloads one at a time via GitOps
- Each service is a learning exercise
- Immich first (most impactful for family use case)

### Phase 8 — Observability (after Phase 7 is stable)
- Zabbix agents on all nodes, VMs, LXCs
- Prometheus + Grafana inside K3s for workload metrics
- Alerting configured

---

## Key decisions log

| Decision | Choice | Reason |
|----------|--------|--------|
| Orchestrator | K3s only (no Swarm) | Single orchestrator reduces operational complexity |
| GitOps tool | Flux CD | Terminal-first, Git-native, aligns with existing Renovate workflow |
| Identity provider | Authentik | Easier user/group management than Authelia, native OIDC, Outline support |
| Database clusters | LXC-based (not in K3s) | Simpler ops, no operator complexity at this scale |
| Router/firewall | GL.iNet GL-MT2500 | OOB access, VLAN routing, Tailscale, firewall — replaces need for managed switch |
| Network switch | Existing dumb switches (×2) | VLAN tags pass transparently — Proxmox/DSM handle tagging in software |
| Homelab subnet | `10.0.x.0/24` per VLAN | Clean separation, VLAN ID = third octet, self-documenting |
| Management VLAN | 254 / `10.0.254.0/24` | Non-default VLAN ID, clearly intentional, enterprise convention |
| KPN router | Untouched | Family devices unchanged, GL-MT2500 connects as single WAN client |
| Personal devices | VLAN 60 / `10.0.60.0/24` | MacBook wired via dock, game PC wired — single cable upstairs via dumb switch |
| Remote access | Tailscale on GL-MT2500 + MacBook | GL-MT2500 survives homelab failure, MacBook uses KPN WiFi + Tailscale when undocked |
| Game PC network | VLAN 60, split-tunnel Tailscale | Full internet via GL-MT2500, homelab access via Tailscale routes only |
| 2.5 GbE upgrade | Not planned | No device in setup supports it — Synology capped at 1 GbE |
| Storage | NFS from Synology | Simplest shared storage, democratic-csi for K3s PVs |
| iSCSI | Removed | Orphaned, adds complexity, NFS covers all use cases |
| Swarm | Removed entirely | Replaced by K3s — no benefit to running both |
| DRBD | Yes, for Factorio + Teamspeak | Sub-30-second recovery requirement not met by Proxmox HA alone |
| Monitoring | Zabbix (infra) + Prometheus/Grafana (K3s) | Zabbix familiar from career, Prometheus native to K8s ecosystem |
| Ansible control plane | AWX (in K3s) | Centralised audit trail, scheduled reconciliation, Vault integration, resume value |
| Configuration drift | AWX scheduled runs (every 30 min) | Continuous reconciliation — same model as Flux but for infrastructure layer |
| Human identity | Named personal user, SSH key only, no root SSH | Audit trail, production discipline, good habit |
| Service identity | `ansible` user for AWX, Proxmox API token for Terraform | Traceable — automated vs human changes distinguishable in logs |
| Secrets management | Vault (self-hosted in K3s) + AWS KMS auto-unseal | Production pattern, resume value, ~$1/month |
| K8s secrets delivery | External Secrets Operator → Vault | Industry standard, decouples secrets from manifests |
| Bootstrap secrets | Sealed Secrets (one-time) + Ansible Vault | Resolves chicken-and-egg without external dependency |
| Repo visibility | Public | Secrets never in Git — safe to share, better for portfolio |
| AWS account | Existing account, eu-west-1 | Replaced mystery Secrets Manager charge with intentional KMS key |

---

## Open questions / to be decided

- [ ] Purchase GL.iNet GL-MT2500 and configure before Proxmox rebuild
- [ ] Assign MacBook a static IP on VLAN 60 for management firewall rule scoping
- [ ] Decide on Factorio server version pinning strategy (mods can break on updates)
- [ ] Homeassistant: confirm purely IP-based integrations (Ring, GPS phone) before K3s deployment
- [ ] Arr stack: confirm which services to bring back (Sonarr confirmed, Radarr TBD)
- [ ] Family photo storage: confirm Immich as the choice, plan migration from Google Photos
- [ ] Cloudflare Tunnel: decide which apps get external exposure vs internal-only
- [ ] Create AWS KMS key in eu-west-1, create vault-unseal IAM user, store credentials in Ansible Vault
- [ ] Vault Helm chart version — pin before deploying
- [ ] Decide on Vault storage backend (Integrated Storage / Raft is recommended for K3s)
- [ ] Configure Tailscale on GL-MT2500 to advertise `10.0.0.0/8`
- [ ] Configure split-tunnel Tailscale on game PC (homelab routes only, gaming goes direct)

---

*This document is a living reference. Update it as decisions change during the build.*
