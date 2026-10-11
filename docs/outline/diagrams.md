<!-- docs/outline/diagrams.md -->

# Diagrams

Pictures of the homelab. The first page shows the whole lab on one sheet; the pages that follow zoom into a single component or a single flow. Each diagram page carries the picture, a short guide to reading it, and the editable source.

---

## The diagrams

### The whole lab

| Page | Shows |
|---|---|
| **Overview diagram** | The whole lab: the edge, the three Proxmox hosts and what runs on each, the asgard Kubernetes cluster, the storage tiers and the off-site services. |

### Platform and infrastructure

| Page | Shows |
|---|---|
| **Asgard K3s diagram** | The six Kubernetes VMs, the Flux deploy chain, the platform components and the apps. |
| **Asgard LXCs diagram** | Every LXC outside Kubernetes and the Proxmox host it runs on. |
| **AdGuard diagram** | DNS for the whole lab: one address, three containers, and how a query is answered. |
| **PostgreSQL diagram** | Patroni high availability behind one address: HAProxy, etcd and the three database nodes. |
| **Synology (Munin) diagram** | The NAS volumes and who uses each, and why storage is tiered. |
| **Observability diagram** | VictoriaMetrics and VictoriaLogs: the shippers, the stores and who looks. |
| **Notifications diagram** | Hermod: producers, tags and the Discord channels they reach. |

### Services

| Page | Shows |
|---|---|
| **Overview page diagram** | The status page and the two read-only APIs its Caddy proxies. |
| **Outline diagram** | This wiki: three hostnames, Postgres, Redis, Garage and Authentik behind it. |
| **Garage diagram** | The in-cluster S3 store and its two storage tiers. |
| **NetBox diagram** | The queryable view of the lab: who writes it and who reads it. |
| **Zabbix diagram** | Host and LXC monitoring outside Kubernetes. |
| **Jellyfin diagram** | The media server on a privileged LXC with QuickSync. |
| **Media automation diagram** | Sonarr, SABnzbd and Recyclarr: from indexer to library. |
| **Immich diagram** | The family photo library and what it depends on. |
| **MicroBin diagram** | The public pastebin and its gated janitor routes. |
| **Startpage diagram** | The static page cloned from Git at pod start. |
| **TeamSpeak diagram** | Voice for friends, with an offsite DNS failover. |
| **Factorio diagram** | The game server a friend runs through SFTP only. |

Each service diagram is also embedded at the top of its service page, and the platform diagrams sit on the matching Components or Hardware page.

---

## Conventions

- **No addresses or IDs in a diagram.** No IP addresses, VM or container IDs, VLAN numbers or subnets. Those live in the text pages (**Hardware**, **Components & interactions**), so a diagram can be shared or reused without leaking the addressing plan.
- **Colour means role, everywhere.** Blue is Kubernetes (VMs and platform), green is DNS and Tailscale, purple is PostgreSQL, orange is backup, monitoring and storage-side services, yellow is workloads people use, teal is automation and control, red is the firewall and edge, grey is outside the lab.
- **Hand-drawn on purpose.** The diagrams are drawn in Excalidraw's sketch style to read as a map, not a spec.
- **Dark, with a transparent background.** Every diagram is drawn in Excalidraw's dark mode with no background fill, so the page behind the picture is the background. On a light page the light ink is hard to read.

---

## Editing a diagram

Every diagram page has an `.excalidraw` file attached. To change one:

1. Download the `.excalidraw` file from its page.
2. Open <https://excalidraw.com>, choose **Open** (Cmd/Ctrl+O) and pick the file.
3. Edit, then export a PNG and an SVG with **Dark mode** on and **Background** off.
4. Replace the attachments on the page. The same sources live in Git: `docs/architecture/` for the overview, `docs/services/diagrams/` for the rest.

The picture inside Outline is a PNG, so it only changes when the page is edited.
