# Services — can-run (K3s)

All workloads deployed via Flux GitOps. Versions pinned, updated by Renovate PRs. All web apps authenticate via Authentik OIDC. All persistent storage via democratic-csi NFS PVs from Synology.

## Core infrastructure (deployed first)

| Service | Namespace | Purpose |
|---------|-----------|---------|
| Authentik | `authentik` | Identity provider — OIDC + LDAP |
| Vault | `vault` | Secrets management |
| External Secrets Operator | `external-secrets` | Pulls secrets from Vault |
| cert-manager | `cert-manager` | TLS certificates via Let's Encrypt |
| Sealed Secrets | `sealed-secrets` | Bootstrap credential only |
| MetalLB | `metallb-system` | LoadBalancer IPs |
| Traefik | `traefik` | Ingress controller |
| AWX | `awx` | Ansible control plane |
| Prometheus | `monitoring` | Metrics collection |
| Grafana | `monitoring` | Metrics visualisation |

## Applications

### Authentik
Identity provider for everything. LDAP provider for SSH access via SSSD. OIDC provider for all web apps. PostgreSQL backend. Deployed before any other app.

URL: `auth.app.xiiisins.com`

### Outline
Team wiki and documentation. OIDC via Authentik. PostgreSQL backend. Used for homelab documentation, notes, runbooks.

URL: `wiki.app.xiiisins.com`

### Immich
Family photo and video backup. Self-hosted Google Photos alternative. Facial recognition, automatic albums, mobile app for backup. PostgreSQL backend. NFS storage at `/volume2/immich`.

URL: `photos.app.xiiisins.com` (external via Cloudflare Tunnel)

### n8n
Workflow automation. Replaces manual scripts for things like backup notifications, monitoring alerts to Discord, etc. PostgreSQL backend.

URL: `n8n.app.xiiisins.com` (internal only)

### Jellyfin
Media streaming. NFS mounts for `/volume2/media`. No hardware transcoding (no GPU). Internal and personal remote access only (not exposed publicly via Cloudflare Tunnel).

URL: `jellyfin.app.xiiisins.com` (internal + personal Tailscale only)

### Homepage
Homelab dashboard. Shows status of all services, Proxmox resource usage, NAS capacity, Zabbix alerts. Docker socket + K8s API for auto-discovery.

URL: `home.infra.xiiisins.com` (internal only)

### Komga
Manga and comic reader. NFS mount for `/volume2/manga`.

URL: `manga.app.xiiisins.com` (internal only)

### Startpage
Custom new tab page with quick links. Stateless — custom Docker image on Docker Hub.

URL: `start.app.xiiisins.com` (internal only)

### Wallpaper gallery
Internal image gallery for wallpaper collection. NFS mount for `/volume2/wallpapers`.

URL: `wallpapers.app.xiiisins.com` (internal only)

### phpIPAM
IP address management. Documents the homelab IP plan. MariaDB Galera backend.

URL: `ipam.infra.xiiisins.com` (internal only)

### Privatebin
Self-hosted pastebin. Stateless — no database required.

URL: `paste.app.xiiisins.com` (internal + external via Cloudflare Tunnel)

## External exposure via Cloudflare Tunnel

Services exposed externally through Cloudflare Tunnel — no port forwarding on KPN router:

| Service | External URL | Auth |
|---------|-------------|------|
| Immich | `photos.app.xiiisins.com` | Authentik OIDC |
| Outline | `wiki.app.xiiisins.com` | Authentik OIDC |
| Privatebin | `paste.app.xiiisins.com` | None (public pastebin) |

Everything else is internal only — reachable via Tailscale when remote.
