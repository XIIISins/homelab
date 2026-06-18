<!-- docs/services/synology.md -->

# Synology (Munin)

Factory reset. Fresh DSM. Two volumes on single RAID 1 pool.

## Volume 1 — data (~553GB)

| Folder | Protocol | Purpose |
|--------|----------|---------|
| `proxmox-backup` | NFS | PBS datastore |
| `k3s-core-data` | NFS | Asgard K3s PVs (legacy, iSCSI now used) |
| `k3s-data` | NFS | Jotunheim K3s PVs |
| `db-backups` | NFS | DB dumps |
| `uploads` | NFS+SMB | Factorio SFTP |
| `hyper-backup` | Internal | Hyper Backup destination |

## Volume 2 — media (~2TB)

| Folder | Protocol | Purpose |
|--------|----------|---------|
| `media` | NFS+SMB | Movies, TV |
| `manga` | NFS+SMB | Manga — Komga |
| `downloads` | NFS | sabnzbd landing zone |
| `immich` | NFS | Photos/videos (~500GB reserved) |

**iSCSI:** SAN Manager installed. Synology CSI creates one target+LUN per PVC (`iqn.2000-01.com.synology:munin.pvc-<uuid>`). LUNs are single-session by default — see [incident log](../incidents/README.md) / known issues re: stale sessions after ungraceful restarts.

**OOB:** Tailscale DSM package (installed via Synology Package Center), tagged `tag:subnet-router`. Advertises the `10.0.0.0/16` supernet, auto-approved by the ACL's `autoApprovers.routes` rule. Authkey is UI-minted and stored in 1Password (out-of-band — not in Vault, not auto-renewing); see decision row "Munin Tailscale authkey: out-of-band". DSM-7 TUN-permissions boot-task (`tailscale configure-host`) installed via DSM Task Scheduler — see CLAUDE.md gotcha for what wipes it. Deployed 2026-05-23 (Phase 5e.3.f); install procedure in [`docs/procedures/teardown-rebuild.md`](../procedures/teardown-rebuild.md) Appendix C.
**K8s user:** `kubernetes` (admin) for Synology CSI driver.
