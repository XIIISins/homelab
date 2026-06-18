<!-- docs/outline/services-and-purpose.md -->

# Services and purpose

What the homelab actually runs, and who each thing is for. The other sections describe the machinery; this one is the catalog — open it when you want to know "what does this homelab *do*," or which service to reach for.

*Every service traces back to one of two audiences: the people the homelab serves, or the operator who keeps it running. If a service can't be placed in one of those, it probably belongs in **Components & interactions** as plumbing, not here.*

---

## For friends and family

The point of the whole thing. Services that exist so people other than the operator get something useful.

| Service | What it's for | Where it runs | Access |
|---|---|---|---|
| **Jellyfin** | Movies, TV, music streaming | Privileged LXC on Urd | `jellyfin.midgard.xiiisins.com` *(planned)* |
| **TeamSpeak** | Voice chat for gaming and hanging out | asgard K3s | `ts3.xiiisins.com` (SRV) |
| **Factorio** | Multiplayer game server | LXC 1120 | UDP game port + SFTP for admins |
| **Outline** | Shared wiki / knowledge base | asgard K3s | `wiki.xiiisins.com` |
| **Startpage** | Landing / dashboard page | asgard K3s | `home.xiiisins.com` |
| **MicroBin** | Pastebin / quick file-share | asgard K3s | `paste.xiiisins.com` |
| **Immich** *(planned)* | Photo and video library | asgard K3s | — |

---

## Operator tooling

Services the operator uses to run, observe, and reason about the homelab. Not secret, just not interesting to the household.

| Service | What it's for | Where it runs | Access |
|---|---|---|---|
| **NetBox** | IPAM / DCIM — source of truth for IPs, VLANs, devices, VMs | asgard K3s | `netbox.niflheim.xiiisins.com` |
| **Semaphore** | Ansible orchestration + fleet drift-check | asgard K3s | `semaphore.niflheim.xiiisins.com` |
| **n8n** | Workflow automation | asgard K3s | `n8n.xiiisins.com` |
| **Zabbix** (Hugin) | Host- and LXC-level monitoring | LXC 1102 (Hugin) on Urd | `hugin.xiiisins.com` |
| **Hermod** | Notification hub — fans alerts to Discord | LXC 1103 on Verd | internal POST endpoint |
| **Vault** | Machine secrets + human secret lookup | asgard K3s | `vault.niflheim.xiiisins.com` → **Identity & secrets** |
| **vmui / VictoriaLogs UI** | In-cluster metrics + logs | asgard K3s | `metric.` / `logs.niflheim.xiiisins.com` → **Observability** |

---

## Platform services

The layer everything else stands on. These are documented in depth under **Components & interactions** and **Hardware**; they appear here as catalog rows so the directory is complete, with a pointer to the real page.

| Service | What it's for | Where it runs | Deep page |
|---|---|---|---|
| **Authentik** | Identity provider — OIDC, LDAP, SAML | asgard K3s | **Identity & secrets** |
| **PostgreSQL** (Patroni) | Relational database for nearly every app | Fulla / Vör / Idunn LXCs | **Storage & data** |
| **Garage** | S3-compatible object storage | asgard K3s | **Storage & data** |
| **Munin** (Synology) | iSCSI block + NFS file storage | DS223J NAS | **Hardware → Storage** |
| **AdGuard Home** | DNS resolution + rewrites | Saga / Mimir / Kvasir LXCs | **Network** |
| **PBS** | Proxmox VM/LXC backups | LXC 1101 on Skuld | **Storage & data** |

---

## Planned

Services that are decided but not yet deployed. Listed here so the catalog reflects intent, not just current state.

- **Jellyfin** — designed (privileged LXC on Urd, QuickSync passthrough); deployment pending. Has its own page already.
- **Immich** — self-hosted photo/video library for the household. Will reuse Garage (S3) + Postgres, same shape as Outline.

Startpage, MicroBin (which took the slot the originally-planned Privatebin would have), and n8n have since gone live and moved into the catalog above. Dedicated subpages for them are a follow-up; the parent catalog rows cover them for now.

---

## Where to go deeper

Each family-facing and operator-tooling service with its own page:

- **Jellyfin** — media server, QuickSync, why it's an LXC.
- **TeamSpeak** — voice server, the SRV failover ring.
- **Factorio** — game server, the SFTP self-service + reconcile-loop pattern.
- **Outline** — the wiki you're reading, its Postgres + Garage + OIDC wiring.
- **NetBox** — IPAM/DCIM truth and the Terraform standing pattern.
- **Semaphore** — Ansible scheduler and the drift-check loop.
- **Zabbix** — host monitoring in its own failure domain.
- **Hermod** — the notification hub and its Discord tag taxonomy.

---

## See also

- **Components & interactions** — the platform services (Authentik, Postgres, Garage, etc.) and how every service wires together.
- **URLs** — the flat directory of every hostname and how to log in.
- **Troubleshooting** — when one of these services is misbehaving.
