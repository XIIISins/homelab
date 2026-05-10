# Network — VLAN design

## Convention

VLAN ID matches the third octet of the subnet. Seeing `10.0.20.151` in a log immediately tells you it's a K3s VM — no lookup needed. Management is on VLAN 254 following enterprise convention of using a non-default VLAN ID for the management plane.

## VLAN table

| VLAN ID | Subnet | Name | Purpose |
|---------|--------|------|---------|
| 10 | `10.0.10.0/24` | must-run | Must-run LXCs |
| 20 | `10.0.20.0/24` | k3s | K3s control plane and worker VMs |
| 30 | `10.0.30.0/24` | storage | NFS traffic between nodes and Synology |
| 40 | `10.0.40.0/24` | services | MetalLB LoadBalancer pool |
| 60 | `10.0.60.0/24` | personal | MacBook (wired) + game PC |
| 254 | `10.0.254.0/24` | management | GL-MT2500, Proxmox nodes, Synology |

## IP assignments

### Management (VLAN 254 — `10.0.254.0/24`)

| Address | Role |
|---------|------|
| `10.0.254.1` | GL-MT2500 (default gateway for all VLANs) |
| `10.0.254.100` | skadi (Celeron N5095) |
| `10.0.254.101` | sigyn (N100) |
| `10.0.254.102` | sylvi (N100) |
| `10.0.254.103` | Synology DS223J |

### Must-run (VLAN 10 — `10.0.10.0/24`)

| Address | Role |
|---------|------|
| `10.0.10.110` | Pi-hole keepalived VIP |
| `10.0.10.120` | Factorio LXC |
| `10.0.10.121` | Teamspeak LXC |
| `10.0.10.122` | Tailscale LXC 1 (subnet router) |
| `10.0.10.123` | Tailscale LXC 2 (subnet router) |
| `10.0.10.124` | Tailscale LXC 3 (exit node) |
| `10.0.10.125` | Pi-hole LXC 1 (skadi) |
| `10.0.10.126` | Pi-hole LXC 2 (sigyn) |
| `10.0.10.127` | Pi-hole LXC 3 (sylvi) |
| `10.0.10.128` | PBS LXC |
| `10.0.10.129` | Zabbix LXC |

### K3s (VLAN 20 — `10.0.20.0/24`)

| Address | Role |
|---------|------|
| `10.0.20.150` | K3s control plane 1 (skadi) |
| `10.0.20.151` | K3s control plane 2 (sigyn) |
| `10.0.20.152` | K3s control plane 3 (sylvi) |
| `10.0.20.153` | K3s worker 1 (skadi) |
| `10.0.20.154` | K3s worker 2 (sigyn) |
| `10.0.20.155` | K3s worker 3 (sylvi) |

### Storage (VLAN 30 — `10.0.30.0/24`)

NFS traffic only. No static assignments needed — Proxmox nodes use their management IP for NFS mounts to Synology. Storage VLAN is isolated.

### Services (VLAN 40 — `10.0.40.0/24`)

| Range | Role |
|-------|------|
| `10.0.40.160–.179` | MetalLB LoadBalancer pool (one IP per exposed K3s service) |

### Personal (VLAN 60 — `10.0.60.0/24`)

| Address | Role |
|---------|------|
| `10.0.60.10` | MacBook (static, used for management firewall rule scoping) |
| `10.0.60.x` | Game PC (DHCP) |

## DNS

Pi-hole runs across 3 LXCs with keepalived VIP at `10.0.10.110`. The GL-MT2500 uses the Pi-hole VIP as its upstream DNS server for all VLANs.

### Naming convention

| Zone | Purpose | Resolves to |
|------|---------|-------------|
| `app.xiiisins.com` | User-facing apps | MetalLB IP internally, Cloudflare Tunnel externally |
| `infra.xiiisins.com` | Infrastructure tooling | Internal only, never in public Cloudflare DNS |
| `svc.xiiisins.com` | Non-HTTP services | Direct DNS records (Teamspeak SRV, Factorio A) |

### TLS

Wildcard certificates via Let's Encrypt DNS-01 challenge using a scoped Cloudflare API token. cert-manager in K3s manages the lifecycle. Covers `*.app.xiiisins.com` and `*.infra.xiiisins.com`.
