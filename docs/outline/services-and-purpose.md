<!-- docs/outline/services-and-purpose.md -->

# Services and purpose

What runs on the homelab, who it is for, and where to read more. Services are grouped by audience: the things friends and family use, the tools the operator uses, and the platform pieces everything else stands on. Where a service has its own page, it is linked; platform pieces are described here and in depth under **Components & interactions** and **Hardware**.

---

## For friends and family

| Service | What it is | Address | Access |
|---|---|---|---|
| **Jellyfin** | Media server with hardware transcoding, fed by the media-automation pipeline | `jellyfin.midgard.xiiisins.com` | Internal and Tailscale only; deliberately not on the internet |
| **Teamspeak** | Voice server | `ts3.xiiisins.com` (DNS SRV) | Public UDP port-forward, with an offsite failover |
| **Factorio** | Game server, managed by its operator over SFTP | `factorio.xiiisins.com` | Public game port; SFTP for the operator |
| **Immich** | Photo and video library (a secondary copy) | `immich.xiiisins.com` | Public through Cloudflare, Authentik OIDC |
| **Outline** | The wiki: how-tos and living notes (this page lives there) | `wiki.xiiisins.com` | Public through Cloudflare, Authentik OIDC |
| **MicroBin** | Pastebin and file sharing | `paste.xiiisins.com` | Public; anonymous create and view, janitor routes gated |
| **Startpage** | Browser homepage | `home.xiiisins.com` | Public, static |

## Operator tooling

| Service | What it is | Address |
|---|---|---|
| **Overview page** | One-screen status page and app launcher | `overview.niflheim.xiiisins.com` |
| **Media automation** | Sonarr, SABnzbd and Recyclarr, filling the Jellyfin library | `sonarr.` / `sabnzbd.niflheim.xiiisins.com` |
| **NetBox** | IPAM/DCIM, and the Ansible inventory source | `netbox.niflheim.xiiisins.com` |
| **Semaphore** | Ansible scheduler and drift-check | `semaphore.niflheim.xiiisins.com` |
| **Zabbix** | Host and LXC monitoring | `hugin.xiiisins.com` |
| **Hermod** | Notification hub to Discord | `hermod.niflheim.xiiisins.com` (internal, API only) |
| **AIOps agent** | Diagnosis, approvals and healing (Gná and Ratatoskr) | Discord; no web UI |
| VictoriaMetrics UI, VictoriaLogs UI | Metrics and log search | `metric.` / `logs.niflheim.xiiisins.com` |
| Vault UI | Secret lookup | `vault.niflheim.xiiisins.com` |

## Platform

These have no subpage of their own: each is a catalog row here and a deep section elsewhere.

| Service | Role | Deep page |
|---|---|---|
| **Authentik** | Sign-in for every web service | **Identity & secrets** (Components) |
| **HashiCorp Vault** | Runtime secrets | **Identity & secrets** |
| **PostgreSQL (Patroni)** | The database cluster behind most apps | **Storage & data** |
| **Garage** | S3 object storage for Outline | **Storage & data** |
| **AdGuard Home** | DNS for the whole network | **Network** |
| **Munin (Synology)** | NAS: backups, NFS, media | **Storage** (Hardware) |
| **PBS** | VM and LXC backups | **Storage & data** |
| **Traefik, Cloudflared, cert-manager** | Web edge | **Edge** |
| **Tailscale** | Remote access | **Hardware** (Networking) |
| **Frigg** | Control node | **GitOps & automation** |

---

## Tiers of care

| Tier | Meaning | Examples |
|---|---|---|
| **Critical** | Absence felt household-wide within minutes. Runs with active redundancy. | DNS, the firewall, the Postgres VIP |
| **Important** | Noticed within hours. A single node failure should be invisible. | Authentik, Jellyfin, Teamspeak, Hermod, Vault |
| **Best-effort** | An outage is acceptable; the operator notices and fixes. | Factorio, the wiki, observability UIs, anything experimental |

---

## Planned or in progress

Open work is tracked in the build sequence rather than here. At the time of writing: household accounts and remote access for Jellyfin, a restore test for Sonarr's config, and the later stages of the AIOps tiers. A package mirror and a second Kubernetes cluster were both considered and dropped for lack of disk and RAM.

---

## Service pages

- **Jellyfin**, **Media automation**, **Teamspeak**, **Factorio**, **Immich**, **Outline**, **MicroBin**, **Startpage**
- **Overview page**, **NetBox**, **Semaphore**, **Zabbix**, **Hermod**, **AIOps agent**

---

## See also

- **Components & interactions**: how these services connect.
- **URLs**: every hostname and how to sign in.
- **Troubleshooting**: first checks when a service misbehaves.
