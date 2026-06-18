<!-- docs/services/n8n.md -->
# n8n

Workflow automation. The **editor UI + REST API** are internal-only and
Authentik-gated; **webhook/form trigger endpoints** are public so third-party
services can drive workflows.

- **Manifests:** `k8s/asgard/apps/n8n/`
- **Image:** `docker.n8n.io/n8nio/n8n:2.23.1` (== Docker Hub `n8nio/n8n`; concrete-pinned — never `latest`/`stable`/`nightly`)
- **Deployed:** 2026-06-01 (commit `e2a7be1`)
- **Terraform:** `terraform/authentik/n8n.tf` (ForwardAuth provider + app + gate), `terraform/vault/main.tf` (secret paths), `terraform/cloudflare/` (apex CNAME), `terraform/adguard/` (midgard rewrite)

## Hostnames + access model

| Hostname | Exposure | Routes | Auth |
|---|---|---|---|
| `n8n.niflheim.xiiisins.com` | internal (AdGuard → Traefik) | **full app** (editor UI + REST + webhooks) | **Authentik ForwardAuth** (`n8n-admins`) |
| `n8n.xiiisins.com` | **public** (cloudflared) | **trigger paths only** (`/webhook*`, `/form*`) | **none** (n8n authenticates webhooks per-workflow) |
| `n8n.midgard.xiiisins.com` | internal LAN (AdGuard → Traefik) | trigger paths only | none |

The exposure is **deliberately asymmetric** — two HTTPRoutes:

- **`n8n-niflheim`** serves the whole app behind the Authentik gate. This is how
  the operator uses n8n. Because the entire route is gated, the
  `/outpost.goauthentik.io` callback rides the gated `/` catchall automatically
  (contrast MicroBin, whose `/` is open and needs the callback routed
  explicitly).
- **`n8n-midgard`** serves **only** the public trigger endpoints (`/webhook/`,
  `/webhook-test/`, `/webhook-waiting/`, `/form/`, `/form-test/`,
  `/form-waiting/`) with no ForwardAuth — external callers must POST without an
  Authentik round-trip. Any non-trigger path at these hostnames 404s at Traefik,
  so the public attack surface is the webhook handler, not the editor.

### Why ForwardAuth and not native OIDC

n8n's built-in SSO (SAML/OIDC) is an **Enterprise-licensed** feature — the
community edition can't do it. SSO is therefore enforced at the edge (Authentik
ForwardAuth in front of Traefik), with n8n's own owner/email-password login
sitting behind the gate as defence-in-depth. Membership in `n8n-admins`
(`terraform/authentik/{groups,users}.yaml`) == "can reach the n8n login screen",
not automatic n8n access.

To widen external exposure to the full UI later: add a `/` catchall rule to
`n8n-midgard` carrying the ForwardAuth filter, and route
`/outpost.goauthentik.io` through a gated rule (MicroBin-style).

## State + storage — no PVC by design

n8n runs in **"regular" execution mode** (one process serves UI + webhooks +
executions) — correct for homelab scale; queue mode (Redis + worker pods) is
only worth it at ~1k+ executions/day and is an env flip + extra Deployment to
add later.

The pod has **no volume**. With external Patroni Postgres + the env-injected
`N8N_ENCRYPTION_KEY` + default binary-data mode (binary data, if any, lands
inline in Postgres), `~/.n8n` holds nothing that must survive a restart, so the
pod is stateless-on-disk. The image pre-creates `/home/node/.n8n` owned by uid
1000 — mounting an emptyDir there would shadow it root-owned and break n8n's
writes (the emptyDir-perms gotcha), so we deliberately don't.

**Consequence:** UI-installed community nodes would NOT survive a restart. If
those are ever wanted, bake them into a pinned image layer (GitOps-clean) rather
than reintroducing a PVC. S3 external storage for binary data is
Enterprise-licensed (community can't write to it), so it's not an option here.

**Recreate** strategy (old pod fully gone before new starts) keeps n8n's TypeORM
DB migrations serial on container start — same rationale as Outline.

## Database

Postgres via the **Patroni HAProxy VIP `10.0.10.210`** (auto-routes to the
current leader). DB + user `n8n`. `pg_hba` is `hostssl`-only so
`DB_POSTGRESDB_SSL_ENABLED=true` is mandatory; the Patroni leader cert is
self-signed, so `DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED=false` keeps the TLS
envelope but skips chain/hostname verification — the n8n analogue of Outline's
`sslmode=no-verify`. (Both retire together once the internal-CA / cert-manager
work replaces the self-signed Patroni cert.)

`DB_TYPE` is **`postgresdb`**, NOT `postgres` — any missing/typo'd DB var
silently falls back to SQLite.

The `n8n` DB + role were created by `ansible/.../postgres-common` run **from
Frigg** — the first real exercise of Frigg's remote-host Ansible after the
SSH-key shim fix (the MacBook can't run it: the Python-3.14 `community.hashi_vault`
fork crash). `postgres-common` must target the current Patroni leader.

## Secrets

One `ExternalSecret` (`n8n-app`) renders a Secret consumed via `envFrom`. Both
Vault paths are minted by `terraform/vault/main.tf` and exist at first deploy
(no resolve-order split needed, unlike Outline):

| Vault path | Field | Env var | Purpose |
|---|---|---|---|
| `secret/k8s/n8n/app` | `encryption_key` | `N8N_ENCRYPTION_KEY` | the one load-bearing secret — encrypts every stored credential in Postgres |
| `secret/k8s/n8n/postgres-password` | `value` | `DB_POSTGRESDB_PASSWORD` | DB auth |

All other config (hostnames, DB connection, proxy posture, task runners) is
non-secret and templated into the same Secret per the repo convention. Until the
Vault module is applied the ExternalSecret sits in `SecretSyncedError` and the
Deployment waits — that's the expected "waiting on TF" state, not a bug.

## Reverse-proxy posture

The **editor** base URL (`N8N_EDITOR_BASE_URL`) is the internal niflheim host;
the **webhook** base URL (`WEBHOOK_URL`) is the public apex, so webhook nodes
advertise a publicly-reachable URL. n8n supports `WEBHOOK_URL` independent of
`N8N_EDITOR_BASE_URL` — that split is the whole point of the design.

Traefik terminates TLS and is the single proxy hop n8n directly trusts
(`X-Forwarded-Proto=https`): `N8N_SECURE_COOKIE=true`, `N8N_PROXY_HOPS=1`
(Express `trust proxy` = 1). The webhook path is
client → Cloudflare → cloudflared → Traefik, but n8n only directly trusts
Traefik.

`N8N_RUNNERS_ENABLED=true` — task runners are the current code-execution model;
running without them is deprecated and logs a warning.

## Operations

- **Bump version:** verify the newest stable at
  <https://hub.docker.com/r/n8nio/n8n/tags>, then update the pinned tag in
  `k8s/asgard/apps/n8n/deployment.yaml` and push (Flux reconciles). Recreate
  strategy means a brief gap during the migration on restart.
- **Health:** `/healthz` (liveness), `/healthz/readiness` (returns 200 only once
  the DB connection is up + migrations done — keeps the pod out of Service
  rotation during the first-boot migration window).
