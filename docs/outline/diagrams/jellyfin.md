<!-- docs/outline/diagrams/jellyfin.md -->

# Jellyfin diagram

The media server on a privileged LXC with QuickSync. It is outside Kubernetes on purpose: it needs the host's iGPU, and K3s only fronts it with a route.

![Jellyfin: media server on a privileged LXC with QuickSync](/api/attachments.redirect?id=b9218dc7-22ea-4aa6-ab85-14c0cba3a78a)

---

## How to read it

- **Left: two ways in.** The normal path is an AdGuard rewrite (`jellyfin.midgard`) to Traefik, which fronts the LXC with a route-only Service and EndpointSlice; it returns 502 whenever the LXC is down. The fallback is `jellyfin-direct.niflheim`, plain HTTP straight to the container, for when K3s is down.
- **The LXC (dashed box, on Urd).** The Jellyfin server (official apt repo, versions held, one Anime library, real-time monitoring off) and the Intel iGPU passed through for QuickSync: HEVC and AV1 decode, H.264 and HEVC encode.
- **Three storage details.** The root disk holds config and SQLite and never lives on NFS. `/media` is NFS, read-only. The transcode cache mount is left out of backups.
- **Right: neighbours.** Sonarr (in K3s) imports finished files by rename, then asks Jellyfin to refresh the library. Munin's media share is mounted read-only. Zabbix checks the hourly QuickSync smoke test (which transcodes a test pattern), the mount and the health endpoint. PBS snapshots the root disk.
- **Remote family access** is Tailscale. About ten concurrent heavy 1080p transcodes stay above real time.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[jellyfin.excalidraw 47551](/api/attachments.redirect?id=c69836f2-cf33-4eef-9f88-59ed9c619a42)

[jellyfin.svg 59098](/api/attachments.redirect?id=b08a127d-a24c-482f-9195-194487514ed4)

---

## See also

- **Jellyfin** (Services): the service page.
- **Media automation** (Services): how episodes arrive.
- **Hypervisors** (Hardware): the iGPU on Urd.
