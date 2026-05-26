<!-- docs/services/outline.md -->

# Outline (asgard K3s)

K3s-hosted Outline wiki for living homelab notes — HOWTOs, gotchas-in-progress, friends/family-readable how-to-use-the-homelab content. Distinct in purpose from the git-tracked `docs/` tree: `docs/` is *deploy-shape* truth (decisions, services, incidents, IaC-adjacent context); Outline is the *operational* notebook where things evolve. Access gated through Authentik OIDC + the `outline-users` group.

Lives in `k8s/asgard/apps/outline/`. Backends span Patroni Postgres, a sidecar Redis, Garage S3, and Authentik OIDC — a useful exemplar of "K8s app fed by every existing infra layer."

## Architecture

| Piece | Where | Notes |
|-------|-------|-------|
| Image | `outlinewiki/outline:1.8.0-1` (Docker Hub) | NOT `ghcr.io/outline/outline` — that path 403s for anonymous pulls. |
| Workload | Deployment `outline/outline` | 1 replica, `strategy.type: Recreate` (see Why). UID 1001 (`nodejs`). `/_health` liveness/readiness on `:3000`. |
| Redis | StatefulSet `outline/outline-redis`, `redis:7-alpine` | 1 replica, UID 999, **emptyDir** backing, `--save "" --appendonly no`. Cache-only — see Why. |
| Database | PG role + DB `outline` on Patroni | Via HAProxy VIP `10.0.10.210`, `sslmode=no-verify` (see Why), scram-sha-256. |
| Object storage | Garage bucket `outline` | In-cluster endpoint `http://garage-s3.garage.svc.cluster.local:3900`, region `garage`, path-style addressing. |
| OIDC | Authentik provider `outline` | Issuer `https://authentik.xiiisins.com/application/o/outline/`. Three redirect URIs (one per hostname). Group-gated on `outline-users`. |
| External DNS | `wiki.xiiisins.com` (CF) + `wiki.midgard.xiiisins.com` (AGH) + `wiki.niflheim.xiiisins.com` (AGH) | All three live as of 2026-05-26. |

### Traffic flow

```
External (browser, anywhere on the internet)
  → DNS wiki.xiiisins.com → Cloudflare proxy
  → cloudflared tunnel → in-cluster cloudflared pods
  → https://traefik.traefik.svc.cluster.local:443 (httpHostHeader=wiki.xiiisins.com, noTLSVerify)
  → Traefik (midgard Gateway, websecure-apex-wildcard listener, *.xiiisins.com cert)
  → HTTPRoute outline-midgard → Service outline:3000 → pod

Internal — midgard fast-path (LAN, tailnet)
  → DNS wiki.midgard.xiiisins.com → AGH rewrite → 10.0.20.10 (Traefik VIP)
  → Traefik (midgard Gateway, websecure-midgard listener, *.midgard.xiiisins.com cert)
  → HTTPRoute outline-midgard → Service outline:3000 → pod

Internal — niflheim only (private)
  → DNS wiki.niflheim.xiiisins.com → AGH rewrite → 10.0.20.10
  → Traefik (niflheim Gateway, websecure listener, *.niflheim.xiiisins.com cert)
  → HTTPRoute outline-niflheim → Service outline:3000 → pod
```

The three hostnames all serve the same Outline instance — Outline's `URL` env var is pinned to `https://wiki.xiiisins.com` so generated links + OIDC callbacks are canonical, but Traefik will route any of the three to the pod. The midgard route lets LAN/tailnet clients bypass the Cloudflare tunnel round-trip without losing TLS.

## Backends

- **Postgres** — Patroni cluster `niflheim-pg` via HAProxy VIP `10.0.10.210`. Role + DB `outline` created via the `postgres-common-databases` task, leader-only:
  ```
  ansible-playbook playbooks/postgres-host.yml --limit <leader> \
    --skip-tags baseline,postgres,patroni,hardening \
    -e '{"patroni_is_leader": true}'
  ```
  Password dual-pathed in Vault — `secret/ansible/postgres/outline-password` (the postgres-common role) and `secret/k8s/outline/postgres-password` (ESO). Outline runs the ~60 entrypoint migrations on first pod boot. Backup posture inherits Patroni's WAL streaming + nightly basebackup (Phase 5g.2).

- **Redis** — sidecar StatefulSet `outline-redis` in the same namespace. Single replica, `redis:7-alpine`, `--save "" --appendonly no`. Used for BullMQ job queues, Yjs collab document state in transit, and websocket presence — all transient, recoverable from PG after a restart. Password from `secret/k8s/outline/app/redis_password` (Vault → ESO Secret `outline-redis-secret`).

- **S3 (Garage)** — bucket `outline` provisioned by `terraform/garage/outline.tf`. Same module mints the access key + writes `secret/k8s/outline/s3` (endpoint, region, bucket name, access key, secret). `AWS_S3_FORCE_PATH_STYLE=true` because Garage doesn't do vhost-style addressing. Holds uploads (images embedded in docs, attachments, exports).

- **OIDC (Authentik)** — provider + application provisioned by `terraform/authentik/outline.tf`. Three redirect URIs registered (one per hostname). Group binding: `outline-users` — non-members get `Policy binding 'None' returned result 'False'` on the Authentik consent screen. Client_id + client_secret written to `secret/k8s/outline/oidc`. Outline env: `USERNAME_CLAIM=email`, `DISPLAY_NAME=Authentik`, `SCOPES="openid profile email"`.

## Why a wiki at all

The homelab has accumulated enough operational notes that mixing them into the git-tracked `docs/` tree would dilute that tree's purpose. `docs/` should be the long-term record of how the homelab is *built*: decisions, services, incidents, runbooks that survive review. A wiki is the right home for things that move at a different cadence — half-finished gotcha drafts, friends-and-family-readable instructions for using the services, ad-hoc notes from a debugging session. Outline plus Authentik group gating also lets non-operator users (friends/family) read the bits intended for them without touching git.

## Why `outlinewiki/outline`, not `ghcr.io/outline/outline`

The GHCR image path returns 403 for anonymous pulls — Outline gates GHCR access behind a sponsorship tier. Docker Hub's `outlinewiki/outline` is the same upstream artifact, public, no auth required. Pinned to `1.8.0-1` (the `-N` suffix is upstream's image-rebuild counter; bump on the next `1.8.x` release).

## Why `Recreate`, not `RollingUpdate`

Outline runs `yarn db:migrate` inside the main container's entrypoint — there's no separate pre-deploy Job. Two replicas mid-roll would race for the migration lock; the loser crashes, re-rolls, repeats. `strategy.type: Recreate` makes the migration serial: the old pod terminates, the new one runs migrations cleanly, then comes up. The cost is ~30s of downtime on every pod restart, which is acceptable for a single-tenant homelab wiki. Revisit if Outline becomes critical-path enough to justify a proper Job-based migration split.

## Why two ExternalSecrets (split secret resolution)

Outline needs four Vault paths to fully boot: `secret/k8s/outline/app` (signing keys + Redis password), `.../postgres-password`, `.../s3`, `.../oidc`. Only `app` + `postgres-password` exist immediately — `s3` lands after `terraform/garage/` apply (which itself requires Garage to be Ready), and `oidc` lands after `terraform/authentik/` apply. ESO's contract is all-or-nothing per ExternalSecret: if any `data` ref fails to resolve, the target Secret isn't materialized at all.

Split:
- `outline-redis-secret` — pulls only `app/redis_password`. Resolves on day 1. Lets the Redis StatefulSet boot independently of S3/OIDC provisioning order.
- `outline-app` — pulls all four paths. Stays `SecretSyncedError` until TF garage + authentik have applied; the Outline pod sits in `CreateContainerConfigError` waiting on the Secret. That's *informative*, not buggy — it tells you which Terraform module still hasn't been applied.

## Why Redis on emptyDir

First deploy tried a 1Gi iSCSI PVC for `outline-redis` and Synology returned "Number of LUN reach limit." — the DS223J's per-target LUN cap is real and we're already deep into it. Outline's Redis is genuinely cache-only:

- BullMQ job state — jobs are re-enqueued from PG if lost.
- Yjs collab doc deltas in transit — clients re-sync from PG on reconnect.
- Websocket presence — naturally rebuilt as clients reconnect.

Nothing in Outline's Redis is canonical. `--save "" --appendonly no` makes that explicit (no RDB, no AOF). emptyDir means the cache evaporates on pod restart, which is fine. The right long-term fix is investigating the Synology LUN cap (revisit in open questions) — once lifted, swap emptyDir for an iSCSI PVC for less restart-time chatter.

## Why `sslmode=no-verify`

Outline's `pg-connection-string` + sequelize chain treats `sslmode=require` as the equivalent of `verify-full` — full chain + hostname verification. Patroni's PG cert is self-signed (intentional, internal-only network), so `require` rejects with `SequelizeConnectionError: self-signed certificate`. `no-verify` keeps the TLS envelope intact (Patroni's `hostssl`-only pg_hba is satisfied — no plaintext fallback) but skips chain + hostname checks. That's the correct posture for self-signed PG inside a private network: encrypt in transit, don't pretend to verify what isn't verifiable.

## Why UID 1001 explicitly

The image declares `USER nodejs` — name-based. K8s admission can't tell whether `nodejs` is non-root without resolving the name inside the image, which it doesn't do, so `runAsNonRoot: true` alone fails with "image has non-numeric user (nodejs), cannot verify user is non-root and will not be admitted." Setting `runAsUser: 1001` explicitly satisfies the check (1001 is the `nodejs` UID baked into the image's passwd).

## Why one Redis per consumer

Mirrors the Authentik Redis pattern. A shared Redis would invert several useful properties:
- Blast radius — an Outline cache misbehavior couldn't take Authentik down.
- Eviction surprises — both apps writing to one Redis means each can evict the other's hot keys.
- Auth — Outline doesn't need to know Authentik's Redis password and vice versa.

One sidecar Redis per app, all ephemeral, is the homelab convention.

## Adding a user

1. Edit `terraform/authentik/users.yaml`. Add `outline-users` to the target user's `groups:` list.
2. `. ~/.cache/homelab/env.sh && (cd terraform/authentik && terraform apply)`. The group binding propagates to Authentik.
3. User logs into Outline (any of the three hostnames). First login auto-provisions an Outline account at the default role (`member`).
4. As an Outline workspace admin, promote/demote in Outline → Settings → Members. Outline has no OIDC-claim→role mapping today; role assignment is a one-time manual step per user.

Removing access: drop `outline-users` from the user's `groups:`, `terraform apply`. The next OIDC token refresh fails the group check; existing sessions expire on Outline's own session TTL.

## Operations

**Check pod state.**
```
kubectl -n outline get pods
kubectl -n outline logs deploy/outline --tail=100
```
Look for `Listening on http://localhost:3000` to confirm migrations completed.

**Rolling restart.** `kubectl -n outline rollout restart deploy/outline`. Recreate strategy means ~30s gap during which the wiki is unreachable; OIDC sessions survive (JWTs in browser cookies).

**Force re-pull after image bump.** Bump `image:` in `deployment.yaml`, commit, `flux reconcile kustomization apps`. Outline's entrypoint runs new migrations idempotently on next boot.

**Backup posture.** PG covers all canonical state (pages, comments, users, permissions). Garage covers all uploads. The Outline pod itself is stateless beyond `/tmp`. Loss of the pod = loss of in-flight cache = harmless. Backup verification is end-to-end via Patroni (PG WAL + basebackup) + Garage's own replication; no Outline-specific backup orchestration.

**Storage expansion.** N/A — Outline has no PVC. Uploads grow in Garage; resize Garage's underlying storage as needed (separate, future work).

**Migrate Redis to iSCSI** (when the LUN cap is investigated + lifted): drop the emptyDir, add a `volumeClaimTemplates` entry on the StatefulSet, follow the chown-init pattern documented in CLAUDE.md for iSCSI fsGroup. Behavioral change is "Redis restart doesn't drop cache" — purely a polish improvement, no correctness impact.

**Bumping Outline.** Pin moves in `deployment.yaml`. Outline's migration discipline is mature; rollbacks are PG-flavored (down-migration if available, or restore from Patroni basebackup). Read upstream release notes before any minor-version jump.

## Known gotchas referenced

- CLAUDE.md "Authentik chart values block does NOT override env vars" — Outline's app config is entirely env-driven from `outline-app` Secret; no chart values shape worth confusing with.
- CLAUDE.md "Cloudflared targets backend Services by ClusterIP DNS, NEVER MetalLB IPs" — applied; `wiki.xiiisins.com` tunnel ingress points at `traefik.traefik.svc.cluster.local`.
- CLAUDE.md "In-cluster K8s-fronted FQDNs: use a CoreDNS rewrite, not hostAliases" — N/A so far; nothing in-cluster currently dials `wiki.*.xiiisins.com`. Add a rewrite if/when an in-cluster integration appears (e.g. an embed-checker bot).
- CLAUDE.md "PG `hostssl`-only rejects plaintext clients" — `sslmode=no-verify` (not `require`) satisfies the hostssl-only pg_hba while accepting Patroni's self-signed cert. Adds the self-signed-cert nuance on top of the existing rule.
- CLAUDE.md "K3s host network can't reach MetalLB-announced VIPs from the same cluster" — N/A; Outline doesn't run on a K3s host, and external traffic reaches Traefik either via cloudflared (in-cluster) or via the MetalLB VIP from off-cluster clients.
