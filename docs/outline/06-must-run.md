# Infrastructure — must-run tier

Services with real consequences if down. Boring, stable, minimal complexity. All run as LXCs managed by Proxmox HA.

## LXC inventory

| LXC | Primary node | IP | VLAN | Role |
|-----|-------------|-----|------|------|
| factorio | skadi | `10.0.10.120` | 10 | Factorio game server + SFTPGo |
| teamspeak | sigyn | `10.0.10.121` | 10 | Teamspeak 3 + PostgreSQL |
| tailscale-1 | skadi | `10.0.10.122` | 10 | Tailscale subnet router |
| tailscale-2 | sigyn | `10.0.10.123` | 10 | Tailscale subnet router (redundant) |
| tailscale-3 | sylvi | `10.0.10.124` | 10 | Tailscale exit node |
| pihole-1 | skadi | `10.0.10.125` | 10 | Pi-hole + keepalived |
| pihole-2 | sigyn | `10.0.10.126` | 10 | Pi-hole + keepalived |
| pihole-3 | sylvi | `10.0.10.127` | 10 | Pi-hole + keepalived |
| pbs | sylvi | `10.0.10.128` | 10 | Proxmox Backup Server |
| zabbix | sylvi | `10.0.10.129` | 10 | Zabbix server |

## Factorio

Game server for a friend's ongoing playthrough. SFTPGo runs inside the same LXC and mounts the NFS share from Synology (`/volume1/uploads/factorio`). Friend accesses saves, mods, and config via SFTP on a dedicated port. No shell access granted.

**DRBD:** Factorio save data lives on a DRBD-replicated volume (skadi ↔ sigyn). If skadi dies, Proxmox HA restarts the LXC on sigyn against already-current data. Recovery target: under 30 seconds.

## Teamspeak

Teamspeak 3 voice server. PostgreSQL runs inside the same LXC for the Teamspeak database. External access via `ts3.xiiisins.com` SRV record pointing to home external IP with port forward on GL-MT2500.

**DRBD:** Teamspeak database lives on a DRBD-replicated volume (sigyn ↔ sylvi). Recovery target: under 30 seconds.

## Tailscale

Three LXCs provide redundancy. Two configured as subnet routers advertising `10.0.0.0/8`. One configured as an exit node. Tailscale also runs independently on the GL-MT2500 — survives complete homelab failure.

Tailscale uses Authentik as OIDC provider for user authentication when Authentik is available. Tailscale's own control plane (cloud-hosted) handles coordination independently of the homelab.

## Pi-hole

Three LXCs with keepalived managing a VIP at `10.0.10.110`. Gravity sync keeps blocklists and DNS records in sync across all three instances. The GL-MT2500 uses the VIP as its upstream DNS server for all VLANs.

Custom DNS records in Pi-hole resolve `*.infra.xiiisins.com` and internal `*.app.xiiisins.com` to the appropriate MetalLB or LXC IPs.

## Zabbix

Zabbix server monitors all infrastructure — Proxmox nodes, LXCs, K3s VMs, Synology, GL-MT2500, and network devices. Runs outside K3s deliberately so monitoring survives K3s failures. Uses the existing MariaDB Galera cluster as its database backend.

Zabbix agents run on every managed host. Agent connections use PSK encryption.

## Proxmox Backup Server

Nightly backups of all VMs and LXCs. 7-day retention. Datastore on Synology NFS. Hyper Backup on Synology creates point-in-time snapshots of the PBS datastore for additional recovery options.
