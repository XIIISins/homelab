# Services — must-run

Detailed notes on each must-run service. For LXC inventory and IPs see [Infrastructure — must-run tier](./06-must-run.md).

## Factorio

**Purpose:** Game server for friend's ongoing playthrough.

**Ports:**
- UDP 34197 — game traffic (port forward on GL-MT2500)
- TCP 27015 — RCON (internal only)
- TCP 22022 — SFTPGo (port forward on GL-MT2500 for friend access)

**Storage:** Save files and mods on Synology NFS (`/volume1/uploads/factorio`). DRBD-replicated local volume for runtime data.

**Friend access:** SFTPGo provides SFTP access to the NFS share. Friend uses SFTP client (FileZilla, WinSCP, etc.) to upload mods and download saves. No shell access. Credentials managed separately from homelab identity (dedicated SFTPGo user, not Authentik).

**Version pinning:** Factorio server version pinned to match client version in use. Update only when friend updates their client. Managed via Renovate watching the Docker image tag.

**DNS:** `factorio.svc.xiiisins.com` A record pointing to home external IP.

## Teamspeak 3

**Purpose:** Voice communications for airsoft team.

**Ports:**
- UDP 9987 — voice (port forward on GL-MT2500)
- TCP 10011 — ServerQuery (internal only)
- TCP 30033 — file transfer (port forward if needed)

**Storage:** PostgreSQL database in same LXC. DRBD-replicated. Backups via PBS nightly.

**DNS:** `ts3.xiiisins.com` SRV record:
```
_ts3._udp.xiiisins.com. 300 IN SRV 10 5 9987 ts3.xiiisins.com.
ts3.xiiisins.com. 300 IN A <home-external-ip>
```

## Tailscale

**Purpose:** Remote access to home network from anywhere.

**Configuration:**
- Tailscale-1 and Tailscale-2: subnet routers advertising `10.0.0.0/8`
- Tailscale-3: exit node (route all internet traffic through home connection when needed)
- GL-MT2500: independent subnet router (OOB — survives homelab failure)

**Authentication:** Authentik OIDC. Users authenticate via Authentik to get Tailscale access.

**Split tunneling on game PC:** Only `10.0.0.0/8` routes through Tailscale. Gaming traffic goes direct to KPN router.

## Pi-hole

**Purpose:** Network-wide DNS with ad blocking. Internal DNS resolution for homelab hostnames.

**Configuration:**
- Three instances synced via Gravity Sync
- Keepalived VIP at `10.0.10.110` — GL-MT2500 uses this as upstream DNS
- Custom DNS records for all `*.infra.xiiisins.com` and internal `*.app.xiiisins.com`
- Upstream DNS: Cloudflare 1.1.1.1 / 1.0.0.1 (DoT)

## Zabbix

**Purpose:** Infrastructure monitoring. Monitors everything outside K3s.

**Monitored hosts:**
- skadi, sigyn, sylvi (Proxmox nodes)
- All must-run LXCs
- All K3s VMs (OS level — not pod metrics, that's Prometheus)
- Synology DS223J (native Zabbix agent)
- GL-MT2500 (SNMP)

**Database:** MariaDB Galera LXC cluster.

**Alerting:** Configured to alert via Discord webhook (same channel used by Hyper Backup notifications).

**Agent auth:** PSK encryption on all agent connections.

## Proxmox Backup Server

**Purpose:** Nightly backups of all VMs and LXCs.

**Schedule:** 02:00 nightly, all hosts.

**Retention:** 7 daily backups. Hyper Backup on Synology provides additional point-in-time recovery of the PBS datastore itself.

**Datastore:** Synology NFS `/volume1/proxmox-backup`.
