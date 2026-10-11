<!-- docs/outline/services-and-purpose/media-automation.md -->

# Media automation

The pipeline that fills the Jellyfin library without anyone copying files: Sonarr finds episodes, SABnzbd downloads them from Usenet, Sonarr imports them. Anime only, Usenet only.

![Media automation: Sonarr, SABnzbd and Recyclarr](/api/attachments.redirect?id=4fdb90ac-bb5a-467a-a126-8d17ca362606)

---

## The pieces

| Piece | Job | Notes |
|---|---|---|
| **Sonarr** | Watches for new episodes, picks releases, imports finished downloads, tells Jellyfin to refresh | `sonarr.niflheim.xiiisins.com` |
| **SABnzbd** | Downloads, repairs and unpacks | `sabnzbd.niflheim.xiiisins.com` |
| **Recyclarr** | Daily job that syncs a community quality profile, custom formats and naming into Sonarr from a file in Git | CronJob, 04:30 |

All three run as ordinary apps in the `media` namespace. Both web UIs are internal-only, behind Authentik ForwardAuth for the `media-admins` group; Sonarr's own login is set to External so that gate is the only one.

---

## Why one share

Sonarr and SABnzbd mount the **same** NFS export of Munin's `media` folder. Downloads land in `Downloads/`, and the import moves them into `Anime/`, so importing is a rename on one file system, not a copy over the network. Every file is created as the NAS's admin account regardless of the pod's user, so there are no UID or GID mismatches to maintain. Jellyfin mounts the same share read-only.

Config databases (Sonarr's SQLite, SABnzbd's ini) sit on `local-path` volumes, never NFS.

---

## Configuration as code, and what is not

**In Git:** the quality profile, custom formats and naming (via Recyclarr), the SABnzbd settings template, the routes and the secrets wiring. **In the apps' own databases:** the SABnzbd download client entry, the root folder, the indexer, the Jellyfin connection and the Discord notification. If Sonarr's config volume is lost, Recyclarr restores the profile and naming, and the rest is a short manual re-entry. A restore test of that path is still on the to-do list.

Usenet credentials and indexer keys are seeded into Vault by hand and mirrored to 1Password; API keys are minted by Terraform.

---

## Notifications

Sonarr cannot post Hermod's payload format, so its native Discord connection posts to the media webhook directly on grab, import, upgrade and anything needing manual attention. That is the one producer that deliberately bypasses Hermod.

---

## See also

- **Jellyfin** (this section): the consumer of the library.
- **Hermod** (this section): why media events skip it.
- **Storage & data** (Components): the NFS and local-path tiers.
- **Media automation diagram** (Diagrams): the pipeline from indexer to library.
