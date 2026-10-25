# Kavita (manga and PDF readers)

Two Kavita instances in the `kavita` namespace (`k8s/asgard/apps/kavita/`), both internal-only on the niflheim Gateway (no Cloudflare tunnel, no midgard alias).

| Instance | URL | Library (subPath of the `manga` share) | Login |
|---|---|---|---|
| `kavita` | `manga.niflheim.xiiisins.com` | `manga/` | Kavita accounts only (OPDS and mobile readers work) |
| `kavita-manga2` | `manga2.niflheim.xiiisins.com` | `manga2/` | Authentik ForwardAuth (`manga2` group) in front of Kavita accounts; browser-only |

- **Why Kavita:** Komga's UI was rejected by the operator; Kavita has dedicated manga (RTL, double-page) and PDF readers.
- **Storage:** one static, read-only NFS PV of `10.0.254.20:/volume5/manga` shared by both; each Deployment mounts its own subPath. Config (SQLite, covers) is a `local-path` PVC per instance, never NFS. Single replica, `Recreate`.
- **Adding content:** put files in `manga/` or `manga2/` on the DSM share (SMB), then scan the library in Kavita. PDFs group into series by folder; follow Kavita's naming for volumes.
- **First start:** the first visitor to each instance creates its admin account; do that immediately. Add the libraries at `/manga`.
- **Auth/DNS:** `terraform/authentik/kavita.tf` (needs the one-time embedded-outpost attach for the manga2 provider), `terraform/adguard/rewrites.tf`.
