# Storage — migration steps

Steps to consolidate the Synology from 11 volumes to 2 before the Proxmox rebuild. All steps are non-destructive to the media volume.

## Prerequisites

- [ ] Confirm no active iSCSI sessions (already verified — `sonarr` LUN is orphaned)
- [ ] Confirm PBS is not actively running a backup job
- [ ] Save current Hyper Backup job configs (screenshot or export)

## Step 1 — Delete orphaned iSCSI LUN

In DSM → iSCSI Manager:
1. Delete the `sonarr` LUN (25 GB, thick provisioned on Volume 11)
2. Delete the `Synology iSCSI Target` if no other LUNs remain

Recovers 25 GB on Volume 11.

## Step 2 — Copy factorio data

Volume 1 (`factorio_data`) has 2.6 GB of friend's factorio files. Copy to temporary location before deleting the volume.

```bash
# From any Proxmox node or your Mac via SMB
cp -r /path/to/volume1/factorio_data /path/to/temporary/location
```

## Step 3 — Delete old volumes

In DSM → Storage Manager, delete the following volumes (in any order):
- Volume 1 (factorio_data, 9.6 GB)
- Volume 3 (container data, 96 GB — nearly empty)
- Volume 4 (homes, 58 GB — no useful data)
- Volume 7 (downloads, 48 GB)
- Volume 8 (manga, 106 GB)

This reclaims approximately **317 GB** of pool space plus the iSCSI LUN.

## Step 4 — Create new Volume 1 (data)

In DSM → Storage Manager → Create Volume:
- Size: 500 GB
- File system: Btrfs
- Label: `data`

Create shared folders within it:
- `proxmox-backup` — NFS, restrict to Proxmox node IPs
- `k3s-data` — NFS, restrict to K3s VM IPs
- `db-backups` — NFS, restrict to Proxmox node IPs
- `uploads` — NFS + SMB
- `hyper-backup` — no external access (Hyper Backup internal destination)

## Step 5 — Reorganise media volume

Current Volume 2 (`media`) stays completely untouched — no data movement. Just add new shared folders:

- Create `manga` folder — copy manga files from old Volume 8 backup
- Create `downloads` folder — move any existing downloads from old Volume 7
- Create `wallpapers` folder — move wallpaper files if applicable
- Create `immich` folder — empty, ready for Immich

## Step 6 — Update PBS datastore path

In PBS → Datastore → Edit:
- Change path from `/volume11/...` to `/volume1/proxmox-backup`

Verify a backup job completes successfully before proceeding.

## Step 7 — Update Hyper Backup

In DSM → Hyper Backup:
- Update `docker-config` job destination to `/volume1/hyper-backup/docker-config`
- Update `Proxmox` job destination to `/volume1/hyper-backup/proxmox`
- Run both jobs manually to verify

## Step 8 — Restore factorio data

Copy the factorio data saved in Step 2 into `/volume1/uploads/factorio/`.

## Done

Final volume layout:
- Volume 1 (data, 500 GB) — infrastructure, backups, uploads
- Volume 2 (media, ~3.1 TB) — all media, grows freely
- Volume 11 (Proxmox VM NFS, retained) — keep until PBS migration verified
