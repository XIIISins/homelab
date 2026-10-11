<!-- docs/outline/diagrams/garage.md -->

# Garage diagram

S3 storage inside the cluster. One node, replication factor 1: Garage is the S3 protocol layer, and durability is the NAS's RAID1.

![Garage: S3 storage inside the cluster](/api/attachments.redirect?id=0beeb37e-1f2b-43d5-b117-315a2d11a6c7)

---

## How to read it

- **Left: three ways in, one door.** The Outline pod talks to S3 inside the cluster and never touches MetalLB. A browser on the LAN or tailnet uploads and reads through a Traefik read/write route. A browser on the internet reads only, through the Cloudflare tunnel and a Caddy that allows `GET`/`HEAD` on `/outline/*` and nothing else. All three end at the `garage-s3` ClusterIP Service, the only door for consumers.
- **Middle: Garage itself.** A one-replica StatefulSet running Garage v2 (Rust) in a distroless image, so there is no shell. Its consistency mode is relaxed on purpose, because there is one node.
- **Below it: two volumes, two tiers.** Metadata (LMDB) sits on an iSCSI volume because it is mmap-class and has to stay on block storage. Object data sits on an NFS volume because it is file-class and grows with the content. Both end on Munin's Volume 2, a single RAID1: that is the real durability.
- **Right: control.** `garage-admin` is ClusterIP-only, reached by port-forward. A layout-init Job posts the cluster layout so the pod can go Ready. Terraform creates the buckets and per-app keys and writes the server secrets and each app's S3 credentials to Vault.
- **The headless Service** publishes not-ready pods. Without that, the layout-init Job could not reach the pod before it was Ready, which would deadlock.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[garage.excalidraw 50694](/api/attachments.redirect?id=2e98c78a-6cbd-492e-b588-66e36c4b098e)

[garage.svg 61343](/api/attachments.redirect?id=7e6d56c1-f497-4f50-aa0f-b0c72ca702b9)

---

## See also

- **Storage & data** (Components): the tiers and the Garage section.
- **Outline** (Services): the app that stores attachments here.
- **Storage** (Hardware): the iSCSI LUN ceiling behind the metadata volume.
