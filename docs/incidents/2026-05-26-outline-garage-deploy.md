<!-- docs/incidents/2026-05-26-outline-garage-deploy.md -->

# 2026-05-26 — Outline + Garage deploy (S3 layer net-new)

End-to-end deploy of Outline wiki backed by Garage object storage in asgard K3s. Garage is the homelab's first S3-compatible storage layer (MinIO ruled out post-OSS-pivot; SeaweedFS skipped because POSIX/WebDAV is redundant with the existing iSCSI+NFS surfaces). Outline is the first consumer; future consumers planned: Immich, backup targets. Sixteen findings surfaced — bootstrap chicken-and-eggs, distroless container ergonomics, Synology iSCSI capacity ceiling, Node-side PG TLS posture, Patroni leader-discovery gap, plus reaffirmations of three pre-existing rules. All new classes now in [CLAUDE.md "Known gotchas"](../../CLAUDE.md).

## Outcome

- Garage v2.3.0 single-node StatefulSet (`garage` namespace), RF=1 dangerous-consistency LMDB, 2 PVCs (`meta` 10Gi + `data` 200Gi on `synology-csi-iscsi-retain`), one S3 bucket `outline` + one access key. Layout assigned via admin API v2 from an alpine+curl+jq bootstrap Job (NOT a CLI sidecar — Garage's image is `FROM scratch`).
- Outline 1.8.0-1 (`outlinewiki/outline` on Docker Hub) Deployment, 1 replica, `Recreate` strategy, UID 1001, Redis sidecar (emptyDir, ephemeral). Postgres via Patroni HAProxy VIP `10.0.10.210` with `sslmode=no-verify` (self-signed cert chain). OIDC via Authentik.
- Three hostnames live: `wiki.xiiisins.com` (external via cloudflared tunnel), `wiki.midgard.xiiisins.com` (LAN fast-path), `wiki.niflheim.xiiisins.com` (internal-only). All return 200 + redirect through OIDC + land on the workspace.
- New TF module `terraform/garage/` (jkossis/garage v1.0.4 provider). Touches: `terraform/vault/` (10 resources — Garage admin token, Outline app secrets, OIDC consumer), `terraform/authentik/` (6 resources — provider, group, binding, application), `terraform/cloudflare/` (1 resource — CNAME), `terraform/adguard/` (2 resources — `wiki.niflheim` + `wiki.midgard` rewrites).

## Pre-flight context

- Patroni cluster `niflheim-pg` 3/3 streaming on Fulla/Vör/Idunn, HAProxy VIP `10.0.10.210` (Phase 5g.2). Leader at deploy start: **Idunn** (had switched from Fulla via earlier patronictl restart for unrelated params).
- Authentik 2026.2.3 with `terraform/authentik/` identity-as-data pattern (users.yaml + groups.yaml) live since Phase 5e.3.
- Cloudflared tunnel + apex zone live since Phase 5e.2 (`apex-static` Caddy pod handles WebFinger).
- AdGuard Terraform rewrites pattern live since Phase 5b.2 (gmichels/adguard, write-to-origin Saga, adguardhome-sync fans to Mimir/Kvasir on `*/1` cron).
- ExternalSecrets Operator + Vault KV pattern stable since Phase 4. CoreDNS rewrite pattern for K8s-fronted FQDNs in-cluster (Phase 5i).
- Garage: net-new. No prior S3 layer in the homelab — operator-side experience but zero in-tree precedent.

## Timeline

1. **Drafted in worktree** `feat/outline-garage` — Garage manifests + Outline manifests + 4 TF module diffs. Rebased against in-flight Hermod work, ff-merged to main.
2. **`terraform apply` in `terraform/vault/`** — minted Garage + Outline secrets, 10 resources.
3. **`git push origin main`** — Flux reconciled. Garage namespace + StatefulSet landed; first iteration of bootstrap Job (`command: [sh, -c, ...]` against the Garage image) entered CrashLoopBackOff with `exec: "sh": executable file not found`.
4. **Bootstrap rewrite** to alpine:3.20 sidecar Job calling admin API v2 (`/v2/GetClusterStatus`, `/v2/GetClusterLayout`, `/v2/UpdateClusterLayout`, `/v2/ApplyClusterLayout`) via curl+jq. Hit the next layer: garage-0 stuck `Init:0/1` (chown waiting on the data PVC mount, which was waiting on ~20 min of mkfs.ext4 over iSCSI). Bootstrap Job's DNS lookup for `garage-0.garage-headless.garage.svc` returned NXDOMAIN because the pod was never `Ready`.
5. **Headless Service fix** — added `publishNotReadyAddresses: true` so the Job could reach garage-0 before garage-0's own readiness criteria (layout assigned) were satisfied. Bumped Job's wait loop from 60×2s to 150×2s, `backoffLimit: 30` — total ~2.5h budget against the mkfs reality.
6. **Job immutability** — Flux failed to apply the rewritten Job: `Job.batch "garage-layout-init" is invalid: spec.template: ... field is immutable`. `kubectl delete job/garage-layout-init` cleared the path; next Flux reconcile created fresh.
7. **garage-0 Ready ~21 min after first mount attempt.** Layout-init succeeded on iteration ~12 (API responsive once layout was applied — small subset of the wait budget actually consumed).
8. **`kubectl port-forward -n garage svc/garage-admin 3903:3903 &`** + **`terraform apply` in `terraform/garage/`** — 4 resources (bucket, key, key→bucket permissions, Vault secret `secret/k8s/outline/s3`). Provider schema surprises: `endpoint` single field, `global_alias` single string, `garage_key.X.id` IS the access_key_id.
9. **Outline pod ImagePullBackOff** — first draft pinned `ghcr.io/outline/outline:1.8.0-1`; GHCR returned 403 anonymously. Re-pinned to `outlinewiki/outline:1.8.0-1` on Docker Hub.
10. **Outline pod admission denied** — `runAsNonRoot: true` against an image with named USER (`nodejs`): "container has runAsNonRoot and image has non-numeric user (nodejs)". Set `runAsUser: 1001` explicitly.
11. **Outline crash on PG connect** — `SequelizeConnectionError: self-signed certificate; if the root CA is installed locally, try running Node.js with --use-system-ca`. Patroni's cert is self-signed; pg-connection-string + sequelize interpret `sslmode=require` as `verify-full`. Changed to `?sslmode=no-verify`.
12. **PG role/DB missing** — Outline crashed at first query: `database "outline" does not exist`. Ran `ansible-playbook playbooks/postgres-host.yml --limit fulla --skip-tags baseline,postgres,patroni,hardening -e '{"patroni_is_leader": true}'`. Failed with `cannot execute CREATE ROLE in a read-only transaction` because Fulla was a replica. Checked `patronictl list` from Hlin → leader was Idunn. Re-ran with `--limit idunn`; `outline` role + DB landed cleanly.
13. **ExternalSecret stuck `SecretSyncedError`** — Outline's ExternalSecret referenced 4 Vault paths (postgres, redis, s3, oidc); s3 + oidc didn't exist yet. ESO blocks materialization on ANY unresolved ref, so outline-redis (which only needs `redis_password`) was also starved. Split into per-cohort ExternalSecrets.
14. **`terraform apply` in `terraform/authentik/`** — minted OIDC provider, application, `outline-users` group, group-policy binding, Vault `secret/k8s/outline/oidc`. 6 resources.
15. **ESO force-sync** via `kubectl annotate externalsecret outline-app force-sync=$(date +%s) --overwrite` + pod restart. Outline reconciled, came healthy, internal route worked.
16. **`terraform apply` in `terraform/adguard/`** — 2 rewrites (`wiki.niflheim` + `wiki.midgard` → Traefik VIP `10.0.20.10`). `terraform/cloudflare/` apply blocked: cached `CLOUDFLARE_API_TOKEN` invalid. `homelab-env --refresh` re-pulled stale value from 1P; the 1P entry was ALSO invalid (operator-side rotation hadn't propagated). Verified with `curl /user/tokens/verify` → not active. Operator minted fresh token in CF dashboard, updated 1P, re-refreshed.
17. **VAULT_TOKEN expired mid-deploy** (~6h in) — `terraform apply` against `terraform/cloudflare/` errored `permission denied`. Recovered via `fish -c 'homelab-env; set-vault-token root; homelab-env --refresh'`. Re-applied cleanly — 1 resource (`wiki.xiiisins.com` CNAME).
18. **`kubectl rollout restart deployment/cloudflared`** — picked up the new ingress rule (cloudflared `config.yaml` is subPath-mounted, doesn't hot-reload).
19. **Browser OIDC flow** — Authentik denied with `Policy binding 'None' returned result 'False'`. Operator wasn't in `outline-users`. Added group to `users.yaml`, `terraform apply terraform/authentik/`. Re-ran login flow → callback succeeded → Outline workspace claimed.
20. **End-to-end validated** — all three hostnames return 200; OIDC discovery + redirect + authorize + callback + Outline session; S3 round-trip via document upload (PUT to Garage bucket, GET on render); Postgres writes persist across pod restart.

## Findings

### 1 — `dxflrs/garage:v2.3.0` is `FROM scratch` — no shell, no busybox

**Trigger.** First-draft layout-init Job used `command: [sh, -c, 'garage layout assign ...']` against the Garage container image. CrashLoopBackOff at start:
```
exec: "sh": executable file not found in $PATH
```

**Cause.** The Garage image ships only the `garage` binary (no shell, no coreutils, no `/bin`). Any `sh -c`, `bash`, multi-step pipeline, or interactive entrypoint fails immediately.

**Fix.** Drive cluster setup via Garage's admin API v2 (`/v2/GetClusterStatus`, `/v2/GetClusterLayout`, `/v2/UpdateClusterLayout`, `/v2/ApplyClusterLayout`) from a separate `alpine:3.20` + `apk add --no-cache curl jq` sidecar Job that hits the in-cluster admin Service.

**Generalisation.** For any distroless / scratch / minimal-OCI container that needs out-of-band logic (cluster bootstrap, schema init, layout assignment), do it from an external Job that talks to the service's API — don't try to `exec` shell logic into the target image. Same class as Postgres init-containers historically using a separate `bitnami/postgresql` rather than the upstream `postgres` image.

### 2 — Bootstrap chicken-and-egg: Garage `/health` 503s until layout assigned, but layout-init can't reach the pod via headless Service until pod is `Ready`

**Trigger.** First reconcile after bootstrap-Job rewrite: garage-0 stuck `Init:0/1` (chown init waiting on data PVC), layout-init in CrashLoopBackOff with:
```
Could not resolve host: garage-0.garage-headless.garage.svc.cluster.local
```

**Cause.** Two-way deadlock. Garage's readiness probe checks `/health`, which returns 503 until a cluster layout has been applied. The headless Service that exposes the pod by hostname (so the layout-init Job can reach it) excludes not-ready pods by default. Pod isn't Ready → not in endpoints → DNS NXDOMAIN → layout-init can't bootstrap → pod never gets Ready.

**Fix.** `publishNotReadyAddresses: true` on the headless Service. Pod becomes resolvable as soon as it has an IP, before its own readiness criteria are satisfied. Layout-init reaches it, applies the layout, Garage's `/health` flips to 200, pod becomes Ready.

**Generalisation.** Any service where bootstrap requires hitting the pod via Service DNS BEFORE the pod can self-declare Ready needs `publishNotReadyAddresses: true` on at least one of its Services (typically the headless one used for peer/admin access, not the public ClusterIP). Pattern applies to: cluster-init systems (etcd, Patroni's REST during init, future Garage multi-node), schema-migration sidecars where the app readiness depends on migration completion, anything with a chicken-and-egg liveness gate.

### 3 — `mkfs.ext4` on a 200Gi Synology iSCSI LUN takes ~20 min (TRIM-bound at ~10 GB/min over 1 GbE)

**Trigger.** chown init on garage-0 waited ~21 min for the data PVC to finish formatting. Layout-init's initial wait loop (60 iterations × 2s = 2 min) expired ten-plus times during that window.

**Cause.** Synology CSI provisions thin LUNs; first mount runs `mkfs.ext4` against the unformatted block device. Modern e2fsprogs issues TRIM/DISCARD across the full LUN as part of format (lazy-itable-init is on but discard is synchronous unless explicitly disabled). Over 1 GbE iSCSI to the DS223J spinners, sustained discard tops out ~10 GB/min. Kernel diagnostic on the worker: `cat /sys/block/sdX/stat` field 14 (sectors discarded) grows linearly during the format.

**Fix.** Bootstrap Job wait loop bumped to 150 iterations × 2s + `backoffLimit: 30` → ~2.5h total budget. Realised this is a one-time cost per PVC (subsequent mounts are seconds).

**Generalisation.** For any Synology iSCSI PVC >50 Gi, expect first-mount mkfs delays in the 15–30 min range; size bootstrap Job timeouts + initial-deploy expectations accordingly. Not a candidate for `mkfs -E nodiscard` tuning in the CSI driver — discard-on-format is the right default; the right answer is generous bootstrap timeouts. Now documented as a sizing rule under storage gotchas.

### 4 — Synology DS223J iSCSI LUN cap surfaces at 10/10 — provisioning the 11th PVC fails

**Trigger.** Outline-redis PVC creation stalled with CSI error:
```
Failed to create LUN, err: Number of LUN reach limit
```

**Cause.** All 10 existing LUNs on Munin were legitimate (live PVs, bound, in-use across vault/authentik/netbox/victoria/teamspeak/garage). Outline-redis was the 11th. DS223J's LUN cap appears to be 10 — possibly a DSM per-volume cap (UI-raisable?) or a model tier limit; investigation deferred.

**Fix (tactical).** Switched Outline-redis to `emptyDir`. Acceptable because Redis state for Outline is cache only (session signing keys are in the postgres-backed app state; Redis loss == re-login of in-flight sessions, no data loss).

**Fix (structural — open).** Investigate Synology DSM per-volume LUN cap settings. If UI-raisable, raise. If model-tier capped, accept that 10 PVCs is the homelab ceiling and audit emptyDir candidates for everything cache-class. Logged as open question.

**Generalisation.** Capacity surfaces are discovered at the moment they bite. Cluster-wide PV count vs NAS LUN cap is now a tracked metric in the asgard-health script. Any new K8s app shipping with multiple persistent stores should be reviewed for cache-vs-state separation before drafting PVCs.

### 5 — Outline image lives on Docker Hub at `outlinewiki/outline`, NOT `ghcr.io/outline/outline`

**Trigger.** First Outline manifest pinned `ghcr.io/outline/outline:1.8.0-1`. ImagePullBackOff with `403 Forbidden` from GHCR (anonymous pull denied; the repo exists but is not anonymously accessible).

**Cause.** Outline publishes to Docker Hub under `outlinewiki/outline`. The GHCR mirror, if it exists, isn't anonymously pullable.

**Fix.** Repinned to `docker.io/outlinewiki/outline:1.8.0-1`. Verify image paths at `https://hub.docker.com/r/outlinewiki/outline/tags` when bumping versions.

**Generalisation.** When pinning a new upstream image, verify the registry path against the project's documented canonical source (usually their README's docker-compose example or `docker pull` snippet). Don't assume `ghcr.io/<org>/<name>` follows from `github.com/<org>/<name>` — many projects publish to Docker Hub primarily and either don't mirror to GHCR or restrict GHCR to authenticated pulls.

### 6 — `runAsNonRoot: true` rejects images whose USER directive is a name, not a numeric UID

**Trigger.** Outline pod admission denied:
```
Error: container has runAsNonRoot and image has non-numeric user (nodejs), cannot verify user is non-root
```

**Cause.** K8s admission can't verify "is this user non-root?" from a string username — it needs a numeric UID it can compare against zero. The Outline image's `USER nodejs` is a name; admission punts.

**Fix.** Set `runAsUser: 1001` (Outline image's nodejs is UID 1001) explicitly in the pod spec. `runAsNonRoot: true` remains as a defense-in-depth assertion (admission now has both data points).

**Generalisation.** Any time an image uses a named USER, the consuming pod spec MUST set `runAsUser: <numeric>` to pass admission with `runAsNonRoot: true`. Affects: Authentik (`nobody`), some Postgres community images (`postgres`), various Node/Python upstream images (`node`/`app`). Discover the UID from the image's docs or `docker inspect <image> --format '{{.Config.User}}'` + `getent passwd <name>` from inside a debug run.

### 7 — Outline (pg-connection-string + sequelize) treats `sslmode=require` as `verify-full` and rejects self-signed PG certs

**Trigger.** Outline pod crash on first PG connect:
```
SequelizeConnectionError: self-signed certificate; if the root CA is installed locally,
try running Node.js with --use-system-ca
```

**Cause.** Patroni issues a self-signed cert (homelab-internal CA, not a public chain). The libpq spec defines `sslmode=require` as "TLS yes, don't verify chain". Modern Node ecosystems (pg-connection-string + sequelize) have tightened that default: `require` now implies chain verification + hostname match. Self-signed fails immediately.

**Fix.** `?sslmode=no-verify` in the PG URL. TLS envelope satisfied, no chain/hostname check. Acceptable for in-cluster traffic over a private VLAN.

**Generalisation.** Modern Node-side PG drivers default to verify-full at `sslmode=require`. Review the SSL mode setting whenever a Node consumer is added against a self-signed PG. Same class for any application that's not a libpq-direct C client; psycopg2/psycopg3 still honor libpq semantics, but JDBC + Node + Go drivers each have their own opinion. The right fix long-term is a private CA + sidecar trust injection — deferred until more Node-PG consumers land.

### 8 — ExternalSecret with multiple `data` refs blocks ALL secret materialization if ANY ref doesn't resolve

**Trigger.** Outline manifest declared a single ExternalSecret `outline-app` covering 4 Vault paths (postgres, redis, s3, oidc). At first deploy, only postgres + redis existed (terraform/garage and terraform/authentik ran later in the sequence). ESO status:
```
SecretSyncedError: secret store error: secret not found
```
Both `outline-app` AND `outline-redis` (which only references `redis_password` — a path that DID exist) were stuck. The Outline pod waited on `outline-app`; the Redis sidecar pod waited on `outline-redis` — but `outline-redis` was a separate ExternalSecret, so why was it blocked?

**Cause.** It wasn't blocked by ESO directly — it was blocked because the operator had grouped both ExternalSecrets in the same Kustomization and Flux was timing out the whole reconcile. Once `outline-app` started erroring, the Kustomization wedged. ESO itself does block per-ExternalSecret on any unresolved ref, though — which is the more fundamental rule.

**Fix.** Split into per-cohort ExternalSecrets: `outline-pg`, `outline-redis`, `outline-s3`, `outline-oidc`. Each maps to one Vault path. Outline's Deployment mounts all four as separate envFrom blocks. Redis sidecar mounts only `outline-redis`. Each cohort materializes as its TF source apply completes; consumers come up incrementally.

**Generalisation.** Any time consumers have separate readiness requirements OR the source Vault paths are minted by different TF modules at different times, give each cohort its own ExternalSecret. Single-ExternalSecret-with-many-refs is fine ONLY when all refs land atomically (one TF module, one apply). The pattern generalises: bundle by mint-time-cohort, not by consumer-shape.

### 9 — Postgres-common DB provisioning needs the CURRENT Patroni leader, not a hard-coded host

**Trigger.** `ansible-playbook playbooks/postgres-host.yml --limit fulla --skip-tags baseline,postgres,patroni,hardening -e '{"patroni_is_leader": true}'` errored:
```
ERROR: cannot execute CREATE ROLE in a read-only transaction
```

**Cause.** The deploy notebook reflexively used `--limit fulla` (Fulla was leader at the last PG-touching deploy). Between then and now, an unrelated `patronictl restart` had switched leadership to Idunn. Fulla was now a hot standby — replication slot streaming, but PG itself in read-only mode. The `-e patroni_is_leader=true` override forced the role into bootstrap path, but the underlying PG rejected the writes.

**Fix.** `patronictl -c /etc/patroni/patroni.yml list` from any node (Hlin used) → identifies the current leader → re-run with `--limit idunn`. Bootstrap completed cleanly.

**Generalisation.** Bake leader-discovery into the playbook. Two options on the table:
- (a) Query DCS directly: etcd `get /service/<scope>/leader` returns the leader hostname.
- (b) Hit Patroni REST `/cluster` on `:8008` from any node; parse `members[?role==leader].name`.
Option (b) is simpler (no etcd creds needed; REST is already exposed for HAProxy's `option httpchk`). Until baked in, the operator must discover the leader before any surgical postgres-common-databases run. Logged as open question. Documenting the recipe in the role's README is a stopgap.

### 10 — `include_tasks` does NOT propagate `--tags` to inner tasks (reaffirmed)

**Trigger.** Reaffirmation of the pre-existing gotcha. `--tags postgres-common-databases` matches the include statement in `postgres-common/tasks/main.yml`, but `databases.yml`'s inner tasks all silently no-op.

**Workaround used.** `--skip-tags baseline,postgres,patroni,hardening` against the full playbook to limit execution to the postgres-common role. Accept that all of postgres-common runs (users + databases tasks both fire); idempotent so no harm.

**Strict-mode boolean conditional** also reaffirmed: `-e '{"patroni_is_leader": true}'` (JSON object) — bare `-e patroni_is_leader=true` produces a string and trips strict mode.

**Generalisation.** No new rule — both already in CLAUDE.md. Surfacing twice in 24h (Teamspeak 5g close + Outline) confirms it's a structural recurring class. The "surgical postgres re-run wrapper" idea from the Teamspeak retro is overdue.

### 11 — Jobs are immutable — `kubectl delete job/X` is the Flux fix-deploy escape hatch

**Trigger.** Bootstrap Job rewrite (from `sh -c` to API-driven) failed to apply via Flux:
```
Job.batch "garage-layout-init" is invalid: spec.template:
Invalid value: ...: field is immutable
```

**Cause.** K8s Jobs are immutable post-creation by design (template, selector, parallelism, completions all fixed once `.status.startTime` is set). Flux's server-side apply can't patch the spec; the existing Job blocks updates.

**Fix.** `kubectl delete job/garage-layout-init -n garage` → next Flux reconcile creates fresh. Works for any Job-spec edit, not just first-deploy fix-ups.

**Generalisation.** Whenever a Job's spec changes in Git, manually delete the in-cluster Job before expecting Flux to land the new version. Document in CLAUDE.md as a Flux/Helm/Kustomize gotcha: `kubectl delete job/<name>` is the standard reconcile-unblocker for Job-spec edits.

### 12 — subPath-mounted ConfigMap doesn't hot-reload (reaffirmed)

**Trigger.** Cloudflared's `config.yaml` (the ingress rules ConfigMap, mounted via subPath) was updated in Git; Flux reconciled the ConfigMap cleanly; cloudflared pods kept serving the snapshot from their start time. `wiki.xiiisins.com` external traffic 404'd at the tunnel.

**Fix.** `kubectl rollout restart deployment/cloudflared -n cloudflared` → pods picked up the new config.

**Generalisation.** No new rule — already in CLAUDE.md under "subPath-mounted ConfigMap/Secret keys do NOT auto-update in running pods". Reaffirmed in the cloudflared context. Worth considering stakater/reloader for cloudflared specifically, since this is the third time it's bitten (apex-static, factorio, cloudflared).

### 13 — jkossis/garage Terraform provider v1.0.4 schema diverges from common assumption

**Trigger.** First-draft `terraform/garage/` module failed plan with field-not-found errors against the obvious-looking attribute names.

**Cause.** Provider's schema:
- **Provider block:** `endpoint = "http://host:port"` — single field, NOT split `host` + `port` + `scheme`.
- **Bucket resource:** `global_alias = "name"` — single string, NOT `global_aliases = ["name"]` list.
- **Access key resource:** the `id` attribute IS the access_key_id. There's no `access_key_id` field directly on the resource; downstream consumers reference `garage_key.outline.id`.

**Fix.** Conformed to actual schema. Diagnose with `terraform providers schema -json | jq '.provider_schemas | to_entries[] | .value.resource_schemas'` — same first-line-diagnostic rule used for the Tailscale provider schema-shape discovery.

**Generalisation.** Already-codified rule: for any new TF provider, run `terraform providers schema -json` first; never trust the README's example block to match the installed version's schema. Documenting per-provider field divergence as gotchas in CLAUDE.md as they surface.

### 14 — Cloudflare API token rotations don't notify consumers — 1Password value can be silently invalid

**Trigger.** Two failures back-to-back during the cloudflare TF apply:
- `homelab-env` cache had a stale token (from before an earlier-day rotation triggered by a BSD-sed redaction leak — separate incident).
- `homelab-env --refresh` re-pulled from 1P; the 1P entry was ALSO invalid because the operator's rotation hadn't propagated to 1P yet.

Verified via:
```bash
curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  https://api.cloudflare.com/client/v4/user/tokens/verify | jq
# → success: false, status: not active
```

**Fix.** Operator minted a fresh token in the Cloudflare dashboard (Account → API Tokens → token row → Roll), updated the 1P "Cloudflare - Terraform - DNS+WAF+Tunnel+BotMgmt" entry, `homelab-env --refresh`, re-verified with the curl above, then re-applied.

**Generalisation.** 1Password stores secrets; it can't validate them. Any rotation-prone external token (Cloudflare API token, Tailscale OAuth client secret, NetBox admin token) needs a verify step BEFORE consuming. Pattern: `homelab-env --refresh && verify-cf-token && terraform apply`. Add `verify-cf-token` shim that wraps the `/user/tokens/verify` curl; fail-fast before TF burns plan-time. Logged as a tooling open question.

### 15 — VAULT_TOKEN expires during long deploys

**Trigger.** ~6h into the deploy, `terraform apply` in `terraform/cloudflare/` errored:
```
Error: permission denied
Error: invalid token
```

**Cause.** The cached VAULT_TOKEN had aged out. Vault TTL for root tokens is configurable but not infinite; in this case the token's TTL was shorter than the deploy duration.

**Fix.**
```bash
fish -c 'homelab-env; set-vault-token root; homelab-env --refresh'
```
`set-vault-token root` re-mints from 1P; `homelab-env --refresh` propagates to the env cache.

**Generalisation.** Any TF apply against a chain longer than the VAULT_TOKEN TTL needs re-verification at each module boundary. Two paths:
- (a) Re-verify VAULT_TOKEN before each `terraform apply` in long deploys (`vault token lookup` succeeds = good; fails = re-mint).
- (b) Use a longer-TTL or non-expiring token for orchestrated deploys (security tradeoff — homelab-acceptable for root, less so for AppRole).

Option (a) preferred. Add to the same `verify-cf-token`-class tooling shim — `verify-vault-token` wrapping `vault token lookup`. Logged as open question.

### 16 — Authentik group-binding gates require explicit user membership

**Trigger.** First OIDC login attempt to Outline. Authentik flow log:
```
Policy binding 'None' returned result 'False'
```
Operator was denied at the consent screen.

**Cause.** Outline's Authentik OIDC provider has `policy_engine_mode: "any"` + a group-policy binding to `outline-users`. The operator's user entry in `terraform/authentik/users.yaml` didn't list `outline-users` in `groups:`. Authentik enforces the binding; no match → denied.

**Fix.** Added `outline-users` to the operator's `groups:` list in `users.yaml`, `terraform apply terraform/authentik/`. Re-ran login → callback succeeded.

**Generalisation.** Every per-app Authentik group gate is opt-in per user. Adding a new app means:
1. Mint the group in `groups.yaml` (TF creates).
2. Bind the group to the provider in `terraform/authentik/<app>.tf`.
3. Add the group to each authorized user's `groups:` list in `users.yaml`.

Step 3 is easy to forget when the operator is the only initial user (the implicit "I'm the admin" assumption doesn't translate to Authentik's explicit group model). Now a per-app deploy checklist item.

## Patterns

Five cross-cutting rules emerge from this deploy:

1. **Any service whose readiness gate requires a bootstrap interaction has chicken-and-egg risk.** Garage's `/health` 503 until layout assigned is the prototype. Fix: `publishNotReadyAddresses: true` on the bootstrap-targeted Service. Generalises to any cluster-init system, schema-migration sidecar, or self-bootstrapping operator.

2. **Distroless / scratch / minimal-OCI containers need out-of-band logic via API, not in-band via shell.** Garage's `FROM scratch` is the prototype. Pattern: separate alpine+curl+jq Job, hit the target's admin API. Don't try to exec into a shell that doesn't exist.

3. **Modern application drivers default to stricter TLS than libpq's `sslmode=require`.** Node/Sequelize is the prototype here, but the class is broader (JDBC, Go's pgx, .NET Npgsql all have their own opinions). Self-signed PG (Patroni's default) requires `sslmode=no-verify` for these consumers, OR a real CA + trust injection. The right long-term answer is a homelab CA; the right tactical answer is `no-verify` per consumer with a documented exception.

4. **Any TF apply chain longer than VAULT_TOKEN TTL needs re-verification at module boundaries.** Same class for any rotation-prone external token (Cloudflare, Tailscale OAuth, NetBox admin). 1Password stores; it can't validate. Wrap with `verify-X-token` shims; fail fast before TF burns plan-time. Surfaced 2× this deploy alone (Vault + Cloudflare).

5. **Multi-source ExternalSecrets bundle reconcile timing.** When a single ExternalSecret references paths minted by different TF modules at different times, the consumer waits on the slowest. Split by mint-time-cohort, not by consumer-shape. The Outline-redis-blocked-by-Outline-app-failure pattern was the giveaway.

## What landed in CLAUDE.md

New gotcha classes added (or scheduled for the post-flight commit):

- **Storage / iSCSI / Synology CSI:** Synology DS223J 10-LUN cap; mkfs.ext4 on Synology iSCSI scales at ~10 GB/min over 1 GbE (size bootstrap timeouts accordingly).
- **K8s scheduling:** `runAsNonRoot: true` admission rejects named-USER images; set `runAsUser: <numeric>` explicitly.
- **K8s scheduling / Flux:** Jobs are immutable — `kubectl delete job/X` is the Flux fix-deploy escape hatch.
- **K8s scheduling / Services:** `publishNotReadyAddresses: true` on bootstrap-targeted headless Services to break self-readiness chicken-and-eggs.
- **Container ergonomics:** Distroless / `FROM scratch` containers need out-of-band logic via API; don't exec shell into them.
- **Postgres / Node clients:** Node/Sequelize + pg-connection-string default to verify-full on `sslmode=require`; use `sslmode=no-verify` against self-signed Patroni.
- **ExternalSecrets:** Split ExternalSecrets by mint-time-cohort; bundling many refs starves consumers whose refs are individually ready.
- **Terraform / Vault / Cloudflare:** Long deploys need VAULT_TOKEN + CF_API_TOKEN re-verify at each module boundary; 1Password stores, can't validate.
- **Authentik:** Per-app group bindings need explicit user membership in `users.yaml`; document as a per-app deploy checklist item.
- **Garage provider:** jkossis/garage v1.0.4 schema specifics (`endpoint` single field, `global_alias` single string, `garage_key.X.id` IS the access_key_id).
- **Image registry paths:** Outline lives at `outlinewiki/outline` on Docker Hub, NOT GHCR.

Reaffirmations (already in CLAUDE.md, surfaced again):

- `include_tasks` doesn't propagate `--tags` (Ansible).
- Strict-mode boolean conditionals need JSON `-e` (Ansible).
- subPath-mounted ConfigMaps don't hot-reload (K8s/Flux).

## Open questions raised

- **Synology DSM LUN cap investigation.** Is the 10-LUN ceiling raisable via DSM UI? Per-volume? Model-tier? If unraisable, audit existing PVCs for emptyDir-candidates (cache-class state) and document a cluster-wide PV-count metric in asgard-health.
- **Patroni leader-discovery automation in postgres-common.** Bake REST `/cluster` query (or DCS lookup) into the playbook so `--limit fulla` vs `--limit idunn` stops being an operator-managed knob. Two options on the table — REST is simpler.
- **`verify-X-token` tooling shims.** Wrap Cloudflare `/user/tokens/verify`, Vault `token lookup`, Tailscale OAuth introspect into a `verify-<service>-token` family; call before every TF apply in long deploy chains.
- **Homelab CA + sidecar trust injection.** Sequelize-vs-self-signed-Patroni was tactically fixed with `sslmode=no-verify`; structural answer is a private CA. Defer until more Node-PG consumers land — Outline alone doesn't justify the lift.
- **stakater/reloader for cloudflared.** Third time subPath-ConfigMap hot-reload has bitten (apex-static, factorio, cloudflared). May be worth deploying reloader cluster-wide.
