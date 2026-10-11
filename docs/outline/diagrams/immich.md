<!-- docs/outline/diagrams/immich.md -->

# Immich diagram

The family photo library. It is a secondary copy next to iCloud and Google Photos, so a single-NAS RAID1 with no offsite copy is a deliberate trade-off.

![Immich: family photo library](/api/attachments.redirect?id=1b0e336b-f9e2-499c-ba2e-60508a57c1d8)

---

## How to read it

- **Top left: two ways in.** Family reach it through the Cloudflare tunnel (`immich.xiiisins.com`) or, on the LAN, through an AdGuard rewrite (`immich.midgard`) that skips the tunnel. Both end at Traefik's midgard Gateway, one route per hostname.
- **The `immich` namespace (official chart).** `immich-server` handles the web app, API, uploads and background jobs. It calls `machine-learning` (faces and smart search), whose model cache is on `local-path` and can be re-downloaded. It uses Valkey, bundled in the chart, on an `emptyDir` as a job queue only.
- **Library volume (NFS).** Originals, thumbnails, encoded video and database dumps live on one NFS volume, which is Munin's shared NFS export: single RAID1, not backed up by PBS. Immich has no S3 backend, so the library lives on NFS rather than Garage.
- **Right: dependencies.** PostgreSQL through the VIP (pgvector plus `cube` and `earthdistance`, TLS required, extensions pre-created). Authentik through Immich's native OIDC, wired by hand once.
- **Top right: how secrets arrive.** Terraform creates the OIDC app and group gate, the Postgres password and the DNS records, and mints the secrets into Vault, which External Secrets hands to the pods.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[immich.excalidraw 50990](/api/attachments.redirect?id=b9fa6c65-9ed5-4d7a-a7e1-ae41597e28b4)

[immich.svg 60033](/api/attachments.redirect?id=64e9e615-67e1-4871-973a-042019ec1231)

---

## See also

- **Immich** (Services): the service page and its redundancy.
- **Storage & data** (Components): the NFS tier and the Postgres cluster.
- **Identity & secrets** (Components): the OIDC flow.
