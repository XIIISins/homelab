<!-- docs/outline/urls.md -->

# URLs

The flat directory — every hostname, what it is, and how to log in. When you just want the link, this is the page. For what a service actually *does*, see **Services and purpose**.

A quick orientation on the three DNS zones, because the hostname tells you how to reach a thing:

- **`xiiisins.com`** (apex) — externally reachable, via the Cloudflare tunnel. Works from anywhere.
- **`midgard.xiiisins.com`** — internal alias for the same publicly-reachable services; resolved on the LAN by AdGuard straight to Traefik.
- **`niflheim.xiiisins.com`** — internal-only. LAN (or tailnet) access exclusively; the firewall blocks it from outside.

---

## Web apps — external (apex)

Reachable from anywhere, through the Cloudflare tunnel.

| URL | Service | Sign in with |
|---|---|---|
| `wiki.xiiisins.com` | Outline (this wiki) | Authentik OIDC (`outline-users`) |
| `authentik.xiiisins.com` | Authentik | Authentik (it *is* the IdP) |
| `hugin.xiiisins.com` | Zabbix | Authentik SAML (`zabbix-admins`) |
| `n8n.xiiisins.com` | n8n — webhook/form triggers only | None (n8n authenticates webhooks per workflow) |
| `paste.xiiisins.com` | MicroBin (pastebin / file-share) | Anonymous; janitor `/list` + `/admin` via Authentik ForwardAuth |
| `home.xiiisins.com` | Startpage (landing dashboard) | None — public landing page |

---

## Web apps — internal

LAN / tailnet only. The `midgard` aliases mirror the apex services; the `niflheim` names are internal-only tooling.

| URL | Service | Sign in with |
|---|---|---|
| `wiki.midgard.xiiisins.com` | Outline | Authentik OIDC |
| `authentik.midgard.xiiisins.com` | Authentik | Authentik |
| `n8n.niflheim.xiiisins.com` | n8n — full editor + API | Authentik ForwardAuth (`n8n-admins`) |
| `n8n.midgard.xiiisins.com` | n8n — webhook/form triggers only | None |
| `hugin.midgard.xiiisins.com` | Zabbix | Authentik SAML |
| `jellyfin.midgard.xiiisins.com` | Jellyfin *(planned)* | Local Jellyfin account |
| `vault.niflheim.xiiisins.com` | Vault UI | Authentik OIDC |
| `netbox.niflheim.xiiisins.com` | NetBox | Authentik OIDC |
| `semaphore.niflheim.xiiisins.com` | Semaphore | Authentik OIDC |
| `metric.niflheim.xiiisins.com` | vmui (VictoriaMetrics) | Authentik ForwardAuth |
| `logs.niflheim.xiiisins.com` | VictoriaLogs UI | Authentik ForwardAuth |
| `hugin-direct.niflheim.xiiisins.com` | Zabbix (Traefik-bypass backdoor) | Local Zabbix admin |

> Most apps also have a **break-glass local admin** for when Authentik is unavailable. Those credentials live in 1Password, not here.

---

## Connection endpoints (not web)

| Endpoint | Service | Notes |
|---|---|---|
| `_ts3._udp.ts3.xiiisins.com` (SRV) | TeamSpeak | Resolves to `hel-ts3` (homelab) then `do-ts3` (fallback). UDP 9987 voice, TCP 30033 file transfer. |
| `factorio.xiiisins.com` | Factorio | UDP game port (external via UCG port-forward); SFTP for admins (no shell). |
| `10.0.10.200:53` | AdGuard Home VIP | The DNS resolver every LAN client points at. |

---

## Admin & infrastructure consoles

Not behind Authentik — these are the underlying platform, with their own local credentials (in 1Password, outside the Homelab sub-vault for the non-homelab ones).

| Address | Console | Credential home |
|---|---|---|
| `10.0.254.1` | UCG-Ultra (UniFi) | 1Password |
| `10.0.254.11:8006` | Proxmox — Urd | 1Password |
| `10.0.254.12:8006` | Proxmox — Verd | 1Password |
| `10.0.254.13:8006` | Proxmox — Skuld | 1Password |
| `10.0.254.20` | Synology DSM (Munin) | 1Password |
| `10.0.11.20` | Proxmox Backup Server | 1Password |
| `saga.niflheim.xiiisins.com` | AdGuard Home admin (origin) | 1Password |

---

## Internal service VIPs

Not user-facing — listed so a hostname or IP in a config or a log is recognisable.

| VIP | Service | Notes |
|---|---|---|
| `10.0.20.10` | Traefik | Internal ingress; everything `*.niflheim` / `*.midgard` lands here. |
| `10.0.10.210` | Patroni HAProxy | The Postgres entry point for every database consumer. |
| `10.0.20.12` | TeamSpeak | Shared voice + file-transfer VIP. |
| `garage-s3.garage.svc.cluster.local:3900` | Garage S3 | In-cluster only; no external endpoint by design. |

---

## See also

- **Services and purpose** — what each of these services is for.
- **Network** (Components) — the full key-IP table and the DNS-zone model behind these hostnames.
- **Identity & secrets** (Components) — where the credentials behind these logins live.
