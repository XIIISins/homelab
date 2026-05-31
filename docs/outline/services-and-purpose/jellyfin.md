<!-- docs/outline/services-and-purpose/jellyfin.md -->

# Jellyfin

The household media server — movies, TV, and music, streamed to whatever device someone's holding. Free and open-source, no account-with-a-vendor, no telemetry.

> **Status: planned.** The design below is settled; the LXC isn't deployed yet. This page describes the intended shape.

---

## Where it runs, and why

Jellyfin runs as a **privileged LXC on Urd** — deliberately *not* in Kubernetes.

The reason is hardware transcoding. Jellyfin needs direct access to the Intel iGPU (`/dev/dri`) to offload video transcoding to **QuickSync** instead of melting a CPU on software transcode. Device passthrough into a pod is awkward and fragile; into an LXC it's a clean bind-mount. Urd is the chosen host because all three nodes now carry the same i3-1215u with Intel UHD Graphics — a capable QuickSync engine — so the placement is a deliberate "keep it on a known node" choice rather than a hardware-forced one.

Privileged (rather than the fleet-standard unprivileged) LXC is required for the GPU passthrough to work.

---

## Dependencies

- **Media storage** — an NFS share from Munin (the Synology NAS) holds the library. Media is bulk, sequential-read data — exactly what NFS over 1 GbE is good at, and the wrong thing to put on per-PVC iSCSI.
- **No database service.** Jellyfin keeps its own metadata in a local SQLite store; it doesn't lean on the Postgres cluster.

---

## Access and identity

- **Local Jellyfin accounts.** Jellyfin runs its own user database rather than federating to Authentik. Media-server logins are low-stakes and shared casually within the household, so the OIDC ceremony isn't worth it here.
- Reachable internally at `jellyfin.midgard.xiiisins.com`, with external access via the Cloudflared tunnel so family can stream from outside the house.

---

## See also

- **Hypervisors** (Hardware) — Urd's i3-1215u and the QuickSync rationale.
- **Storage & data** (Components) — why media lives on NFS, not iSCSI.
- **Compute & hypervisors** (Components) — the VM-vs-LXC decision that puts Jellyfin in an LXC.
