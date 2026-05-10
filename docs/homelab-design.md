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
All three nodes and the Synology currently connect to a dumb switch. A **TP-Link TL-SG108E** (managed gigabit, ~€25) will be purchased to enable VLAN support. The ISP-provided router handles DHCP and internet uplink only — no VLAN awareness required on the router.

### Network speed
All devices are 1 GbE. The Synology DS223J is capped at ~115 MB/s. No 2.5 GbE upgrade planned — nothing in the setup supports it.

---

## Network design

### Subnet
`192.168.2.0/24` — full subnet available. ISP router at `.1`. Homelab uses `.100–.200`.

### VLANs

| VLAN ID | Name | Purpose |
|---------|------|---------|
| 1 | uplink | Main LAN, ISP router, internet access. All VMs/LXCs needing internet get a NIC here. |
| 10 | must-run | Factorio, Teamspeak, Tailscale LXCs |
| 20 | k3s | K3s control plane and worker VMs |
| 30 | storage | NFS traffic between nodes and Synology |

Proxmox nodes connect to the managed switch via trunk ports carrying all VLANs. The switch uplink port to the ISP router is untagged VLAN 1. The Synology connects untagged on VLAN 1 and VLAN 30 (dual NIC not possible — single NIC, so NFS traffic shares VLAN 1).

### How internet access works
VMs and LXCs that need internet get two virtual NICs: one on their tier VLAN, one on VLAN 1. Outbound traffic routes via the VLAN 1 NIC through the ISP router. K3s pods reach the internet via NAT on the worker VM's VLAN 1 NIC — pods use the K3s internal network (`10.42.0.0/16`) and the worker masquerades outbound traffic behind its VLAN 1 IP.

### IP allocation

| Range | Role | Assignments |
|-------|------|-------------|
| `.100–.103` | Physical nodes + NAS | `.100` skadi, `.101` sigyn, `.102` sylvi, `.103` Synology |
| `.110–.119` | VIPs (keepalived / MetalLB) | `.110` HAProxy VIP, `.111` Swarm VIP (legacy — remove), `.112` Pi-hole VIP, `.113–.119` reserved |
| `.120–.134` | LXC containers | See LXC inventory below |
| `.140–.144` | (legacy Swarm VMs — removed in rebuild) | — |
| `.150–.159` | K3s VMs | `.150–.152` control plane, `.153–.155` workers, `.156–.159` spare |
| `.160–.179` | MetalLB LoadBalancer pool | One IP per exposed K3s service |
| `.180–.200` | Unallocated buffer | Scratch space, temp VMs, experiments |

### DNS
Pi-hole runs across 3 LXCs with keepalived VIP at `.112`. Split-horizon DNS:

- `app.xiiisins.com` — user-facing applications (internal + Cloudflare Tunnel for external)
- `infra.xiiisins.com` — infrastructure tooling, internal only, never in public DNS
- `svc.xiiisins.com` — non-HTTP services (Teamspeak SRV, Factorio A record, Wireguard)

Pi-hole resolves `*.infra.xiiisins.com` and internal `*.app.xiiisins.com` to the HAProxy VIP. External `*.app.xiiisins.com` entries go through Cloudflare Tunnel.

TLS: wildcard cert via Let's Encrypt DNS-01 challenge using Cloudflare API token. Traefik manages cert lifecycle.

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
| factorio | skadi | `.120` | Factorio game server + SFTPGo |
| teamspeak | sigyn | `.121` | Teamspeak 3 + PostgreSQL |
| tailscale-1 | skadi | `.122` | Tailscale subnet router |
| tailscale-2 | sigyn | `.123` | Tailscale subnet router (redundant) |
| tailscale-3 | sylvi | `.124` | Tailscale exit node |
| pihole-1 | skadi | `.125` | Pi-hole + keepalived |
| pihole-2 | sigyn | `.126` | Pi-hole + keepalived |
| pihole-3 | sylvi | `.127` | Pi-hole + keepalived |
| pbs | sylvi | `.128` | Proxmox Backup Server |
| zabbix | sylvi | `.129` | Zabbix server (monitors all infrastructure) |

*Note: IPs above are illustrative — finalise against the full IP plan.*

### Factorio SFTP access
SFTPGo runs inside the Factorio LXC. Mounts the NFS share (`/volume-data/uploads/factorio`) for saves, mods, and config. Friend accesses via SFTP on a dedicated port. No shell access granted.

### Tailscale
Three LXCs provide redundancy. At least two configured as subnet routers advertising `192.168.2.0/24`. One configured as an exit node. If one LXC dies Tailscale automatically fails over to another subnet router.

---

## Can-run tier — K3s cluster

Learning environment. Complexity is intentional. Failure here has no consequence.

### Cluster topology
6 VMs total — one control plane VM and one worker VM per physical node, co-located.

| VM | Node | IP | Role | vCPU | RAM |
|----|------|-----|------|------|-----|
| k3s-cp-1 | skadi | `.150` | Control plane + etcd | 2 | 2 GB |
| k3s-cp-2 | sigyn | `.151` | Control plane + etcd | 2 | 2 GB |
| k3s-cp-3 | sylvi | `.152` | Control plane + etcd | 2 | 2 GB |
| k3s-wk-1 | skadi | `.153` | Worker (heavy workloads) | 2 | 8 GB |
| k3s-wk-2 | sigyn | `.154` | Worker | 2 | 4 GB |
| k3s-wk-3 | sylvi | `.155` | Worker | 2 | 3 GB |

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
- Purchase TP-Link TL-SG108E
- Configure VLANs on switch
- Verify trunk ports to nodes work correctly

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
| Network switch | TP-Link TL-SG108E (managed gigabit) | VLANs without requiring smart router, ~€25 |
| 2.5 GbE upgrade | Not planned | No device in setup supports it — Synology capped at 1 GbE |
| Storage | NFS from Synology | Simplest shared storage, democratic-csi for K3s PVs |
| iSCSI | Removed | Orphaned, adds complexity, NFS covers all use cases |
| Swarm | Removed entirely | Replaced by K3s — no benefit to running both |
| DRBD | Yes, for Factorio + Teamspeak | Sub-30-second recovery requirement not met by Proxmox HA alone |
| Monitoring | Zabbix (infra) + Prometheus/Grafana (K3s) | Zabbix familiar from career, Prometheus native to K8s ecosystem |
| VM memory | Fixed allocation (no ballooning) | Predictable, no surprises under load |

---

## Open questions / to be decided

- [ ] Confirm exact VLAN IDs and switch port assignments once switch arrives
- [ ] Decide on Factorio server version pinning strategy (mods can break on updates)
- [ ] Homeassistant: confirm purely IP-based integrations (Ring, GPS phone) before K3s deployment
- [ ] Wireguard: evaluate whether to bring back alongside Tailscale or drop permanently
- [ ] Arr stack: confirm which services to bring back (Sonarr confirmed, Radarr TBD)
- [ ] Family photo storage: confirm Immich as the choice, plan migration from Google Photos
- [ ] Cloudflare Tunnel: decide which apps get external exposure vs internal-only
- [ ] Proxmox node naming: finalise or change Norse theme

---

*This document is a living reference. Update it as decisions change during the build.*
