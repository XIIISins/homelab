<!-- docs/outline/services-and-purpose/immich.md -->

# Immich

A self-hosted photo and video library for the operator and close family. It is a **secondary copy** next to the phone vendors' own cloud backups, never the only copy, and that framing sets how much redundancy it gets.

---

## How it is built

Immich runs in asgard K3s from its official chart. It is a good example of an app fed by several infrastructure layers:

| Need | Provided by |
|---|---|
| Photos and video (originals, thumbnails, encoded video) | An NFS volume on Munin. Immich has no native S3 backend, so Garage is not used. Sizing is nominal: the real ceiling is the NAS volume. |
| Database | The shared Postgres cluster through the HAProxy VIP, with the **pgvector** extension and the `cube`/`earthdistance` extensions pre-created, because Immich's migrations need superuser rights the app's role lacks. |
| Job queue | The chart's bundled Valkey on emptyDir: cache-class, rebuilt on restart. |
| Machine-learning models | A small `local-path` volume: downloadable and disposable. |
| Sign-in | Authentik OIDC for the `immich-users` group. |

## Access

`immich.xiiisins.com` is public through the Cloudflare tunnel (family connect from anywhere, including the mobile app), and `immich.midgard.xiiisins.com` is the internal fast path. There is no internal-only name: it is family-facing, not operator tooling.

## One manual step

Terraform creates the Authentik provider, the application, the group gate and the client secret in Vault, but enabling OAuth inside Immich is done once by hand in its admin settings after the first-run wizard, because the setting is not safely declarable.

## Redundancy, stated honestly

One NAS with RAID 1, no offsite copy, and not part of the PBS job. That is a deliberate trade-off for a secondary copy, not an oversight.

---

## See also

- **Storage & data** (Components): the NFS tier and the Postgres cluster.
- **Edge** (Components): the public path.
- **Identity & secrets** (Components): the OIDC flow.
