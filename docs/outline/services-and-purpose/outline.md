<!-- docs/outline/services-and-purpose/outline.md -->

# Outline

The homelab's wiki — the knowledge base you're reading right now. Outline is an open-source team wiki: markdown documents, collections, full-text search, and a clean editor. It serves double duty as a household-shared knowledge space and the operator's documentation home.

---

## Where it runs

Outline runs in **asgard K3s** (`outline` namespace) as a single-replica Deployment with a `Recreate` update strategy.

- **Single replica + Recreate** because Outline runs its database migrations on container start. Two replicas would race on migrations; `Recreate` makes restarts serial at the cost of a few seconds' downtime on each roll — fine for a wiki.
- Runs as numeric UID 1001 (the image's `nodejs` user).

---

## Dependencies

Outline is the first service to lean on the full modern stack at once:

- **Postgres** (Patroni VIP) for documents and metadata, connected with `?sslmode=no-verify` — Outline's Node Postgres client treats plain `require` as full certificate verification and would reject the cluster's self-signed cert, so `no-verify` keeps the TLS envelope without the chain check.
- **Garage (S3)** for attachments and uploads — Outline is the **first consumer of the homelab's object-storage layer**. Future services (Immich, backups) follow the same pattern.
- **Redis** as a session/cache store, run as a sidecar on `emptyDir`. Cache-class state that's cheap to rebuild, so it deliberately avoids spending an iSCSI LUN against the NAS's LUN cap.

---

## Access and identity

- **Authentik OIDC**, gated on the `outline-users` group. Membership is declared per-user in `terraform/authentik/users.yaml` — being in Authentik isn't enough; you have to be granted the Outline app specifically.
- The OIDC redirect path is `/auth/oidc.callback` (Outline-specific; not the same path as NetBox or other apps).
- **Three hostnames:** `wiki.xiiisins.com` (external, via Cloudflared), `wiki.midgard.xiiisins.com` and `wiki.niflheim.xiiisins.com` (LAN, via AdGuard → Traefik).

---

## See also

- **Storage & data** (Components) — the Garage S3 layer Outline pioneered and the Patroni Postgres it persists to.
- **Identity & secrets** (Components) — the Authentik OIDC group-gate model.
- **Edge** (Components) — how the three `wiki.*` hostnames resolve and route.
