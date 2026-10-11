<!-- docs/outline/diagrams/outline.md -->

# Outline diagram

The operational wiki, fed by every infrastructure layer: three hostnames, one instance, with Postgres, Redis, Garage S3 and Authentik OIDC behind it.

![Outline: the operational wiki, fed by every infra layer](/api/attachments.redirect?id=61b4afbb-cfd5-43f8-82af-7612f5594ff7)

---

## How to read it

- **Left: three ways in.** The Cloudflare tunnel (`wiki.xiiisins.com`) for readers anywhere, an AdGuard rewrite (`wiki.midgard`) for the LAN and tailnet, and an AdGuard rewrite (`wiki.niflheim`) for private access. Traefik has a route per hostname and all three reach the same Service.
- **The `outline` namespace (dashed box).** One Outline Deployment (`Recreate`), its URL pinned to `wiki.xiiisins.com` so links and OIDC callbacks stay canonical. Beside it, a Redis sidecar StatefulSet on an `emptyDir` with no persistence: it holds cache, job queues and collaboration state, nothing canonical, and rebuilds on restart.
- **Why Redis on `emptyDir`, one per app.** An iSCSI claim would hit the NAS LUN cap and nothing in Redis is canonical. A Redis per app keeps blast radii separate and avoids eviction fights with Authentik.
- **Right: what Outline stores and who it trusts.** PostgreSQL through the VIP (its own role and database, TLS without verification). Garage S3 (bucket `outline`, path-style, browser uploads through the S3 hostname). Authentik OIDC, restricted to the `outline-users` group.
- **Bottom: secrets.** Terraform creates the Garage bucket and key and the Authentik OIDC app and writes the secrets to Vault; External Secrets turns them into Kubernetes Secrets (app keys, database password, S3 credentials, OIDC secret).
- Outline is the operational notebook; `docs/` in Git is the record of how things are built.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[outline.excalidraw 48544](/api/attachments.redirect?id=e55c5091-1244-40cc-a7a9-34973a3ce13e)

[outline.svg 57890](/api/attachments.redirect?id=923ae2b6-5c6d-460b-8e46-4fdb0fb1b115)

---

## See also

- **Outline** (Services): the service page.
- **Garage diagram** (Diagrams): the S3 store behind uploads.
- **Edge** (Components): the three hostnames.
