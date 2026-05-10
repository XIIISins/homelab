# Storage — Synology layout

## Hardware

Synology DS223J — 2-bay NAS, single 1 GbE port, ~3.5 TB usable in RAID 1. Capped at ~115 MB/s network throughput.

## Volume design

Two volumes replacing the previous 11-volume layout. Fewer volumes = all free space is fluid within each volume rather than siloed in fixed partitions.

### Volume 1 — data (~500 GB)

Infrastructure and application data. Fixed size so PBS and K3s storage have a guaranteed ceiling that media cannot eat into.

| Shared folder | Protocol | Purpose |
|---------------|----------|---------|
| `proxmox-backup` | NFS | PBS datastore |
| `k3s-data` | NFS | K3s PersistentVolumes via democratic-csi |
| `db-backups` | NFS | MariaDB + PostgreSQL nightly dumps |
| `uploads` | NFS + SMB | Factorio saves/mods (SFTP via SFTPGo), friend uploads |
| `hyper-backup` | Internal | Hyper Backup destination — snapshots PBS datastore |

### Volume 2 — media (~3.1 TB, remainder of pool)

All bulk media. Gets the rest of the pool so it can grow freely without ever hitting a ceiling.

| Shared folder | Protocol | Purpose |
|---------------|----------|---------|
| `media` | NFS + SMB | Movies, TV — Jellyfin and arr stack |
| `manga` | NFS + SMB | Manga collection — Komga |
| `downloads` | NFS | sabnzbd download landing zone |
| `wallpapers` | NFS + SMB | Wallpaper gallery |
| `immich` | NFS | Immich photo/video library |

## NFS exports

All NFS exports use IP-based access control — only Proxmox node IPs and the K3s worker IPs are permitted to mount each share. No username/password for NFS (handled at network layer via VLAN 30 isolation).

## Backup chain

```
Proxmox VMs + LXCs
  → PBS (nightly, 7-day retention)
    → Hyper Backup snapshots PBS datastore
      → Point-in-time recovery available
```

## Power consumption

- Active: ~16W
- HDD hibernation: ~4W

HDD hibernation enabled for the media volume when idle — Jellyfin and arr stack activity keeps it awake during use, but it spins down overnight.
