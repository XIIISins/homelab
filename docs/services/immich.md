<!-- docs/services/immich.md -->
# Immich

Self-hosted photo/video library for the operator + direct family — a
**secondary copy** alongside iCloud/Google Photos, not the sole copy. That
framing drove the redundancy posture below (single-NAS RAID1, no PBS, no
offsite — a deliberate, stated tradeoff, not a silent gap).

- **Manifests:** `k8s/asgard/apps/immich/`
- **Chart:** official `oci://ghcr.io/immich-app/immich-charts/immich`, pinned `0.13.1` (app `v3.0.0`) — built on the bjw-s common-library chart
- **Deployed:** 2026-09-03
- **Terraform:** `terraform/authentik/immich.tf` (native OIDC provider + app + `immich-users` gate), `terraform/vault/main.tf` (PG password), `terraform/cloudflare/` (apex CNAME), `terraform/adguard/` (midgard rewrite)
- **Hostnames:** `immich.xiiisins.com` (external, cloudflared) + `immich.midgard.xiiisins.com` (LAN-fast-path) — no niflheim-only route; family-facing, not operator-only

## Storage — NFS, not Garage (corrects the original design assumption)

Immich has **no native S3 backend** — confirmed against the maintainers
directly ([immich-app/immich discussion #23745](https://github.com/immich-app/immich/discussions/23745)),
who describe object-storage support as a "long term endeavour" still needing
groundwork. `UPLOAD_LOCATION` (originals, thumbnails, encoded video, profile
pics, DB backups — all together, one mount, no per-subdir override) is a PVC
on the **existing** `nfs-client` StorageClass — same `k8s-nfs` export
MicroBin already uses, not a new dedicated share. Full rationale:
[`decisions.md`](../operations/decisions.md) "Immich storage backend — NFS, not Garage".

**Sizing is nominal, not a real allocation.** `csi-driver-nfs` creates one
plain subdirectory per PVC inside the configured export and enforces no
per-PVC quota; the DSM shared folder carries none either. The real ceiling
is the underlying DSM volume (Volume2, "K8s NFS"), left untouched at its
current size on first deploy per the operator's call — growing it later is
a live, online DSM Storage Manager operation needing **no Kubernetes-side
action at all**, not even a pod restart.

The one storage knob Immich *does* expose separately from the library:
`MACHINE_LEARNING_CACHE_FOLDER` (downloaded face-recognition/CLIP model
files — small, re-downloadable, not precious). That's on `local-path`
(existing per-worker 50G disk, `accessMode: ReadWriteOnce` — the chart's own
commented example suggests `ReadWriteMany`, which local-path can't satisfy;
override required).

## Postgres — extensions required (Immich-specific gotcha)

Immich hard-requires a vector-search extension at server startup (`vchord`
or `pgvector`) and its own migrations separately need `cube`+`earthdistance`
(geo "nearby" search) — the latter needs superuser to `CREATE EXTENSION`,
which the `immich` DB role isn't. Both pre-created via the generalized
`postgres_databases[].extensions` pattern in
`ansible/inventory/group_vars/postgres.yml` (see
[`known-issues/postgres.md`](../known-issues/postgres.md) for the full
gotcha + the reusable mechanism). `pgvector` is PGDG-packaged
(`postgresql-17-pgvector`, installed cluster-wide by the `postgres` role);
`cube`/`earthdistance` are core contrib, no extra package.

DB connection uses `DB_URL` (not discrete `DB_HOSTNAME`/etc.) so the SSL
query params can ride along:
`postgresql://immich:<password>@10.0.10.210:5432/immich?sslmode=require&uselibpqcompat=true`.
Patroni's `pg_hba` is `hostssl`-only (TLS mandatory) but the leader cert is
self-signed — plain `sslmode=require` still verifies the chain and fails.
`uselibpqcompat=true` is Immich's current officially-documented escape
hatch (confirmed via `docs.immich.app` + a live GitHub issue) — the Immich
analogue of Outline's `sslmode=no-verify` / n8n's
`DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED=false`.

DB + role created via `postgres-common` (leader-gated), same as every other
PG consumer.

## Redis/valkey — bundled, emptyDir

The chart ships its own valkey component (`valkey.enabled: true`), default
persistence `emptyDir` — used as-is, no hand-rolled sidecar needed (unlike
Outline, whose chart doesn't bundle one). Cache-class job-queue state, cheap
to rebuild on restart.

## Identity — native OIDC, manual activation

Immich has built-in OAuth support (unlike n8n/MicroBin, which need Traefik
ForwardAuth because their OSS editions lack native SSO) — but **no env-var
or safely-confirmed config-file path** to activate it declaratively.
`terraform/authentik/immich.tf` mints the OIDC provider + application +
`immich-users` group gate + client secret to Vault
(`secret/k8s/immich/oidc`) same as every other app, but wiring it into
Immich itself is a **one-time manual step**: log in via the local admin
account Immich's own first-run wizard creates, then paste
`issuer_url`/`client_id`/`client_secret` into Administration → Settings →
OAuth and enable it. Immich's `oauth` config-file block (in
`immich.configuration`) could theoretically automate this, but whether
setting it makes the Admin Settings UI read-only for those fields wasn't
confirmed at design time — deferred rather than guessed. Redirect URIs
registered: `{apex,midgard}/auth/login`, `{apex,midgard}/user-settings`,
and the mobile deep link `app.immich:///oauth-callback`.

## Deploy-time findings (2026-09-03)

Three real blockers surfaced getting this live, none of them Immich-shape
design mistakes — captured in known-issues so they don't get rediscovered:

- **PG extensions** (above) — Immich's own migration failing with
  `permission denied to create extension "earthdistance"` on a
  non-superuser role. See `known-issues/postgres.md`.
- **Stale Vault AppRole SecretIDs** on both the operator's control paths —
  `seed-vault-approle` re-stamps a file's mtime without actually minting a
  fresh SecretID from Vault, which looked like a successful re-seed but
  wasn't. See `known-issues/vault.md`.
- **`terraform/authentik` needs the root Vault token from the MacBook**,
  not the narrower `ansible-local` AppRole (scoped to `ansible/*` reads
  only) — a wall of 403s across every *pre-existing* app's OIDC secret in
  state, not just Immich's new one. See `known-issues/vault.md`.
- A zsh-specific bug (`local path` inside a function empties `$PATH`) in
  `.config/scripts/homelab.sh` was found + fixed along the way — unrelated
  to Immich itself, but it's what made the AppRole diagnosis possible in
  the first place. See `known-issues/shell-tooling.md`.

## Operations

- **Bump chart version:** check <https://github.com/immich-app/immich-charts/releases>
  for the latest `immich` tag, update the pin in
  `k8s/asgard/apps/immich/helmrelease.yaml`, push.
- **A stuck `HelmRelease Ready: False` after Helm exhausts its retry budget**
  does not self-heal on a plain `flux reconcile` even once the underlying
  issue is fixed — needs `flux reconcile helmrelease immich -n immich
  --force`. See `known-issues/vault.md` (filed there, not helm-specific to
  Immich).
- **Health:** `/api/server/ping` → `{"res":"pong"}`.
