<!-- docs/services/synology.md -->

# Synology (Munin)

DS223J on a single RAID 1 pool, carved into DSM volumes by purpose. Serves PBS and Proxmox over NFS, K8s PVs over NFS and iSCSI, and the media library.

**Live volumes (after the 2026-05-30 storage-tiering redesign, [`storage-tiering.md`](../procedures/storage-tiering.md)):**

| Volume | Role |
|--------|------|
| Volume1 | Backup: the PBS datastore (`proxmox-backup`, mounted inside the PBS LXC) and Hyper Backup |
| Volume2 | All asgard K8s storage: the `k8s-nfs` share (every `nfs-client` PVC, ~95 GiB effective, so NFS sizing is advisory and the DSM volume is the real ceiling) and the one surviving iSCSI LUN class (`synology-csi-iscsi-retain-vol2`, e.g. `garage/meta`) |
| Volume5 | Media: shared folder `media` (renamed from `media-backup`) |

The DSM-wide iSCSI LUN cap (~10) is why K8s storage is tiered; see [`storage-iscsi-synology.md`](../known-issues/storage-iscsi-synology.md). The folder tables below predate the redesign and are kept as the share inventory; verify against DSM before relying on a row.

## Volume 1 — data (~553GB)

| Folder | Protocol | Purpose |
|--------|----------|---------|
| `proxmox-backup` | NFS | PBS datastore |
| `k3s-core-data` | NFS | Asgard K3s PVs (legacy, iSCSI now used) |
| `k3s-data` | NFS | Jotunheim K3s PVs (unused: Jotunheim dropped 2026-10-05) |
| `db-backups` | NFS | DB dumps |
| `uploads` | NFS+SMB | Factorio SFTP |
| `hyper-backup` | Internal | Hyper Backup destination |

## Volume 2 — media (~2TB)

| Folder | Protocol | Purpose |
|--------|----------|---------|
| `media` | NFS+SMB | Movies, TV. **Live export `10.0.254.20:/volume5/media`**, read-only for Jellyfin (LXC 1123, [`jellyfin.md`](jellyfin.md)) and read/write for the media-automation pods ([`media-automation.md`](media-automation.md)) |
| `manga` | NFS+SMB | Manga — Komga |
| `downloads` | NFS | sabnzbd landing zone |
| `immich` | NFS | Not a separate folder — Immich's library is a PVC on the existing `k8s-nfs` share/`nfs-client` StorageClass (same as MicroBin), deployed 2026-09-03. See [`services/immich.md`](immich.md). |

**iSCSI:** SAN Manager installed. Synology CSI creates one target+LUN per PVC (`iqn.2000-01.com.synology:munin.pvc-<uuid>`). LUNs are single-session by default — see [incident log](../incidents/README.md) / known issues re: stale sessions after ungraceful restarts.

**OOB:** Tailscale DSM package (installed via Synology Package Center), tagged `tag:subnet-router`. Advertises the `10.0.0.0/16` supernet, auto-approved by the ACL's `autoApprovers.routes` rule. Authkey is UI-minted and stored in 1Password (out-of-band — not in Vault, not auto-renewing); see decision row "Munin Tailscale authkey: out-of-band". DSM-7 TUN-permissions boot-task (`tailscale configure-host`) installed via DSM Task Scheduler — see CLAUDE.md gotcha for what wipes it. Deployed 2026-05-23 (Phase 5e.3.f); install procedure in [`docs/procedures/teardown-rebuild.md`](../procedures/teardown-rebuild.md) Appendix C.
**K8s user:** `kubernetes` (admin) for Synology CSI driver.
