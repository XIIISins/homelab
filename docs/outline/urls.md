<!-- docs/outline/urls.md -->

# URLs

Every hostname worth knowing, what it is, how you sign in, and where it is reachable from. Three DNS zones decide reachability:

- `xiiisins.com`: public, resolved by Cloudflare. Reached through the tunnel from anywhere.
- `midgard.xiiisins.com`: internal alias of a public service, for LAN and tailnet clients.
- `niflheim.xiiisins.com`: internal only. Resolves on the LAN and tailnet and nowhere else.

"Internal" means on the home network or connected through Tailscale.

---

## For everyone

| Address | What | Sign-in | Reach |
|---|---|---|---|
| `wiki.xiiisins.com` | This wiki (Outline) | Authentik (OIDC) | Public. Internal aliases: `wiki.midgard.…`, `wiki.niflheim.…` |
| `immich.xiiisins.com` | Photos and videos | Authentik (OIDC) | Public. Internal alias: `immich.midgard.…` |
| `paste.xiiisins.com` | Pastebin and file share | None to create or view; `/list` and `/admin` use Authentik | Public |
| `home.xiiisins.com` | Startpage | None | Public |
| `jellyfin.midgard.xiiisins.com` | Media server | Jellyfin accounts | Internal only (LAN or Tailscale) |
| `ts3.xiiisins.com` | Teamspeak (DNS SRV, not a web page) | Teamspeak identity | Public, UDP 9987 |
| `factorio.xiiisins.com` | Factorio game server | Game client | Public, UDP 34197 |
| `authentik.xiiisins.com` | Authentik sign-in portal | Authentik | Public. Internal alias: `authentik.midgard.…` |

## Operator tools

| Address | What | Sign-in |
|---|---|---|
| `overview.niflheim.xiiisins.com` (also `status.xiiisins.com`) | Status page and app launcher | None (internal only) |
| `vault.niflheim.xiiisins.com` | Vault UI | Authentik (OIDC) |
| `netbox.niflheim.xiiisins.com` | IPAM/DCIM | Authentik (OIDC); local superuser break-glass |
| `semaphore.niflheim.xiiisins.com` | Ansible scheduler | Authentik (OIDC); local admin break-glass |
| `hugin.xiiisins.com` | Zabbix (public); `hugin.midgard.…` internal | Authentik (SAML); local `Admin` |
| `hugin-direct.niflheim.xiiisins.com` | Zabbix backdoor, straight to the LXC | Local `Admin` only |
| `metric.niflheim.xiiisins.com` | VictoriaMetrics UI | Authentik (ForwardAuth) |
| `logs.niflheim.xiiisins.com` | VictoriaLogs UI (`/insert` is open to log shippers) | Authentik (ForwardAuth) |
| `sonarr.niflheim.xiiisins.com` | Sonarr | Authentik (ForwardAuth, `media-admins`) |
| `sabnzbd.niflheim.xiiisins.com` | SABnzbd | Authentik (ForwardAuth, `media-admins`) |
| `jellyfin-direct.niflheim.xiiisins.com` | Jellyfin without Traefik | Jellyfin accounts |
| `smoketest.niflheim.xiiisins.com` | Returns "smoketest ok": proves DNS, Traefik and a backend work | None |
| `logs-read.` / `metrics-read.niflheim.xiiisins.com` | Read-only query routes for the AIOps Toolbelt | IP allowlist to the control node only |
| `adguard.niflheim.xiiisins.com` / `adguard-vip.…` | AdGuard admin (Saga) / the VIP | AdGuard admin account |

## Hosts (no web page unless noted)

| Address | What |
|---|---|
| `urd.`, `verd.`, `skuld.niflheim.xiiisins.com` | Proxmox nodes (web UI on the hypervisor port) |
| `munin.niflheim.xiiisins.com` | Synology DSM |
| `pbs.niflheim.xiiisins.com` | Proxmox Backup Server |
| `saga.`, `mimir.`, `kvasir.niflheim.xiiisins.com` | AdGuard nodes |
| `fulla.`, `vor.`, `idunn.niflheim.xiiisins.com` | Postgres nodes |
| `hlin.`, `eir.`, `snotra.niflheim.xiiisins.com` | HAProxy and etcd nodes |
| `pg17-vip.niflheim.xiiisins.com` | The Postgres VIP |
| `gondul.`, `hlokk.`, `sigrun.niflheim.xiiisins.com` | K3s control planes |
| `einherjar-urd.`, `-verd.`, `-skuld.niflheim.xiiisins.com` | K3s workers |
| `hermod.niflheim.xiiisins.com` | Notification hub (API, internal) |
| `gna.`, `ratatoskr.`, `frigg.niflheim.xiiisins.com` | AIOps agent host, the Discord bot, the control node |
| `do1.xiiisins.com` | The offsite DigitalOcean node (Teamspeak failover, outside watcher) |

---

## Rules of thumb

- If a name ends in `niflheim`, it is internal-only; it is not on the internet.
- If it has a `midgard` twin, the twin is the same service reached without leaving the house.
- A sign-in problem on an Authentik-gated page is almost always group membership, not a wrong password.
- Jellyfin and the tools above are reachable remotely by joining the tailnet.

---

## See also

- **Services and purpose**: what each service is for.
- **Edge** (Components): how names reach services.
- **Troubleshooting**: when a name does not work.
