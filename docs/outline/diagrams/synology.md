<!-- docs/outline/diagrams/synology.md -->

# Synology (Munin) diagram

The Synology that backs storage for the whole lab. One DS223J, one RAID1 pool, carved into volumes by purpose, with a hard DSM-wide iSCSI LUN cap that shapes everything.

![Munin: the Synology that backs storage for the whole lab](/api/attachments.redirect?id=e4e3b8b3-8c14-4a3a-9cf9-7e7de7e09367)

---

## How to read it

- **The centre box: three volumes.** Volume 1 is backup: the PBS datastore (NFS, mounted inside the PBS LXC on Urd), NFS storage for the Proxmox hosts, and the Hyper Backup destination. Volume 2 is all asgard Kubernetes storage: the `k8s-nfs` share behind every `nfs-client` volume, plus the one surviving iSCSI LUN class. NFS sizes are advisory; the volume is the ceiling. Volume 5 is media, a shared folder exported over NFS per client.
- **Right: the Kubernetes consumers.** `csi-driver-nfs` serves Immich, MicroBin, TeamSpeak and Garage's data. Synology CSI (iSCSI) serves Garage's metadata and other mmap-class databases only. Sonarr and SABnzbd read and write the media share and import by rename.
- **Left: the others.** The PBS LXC, the Proxmox hosts and the Jellyfin LXC (read-only on media).
- **Top right: why storage is tiered.** DSM allows about ten iSCSI LUNs in total. Because Synology CSI uses one LUN per volume claim, iSCSI is kept for single-instance mmap and fsync databases.
- **Bottom: out-of-band access.** The DSM Tailscale package makes Munin a subnet router. Its auth key is minted in the UI and kept in 1Password only.
- Databases never run on NFS, and VM disks live on local LVM-thin, not here.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[synology.excalidraw 33788](/api/attachments.redirect?id=10db7b8a-7bf2-4115-9430-da79c2f3b96e)

[synology.svg 47111](/api/attachments.redirect?id=f11b8563-f3a7-4877-8e4c-47f19253ecb8)

---

## See also

- **Storage** (Hardware): Munin and the LUN ceiling in detail.
- **Storage & data** (Components): the storage tiers.
