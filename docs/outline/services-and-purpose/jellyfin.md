<!-- docs/outline/services-and-purpose/jellyfin.md -->

# Jellyfin

The household media server. It plays an anime library to family devices, transcoding in hardware when a device cannot play a file directly.

---

## Where it runs

A **privileged LXC on Urd** (`10.0.11.223`), outside Kubernetes. Two reasons:

- **QuickSync.** The Intel iGPU's render device (`/dev/dri/renderD128`) is passed through to the container so H.264 and HEVC transcodes run on the GPU instead of the CPU. That passthrough needs root-ticket authentication to the Proxmox API, so the LXC is defined in the root-auth Terraform module.
- **Independence.** Playback should survive a Kubernetes outage. A direct name, `jellyfin-direct`, reaches the container without Traefik.

The library sits on Munin's `media` share, mounted **read-only** inside the container. The app database stays on the container's local disk (SQLite must never live on NFS), and PBS backs the root disk up. A 40 GB cache mount is excluded from backups.

---

## Access

- **Normal:** `jellyfin.midgard.xiiisins.com`, through Traefik on the internal Gateway. Traefik fronts the LXC with a Service and a manual EndpointSlice, so the page returns 502 whenever the container is down.
- **Fallback:** `jellyfin-direct.niflheim.xiiisins.com`, plain HTTP straight to the container.
- **Remote family:** over Tailscale. Jellyfin is **not** exposed through the Cloudflare tunnel: video through a tunnel risks the whole Cloudflare zone under their terms.

---

## What fills the library

**Sonarr** imports finished downloads into the share and tells Jellyfin to refresh, so the library updates without file-system watching. See **Media automation**.

---

## Keeping it honest

- **A QuickSync self-test** runs hourly, transcoding a test pattern as the Jellyfin user. Zabbix raises a High alert if it keeps failing for 70 minutes, which catches a lost GPU device or a broken driver before a family member does.
- Zabbix also watches that the media share is mounted, that `/health` reports Healthy, and for GPU hangs in the Proxmox hosts' kernel logs.
- Software versions are pinned; bumping is a deliberate variable change.
- The container has been migrated to another node and back, and restored from PBS into a scratch container, as part of acceptance.

---

## See also

- **Media automation** (this section): how episodes arrive.
- **Hypervisors** (Hardware): the iGPU on Urd.
- **Edge** (Components): why Jellyfin is internal-only.
- **Storage** (Hardware): the media share on Munin.
