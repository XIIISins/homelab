<!-- docs/known-issues/outline.md -->

# Known gotchas — Outline (wiki)

*Migrated from `CLAUDE.md`. Recovery commands + rules. Incident retros in [`../incidents/`](../incidents/).*

## Outline (wiki, 5j)

- **Outline image is `outlinewiki/outline` on Docker Hub — `ghcr.io/outline/outline` 403s anonymously.** Common copy-paste error; pod ends up in `ImagePullBackOff` with `failed to authorize: failed to fetch anonymous token ... 403 Forbidden`. Verify image path + current tags at https://hub.docker.com/r/outlinewiki/outline/tags when bumping. Tags follow `<version>-<rebuild>` (`1.8.0-1` = latest 1.8.0 rebuild). The GHCR confusion exists because Outline's *source* repo is on GitHub at `outline/outline`, but official container images publish only to Docker Hub.
- **Outline uses Recreate strategy, not RollingUpdate — migrations run on container entrypoint.** Outline runs `yarn db:migrate` automatically on container start (no separate migration Job). Two replicas would race; the loser crashes and re-rolls. `strategy.type: Recreate` makes migrations serial. Tradeoff: ~30s downtime on every pod restart. Acceptable for homelab single-tenant; revisit if Outline becomes critical-path.
- **Outline OIDC redirect URI lives at `/auth/oidc.callback`** — distinct from python-social-auth's `/oauth/complete/oidc/` (NetBox) and the more common `/oauth/callback`. Authentik provider must register the exact full path: `https://wiki.<host>/auth/oidc.callback`. Register one per hostname the app is served at (Outline reads the `URL` env var for the canonical redirect, but per-hostname registrations cover edge cases like the operator clicking "Sign in" while on the LAN hostname). OIDC discovery path: `<OIDC_ISSUER_URL>/.well-known/openid-configuration` (trailing slash on `OIDC_ISSUER_URL` is mandatory — Authentik's per-app discovery lives at `/application/o/<slug>/`).
- **One Redis per consumer (homelab convention).** Mirrors the Authentik Redis pattern. Hand-rolled single-replica StatefulSet in the same namespace, password from the shared `outline-redis-secret` Secret via secretKeyRef. Keeps blast radius tight (Outline can't see Authentik's session keys, vice versa) and avoids cross-app cache eviction surprises. The cost (one extra pod per consumer) is negligible vs the shared-Redis operational surface (eviction policy, cross-tenancy, restart blast).

