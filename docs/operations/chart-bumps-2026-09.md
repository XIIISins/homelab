<!-- docs/operations/chart-bumps-2026-09.md -->

# Helm chart bump review — 2026-09-30

Review of every Flux-managed chart against upstream, done from a cloud session
that could read upstream git repos (no cluster access, no `helm`). **Nothing
here has been applied to the cluster.** This doc is the handoff for an
in-homelab session that can watch Flux reconcile and check live state.

- **Landed on branch `claude/intelligent-gates-csam4m`:** the safe batch (§1) +
  the Vault server image pin (§2). Merge → Flux applies.
- **Not landed, needs a plan:** §3–§8. Each has the breaking changes found, what
  to check on the live cluster first, and a suggested sequence.
- **Confidence:** stated per item. "Read" = I read the upstream changelog/release
  notes. "Diff only" = I diffed the chart and checked our values keys against
  it, but upstream release notes weren't reachable. Treat "diff only" as
  weaker evidence.

## Inventory (pinned → latest as of 2026-09-30)

| Chart | Pinned | Latest | Status |
|---|---|---|---|
| csi-driver-nfs | 4.13.2 | 4.13.4 | §1 landed |
| local-path-provisioner (git tag) | v0.0.36 | v0.0.37 | §1 landed |
| immich | 0.13.1 (app v3.0.0) | 0.13.2 (app v3.2.0) | §1 landed |
| victoria-logs-collector | 0.3.4 | 0.3.7 | §1 landed |
| victoria-logs-single | 0.12.5 | 0.13.9 | §1 landed |
| victoria-metrics-agent | 0.39.0 | 0.49.0 | §1 landed |
| victoria-metrics-single | 0.38.0 | 0.48.0 | §1 landed |
| kube-state-metrics | 7.4.0 | 8.6.0 | §1 landed |
| sealed-secrets | 2.18.6 | 2.20.0 | §1 landed |
| trust-manager | 0.23.0 | 0.25.0 | §1 landed |
| vault | 0.32.0 | 0.34.1 | §2 image pinned; chart bump = §3 |
| traefik | 40.2.0 | 41.6.0 | §4 |
| cert-manager | v1.19.0 | v1.21.2 | §5 |
| metallb | 0.15.3 | 0.16.1 | §5 |
| synology-csi | 0.11.1 | 0.11.4 | §6 |
| authentik | 2026.2.3 | 2026.8.3 | §7 |
| netbox | 8.2.17 (app v4.6.1) | 8.3.89 (app v4.7.1; 4.7.2 exists) | §8 |
| external-secrets | 0.20.4 | 2.11.0 | §9 |
| Flux (`gotk-components`) | v2.8.7 | v2.9.5 | §10 |

"Latest" = default-branch `Chart.yaml` or newest git tag; can run slightly ahead
of what's published to the chart repo. Confirm with `flux`/`helm` on the box
before pinning anything not on the branch.

Method for the values check: for each chart, every key we override in our
`HelmRelease.spec.values` was checked for existence in the new chart's
`values.yaml`. Keys absent in the *old* chart too are null-default keys (noise);
the only real removal found was Traefik `logs.*` (§4).

---

## 1. Safe batch — landed on the branch

No breaking changes found for our config. After merge, watch each HelmRelease
reconcile (`flux get hr -A`), then spot-check.

| Chart | Notes | Post-merge check |
|---|---|---|
| csi-driver-nfs 4.13.4 | Skimmed only. | An `nfs-client` PVC still mounts (MicroBin, Immich library). |
| local-path-provisioner v0.0.37 | Both the `GitRepository` ref **and** the image tag were bumped together (file header says to). | Vault PVCs (Raft) unaffected; new PVC binds on `local-path`. Provisioner pod Ready. |
| immich 0.13.2 (app v3.0.0 → **v3.2.0**) | Chart's only breaking change was 0.13.0 (already on it). **App** release notes NOT read. | `immich.xiiisins.com` loads, an upload works, ML job runs. App-side DB migration runs on first start — check pod logs. Immich needs pgvector/cube/earthdistance (already provisioned). |
| victoria-logs-single 0.13.9 (app → v1.52.0) | New `server.http` / `server.syslog` lists are additive; we use neither `httpListenAddr` nor syslog `extraArgs` (grep-verified). App notes NOT read. | VL UI loads; vlagent still ingesting (`logs.niflheim…/insert/native`); PVC (`local-path`/iSCSI as before) intact. |
| victoria-metrics-single 0.48.0 (app → v1.153.0) | Same `http` list change; additive. App notes NOT read. | vmui loads; the 4 custom dashboards still render (`customDashboardsPath`); scrape targets up. |
| victoria-metrics-agent 0.49.0 | Additive. | `vmagent` targets healthy; cAdvisor scrape (needs `nodes/proxy`, custom RBAC) still 200. |
| victoria-logs-collector 0.3.7 (vlagent → v1.52.0) | Additive. | DaemonSet rolls on all 3 workers; pod logs still flowing to VL. |
| kube-state-metrics 8.6.0 (app v2.20.0) | v8 major only drops `CiliumNetworkPolicy` / `networkPolicy.flavor`; we set neither. | `kube_*` series still present in vmui. |
| sealed-secrets 2.20.0 (controller 0.37 → 0.40) | Adds seccomp RuntimeDefault default. Diff only. | **Existing SealedSecrets still decrypt** (`kubectl get sealedsecret -A` all `Synced`). Keys are in 1P — untouched by a chart bump. |
| trust-manager 0.25.0 | Diff only. Adds default-package securityContext (seccomp RuntimeDefault). | `Bundle` for `homelab-internal-ca` still syncs; ESO/Vault CA trust unchanged. |

Roll back = revert the commit; Flux re-pins. None of these charts change CRDs in
a way we depend on, but sealed-secrets and trust-manager CRD schema versions did
change (controller-gen bump) — no action, just don't be surprised by a CRD diff.

---

## 2. Vault server image pin — landed on the branch

`k8s/asgard/infrastructure/vault/helmrelease.yaml` now sets
`server.image.{repository,tag}` = `hashicorp/vault:1.21.2`.

**Why:** the chart's default server image moves with the chart version.
`0.32.0` → Vault **1.21.2**; `0.33.x`/`0.34.x` → Vault **2.0.x** (`0.34.1`
ships 2.0.4). We did not pin it, so any routine chart bump would have been a
silent Vault **major** upgrade of the secrets store.

1.21.2 is the appVersion of the currently pinned chart, so this render is
**expected to be a no-op** (no pod restart). **Verify on the live cluster before
trusting that assumption:** `kubectl -n vault get pod vault-0 -o
jsonpath='{.spec.containers[0].image}'` should read `hashicorp/vault:1.21.2`. If
it reads anything else, the tag in the file is wrong — fix it to the running
version before merging.

The `injector` is disabled (`injector.enabled: false`), so `agentImage` /
vault-k8s tags don't matter here. CSI provider likewise off.

## 3. Vault chart 0.32.0 → 0.34.1 (and Vault 1.21 → 2.0) — do separately

Do the **chart** bump while keeping the image at 1.21.2; treat Vault 2.0 as its
own upgrade afterwards. Chart 0.33/0.34 changelog (read): default versions bump
to 2.0.x, adds Gateway API HTTPRoute support and Enterprise redundancy zones —
nothing that touches our values. Chart tested on K8s 1.32–1.36 (we're on 1.33,
fine).

**Vault 2.0.0 breaking/behaviour changes that hit us (read, from
`hashicorp/vault` CHANGELOG.md):**

1. **`sys/generate-root` and `sys/rekey` are now authenticated by default.** The
   old unauthenticated behaviour needs the new HCL key
   `enable_unauthenticated_access` including `"generate-root"` / `"rekey"`.
   This directly affects the **recovery path**: the open item "write a
   `vault operator generate-root` runbook"
   ([open-questions.md](open-questions.md)) must be written against the 2.0
   behaviour, and the root-token recovery procedure needs re-validating on 2.0
   before it's relied on. With AWS KMS auto-unseal the *rekey* here is the
   recovery-key rekey — same caveat.
2. **Non-canonical request paths are rejected** (e.g. `secret//data/x`,
   double slashes). Check anything that builds Vault paths by string
   concatenation: Ansible `vault_kv2_get` lookups, `terraform/vault`, ESO
   `remoteRef.key`, the `homelab.sh`/`vault-homelab-env` shims, Semaphore,
   `infra-health-check`. A trailing/leading slash bug that worked before will
   404/400 after.
3. `http` listener gains `max_token_header_size` (default 8 KB) — irrelevant
   unless a very large token/OIDC header is in play. Vault OIDC tokens through
   Traefik are well under.
4. Container images are exported as compressed OCI layout and UBI images move to
   UBI 10 — irrelevant to the `hashicorp/vault` (Alpine) image we use.

**Not read:** the Vault 2.0 *upgrade guide* on developer.hashicorp.com
(docs are not in the git repo) and anything in 2.0.1–2.0.4 point releases beyond
the chart notes. **Read the upgrade guide before 2.0.** Also confirm the
`hashicorp/vault` Terraform provider used in `terraform/vault/` supports 2.0.

**Chart 0.34.1 landed 2026-09-30 (`077e658`), verified in-homelab.** Live before:
3/3 unsealed on 1.21.2, `vault-2` active. Render diff 0.32.0 vs 0.34.1 with our
values: only the chart label plus a new startup-script guard that aborts only if
the raft config contains an `autopilot_redundancy_zone` placeholder (ours does
not); image stays 1.21.2. The StatefulSet is `updateStrategy: OnDelete`, so the
bump does **not** roll pods — the new spec (label + guard) only takes effect as
each pod is deleted. After Flux: HelmRelease `v10` Ready, all 3 pods still
unsealed on 1.21.2, UI 200, `/v1/sys/health` ok, `ClusterSecretStore` Valid,
19/19 ExternalSecrets synced. **Canary `vault-1` restarted 2026-09-30:** came
back Ready in ~10s on the 0.34.1 spec (no autopilot-guard error), auto-unsealed
from the KMS stored key, rejoined Raft as a voter, identical committed index on
all 3, UI 200, ExternalSecrets still 19/19. **Still pending:** `vault-0`
(standby), then the active `vault-2` last — until rolled they run the 0.32.0 pod
spec (functionally identical).

Suggested sequence: (a) merge §2 pin; (b) bump chart to 0.34.1 with image still
1.21.2; confirm Raft 3/3, unseal-on-restart works (AWS KMS reachable), OIDC login
OK; (c) later, plan 2.0: snapshot Raft first (`vault operator raft snapshot
save`), roll one pod at a time (Raft quorum, 3 voters — CLAUDE.md says Vault
accepted 2/3 during worker rebuilds), then re-test the generate-root runbook.

---

## 4. Traefik 40.2.0 → 41.6.0 — needs a values change

**Confidence: Read** (chart changelog + upgrade notes in the v41 diff).

Breaking chart change: the logging keys were renamed to match upstream Traefik.

- `logs.general` → `log` (e.g. `logs.general.level` → `log.level`)
- `logs.access` → `accessLog` (e.g. `logs.access.format` → `accessLog.format`)
- Filter/field keys camelCased (`filters.statuscodes` → `statusCodes`, …) and the
  `accessLog.fields.general` nesting removed.
- `providers.file.content` is now an object (`{}`), was a string — **we do not
  set it** (our `providers` block is `kubernetesGateway`/`kubernetesCRD`/
  `kubernetesIngress` only).
- Image `registry`/`repository` now default to `null` and are auto-resolved —
  we don't override the image.

Our `k8s/asgard/infrastructure/traefik/helmrelease.yaml` has (≈ line 113):

```yaml
logs:
  general:
    level: INFO
  access:
    enabled: true
    format: common
```

→ must become `log.level: INFO` and `accessLog.enabled: true` /
`accessLog.format: common`. **If left as-is the settings are silently ignored**
(access logs fall back to the new default format/off state; JSON noise or no
access log). Upstream ships a migrator: `hack/migrate/README.md` in
`traefik/traefik-helm-chart`.

**Verified 2026-09-30 (in-homelab session, live cluster + `helm template`):**
live release was 40.2.0 / image v3.7.1, 3/3 pods Ready, live values identical to
the repo. Correction to the above: chart 41's `values.schema.json` **rejects**
the old `logs` key (`additional properties 'logs' not allowed`), so leaving it
would fail the HelmRelease rather than silently ignore it. Rendered 40.2.0 vs
41.6.0 with the renamed values: the only diffs are the `helm.sh/chart` label and
image `v3.7.1` → `v3.7.13` (args, Service, anti-affinity, strategy identical).
CRD delta: `traefik.io_middlewares` gains an optional `errors.errorRequestHeaders`
field (additive) plus Hub CRDs (unused); the HelmRelease sets no `upgrade.crds`
policy so Flux skips CRD updates — harmless, nothing we use changed.

Also note in 41.x: a `safeNaming` option ("can be breaking", needs Traefik
v3.7.11+) — **leave it off**; it is opt-in. Proxy app goes v3.7.x → v3.7.13.

Everything else in our Traefik values (Gateway API provider, `service.spec`
loadBalancerIP `10.0.20.10` + `externalTrafficPolicy: Local`, required pod
anti-affinity, `NET_BIND_SERVICE`, redirect `entryPoint`) has no removal in the
new chart — the redirect/service/affinity keys were null-default noise in my
check, not removals. Still: **render before merging** (`flux build` /
`helm template` with our values) and diff the Deployment/Service vs the current
render; the CRD dir gained a `middlewares` change and `hub.traefik.io` CRDs
(Hub, unused by us).

Blast radius: Traefik fronts every HTTPRoute and terminates all TLS (niflheim +
midgard Gateways, cloudflared backchannel). Do it in a quiet window; VIP
`10.0.20.10` is pinned to a worker via MetalLB L2 + ETP Local so a rolling
update must keep one pod per worker (already `maxSurge 0 / maxUnavailable 1`).
Smoketest: `curl https://smoketest.niflheim.xiiisins.com/anything` → 200
"smoketest ok" (CLAUDE.md), plus one external (`wiki.xiiisins.com`) and one
Authentik ForwardAuth path (`vmui`).

---

## 5. cert-manager v1.19.0 → v1.21.2 and MetalLB 0.15.3 → 0.16.1

**Confidence: Diff only.** Their release notes live on the project websites /
GitHub Releases, which I couldn't reach. Chart diff found none of our overridden
keys removed. **Read these before merging:**

- cert-manager 1.20 and 1.21 release notes (cert-manager.io/docs/releases).
  cert-manager's own guidance is to upgrade one minor at a time — do
  1.19 → 1.20 → 1.21. Requires K8s ≥ 1.22 (fine). We use
  `enableGatewayAPI: true` via `config` and DNS-01 Cloudflare; both need
  re-checking against the notes. Check `Certificate` renewals still go `Ready`
  after (the three wildcards: niflheim, midgard, apex; plus `vault-tls` /
  `homelab-internal-ca`).
- MetalLB v0.16.0 / v0.16.1 release notes (metallb.io/release-notes). We run
  **L2 mode**; the visible chart change is FRR/BGP-only (`speaker.bgpDebounce…`).
  Pre-1.0 so read notes for CRD or L2 behaviour changes. `L2Advertisement`
  `nodeSelectors` (excludes CPs) is load-bearing — re-verify after upgrade, and
  the multi-homed-worker landmines (`known-issues/networking-multi-homed-workers.md`)
  are untouched by a chart bump but confirm VIPs (`.10`, `.11`, `.12`) still ARP.

Both are cluster-critical (TLS issuance; every VIP). Own PRs, one at a time.

---

## 6. synology-csi 0.11.1 → 0.11.4 — driver jumps v1.2.1 → v1.4.0

**Confidence: Read** (chart README/diff). **This is not a patch-level bump** — the
chart patch number hides a driver minor jump.

- **TLS verification now happens.** Since driver v1.3.1, DSM connections verify
  the certificate. DSM's default self-signed cert is issued for a *name*, but
  our `client-info` connects by IP (`10.0.254.20`). Upstream: "`tlsServerName` —
  required if `host` is an IP address but the certificate only has DNS names —
  the default for DSM self-signed certificates". Options in the client-info
  entry: `tlsCACert` (preferred), `tlsServerName`, or `insecureSkipVerify: true`
  (not recommended; warns every connection).
- **Our client-info is a SealedSecret**
  (`k8s/asgard/synology-csi-config/synology-secret.yaml`), so I could not see
  whether it sets `https: true` and what `port`. **First step for the in-homelab
  session:** inspect only the non-secret keys, filtering inside the shell so the
  password never reaches the transcript (CLAUDE.md never-echo-secrets rule):
  `kubectl -n synology-csi get secret synology-csi -o
  jsonpath='{.data.client-info\.yaml}' | base64 -d | grep -E '^\s*(- )?(host|https|port|tls\w*|insecureSkipVerify):'`
  (never print the whole file — it holds the DSM password).
  - If `https: false` (plain 5000) → TLS change likely irrelevant, low risk.
  - If `https: true` → reseal the secret with `tlsServerName`/`tlsCACert`
    **before** the bump, or every iSCSI attach/detach fails.
- **`fsType: btrfs` dropped** (image is now UBI 9, no `btrfs-progs`). Our
  StorageClasses don't set `fsType` (default ext4) — but confirm **no existing
  PV was formatted btrfs** (`kubectl get pv -o custom-columns=…,FS:.spec.csi.fsType`).
  Upstream: migrate btrfs PVs to ext4/xfs before upgrading.
- **Image now runs `USER 1000`**; the chart sets `runAsUser: 0` on the node
  plugin container, which we need for mount/format + host chroot — no action,
  but confirm the node DaemonSet comes up.
- StorageClass params in our values (`dsm`, `location: /volume2`, `protocol`,
  `reclaimPolicy: Retain`, `isDefault: false`) all still exist.

Blast radius: every iSCSI PVC (Teamspeak, Garage meta/data, VL, VM, NetBox
Valkey, Authentik Redis…). Rules from CLAUDE.md apply: DSM ~10-LUN cap, don't
reboot the NAS during this, CSI stays workers-only (CP taint), and drain stateful
pods from a node before touching its CSI node plugin. Rolling the node
DaemonSet one worker at a time; keep an eye on `dmesg` for iSCSI session errors
(`known-issues/storage-iscsi-synology.md`).

---

## 7. Authentik 2026.2.3 → 2026.8.3 — two-hop upgrade

**Confidence: Read** (release notes `website/docs/releases/2026/v2026.5.mdx`,
`v2026.8.mdx` in `goauthentik/authentik`, plus the chart diff).

- **Upgrades MUST go by major release and all components together:**
  2026.2.x → **2026.5.x** → 2026.8.x. Do **not** jump to 2026.8. I did not look
  up the latest 2026.5.x patch tag / chart version — find it
  (`git ls-remote --tags https://github.com/goauthentik/helm 'authentik-2026.5*'`)
  and use the latest patch for hop 1. Outposts (embedded/proxy) must match the
  server version.
- **2026.5:** listen address default changes `0.0.0.0` → `[::]` (IPv4-only
  environments may need to adapt; our pod network is v4 — verify probes and the
  Traefik/cloudflared backends still connect); `AUTHENTIK_POSTGRESQL__CONN_OPTIONS`
  deprecated (removed next version — grep our values/env for it; we use the
  Patroni VIP with `sslmode`).
- **2026.8:**
  - **Forwarded headers are only honoured from trusted proxies.** Must set
    `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS` to cover **every address that
    connects directly to authentik**: the Traefik pod network (Pod CIDR
    `10.42.0.0/16`) and anything else in front. Get this wrong and HTTPS is read
    as HTTP → mixed content, endless spinner, auth/SAML/OIDC errors.
    This bites **Zabbix SAML (`hugin.xiiisins.com`), Tailscale OIDC, Vault OIDC,
    NetBox/Outline/Immich/Semaphore OIDC, and every ForwardAuth app**
    (MicroBin, n8n, vmui, VL UI) — test all of them, including the embedded
    outpost (it has its own cached `authentik_host`, see `known-issues/authentik.md`).
  - Server + proxy outpost rewritten in Rust (claimed 1:1) — watch the outpost
    behind ForwardAuth for regressions.
  - WebAuthn "Prevent duplicate devices" option removed;
    `hash_password` command no longer takes a positional password.
- **Chart:** bundled Bitnami postgresql subchart 16→18 — irrelevant, we run
  `postgresql.enabled: false` (external Patroni VIP) and Redis is our own
  StatefulSet; verify with `helm template`. Chart no longer sets the
  `AUTHENTIK_LISTEN__*` env from `containerPorts` (see the 2026.5 listen-address
  note above).
- **Terraform:** `terraform/authentik` pins `goauthentik/authentik` provider
  **2026.2.0**. Bump the provider in step with each server hop and re-run
  `terraform plan` (expect no diff; the `authentik_*` blueprints/providers we
  manage — Tailscale, NetBox, Outline, Vault, Zabbix SAML, ForwardAuth —
  are the surface). Do the plan **before** and **after** each hop.
- Pre-hop checklist: fresh PG backup of the `authentik` DB via the Patroni
  leader, note current login flows/embedded-outpost token, keep the local
  `akadmin` break-glass credentials handy.

---

## 8. NetBox 8.2.17 → 8.3.89 — chart alone is easy, NetBox 4.7 is a migration

**Confidence: Read** (`docs/release-notes/version-4.7.md`).

Our values pin `image.tag: v4.6.1` (k8s/asgard/apps/netbox/helmrelease.yaml),
so a chart-only bump doesn't change the app. Chart 8.3 bumps subcharts
(postgresql 18.6→18.12, valkey 5.6→6.3, common 2.39→2.41) — postgres subchart is
unused (external PG), valkey is our standalone bundled one → **the valkey
subchart major bump (5.x → 6.x) needs checking** against
`valkey.architecture: standalone` / `auth.enabled` / persistence keys before
merging (I flagged those as absent from the new `values.yaml` — likely
null-default noise, but verify with `helm template`).

**NetBox 4.7 breaking changes that hit us:**

- **`ltree` extension.** Hierarchical models (Region, SiteGroup, Location,
  DeviceRole, Platform, ModuleBay, InventoryItem…) move from django-mptt to a
  PostgreSQL `ltree` column. The NetBox DB needs the `ltree` extension — we
  provision extensions through `postgres_databases[].extensions` in
  `postgres-common` (same mechanism added for Immich's pgvector). Add `ltree` and
  apply via Frigg/Ansible **before** upgrading; the migration will fail
  otherwise. The migration may also be slow (upgrade runs
  `rebuild_config_context_cache`, one UPDATE per device/VM — small here).
- **PG ≥ 15 and Redis/Valkey ≥ 6 required** — we're on PG 17 and Valkey; fine.
- **`social-auth-app-django` 6.0 / `social-auth-core` 5.1 (majors).** Our login
  is Authentik OIDC via python-social-auth (with the known
  `SOCIAL_AUTH_PIPELINE` gap in `known-issues/netbox.md`). NetBox says to test SSO
  on a non-production instance — **verify the OIDC round-trip immediately after**
  and keep the local superuser as break-glass.
- **API changes vs the Terraform provider.** `terraform/netbox` uses
  `e-breuninger/netbox v5.3.0` (already awkward with 4.6 — see known-issues).
  4.7 changes: select/multi-select custom field values are returned as
  `{value,label}` objects; API token plaintext can no longer be supplied by the
  client; running scripts via REST needs a write-enabled token; config-context
  serializer classes removed; bulk error response shape changed;
  `ipam.Service` `protocol`/`ports` replaced by `port_mappings`. Our token flow
  is the admin API token (1P `Asgard - NetBox - admin API token`). **Run
  `terraform plan` in `terraform/netbox` before and after** — expect no diff; any
  custom-field diff means the provider misreads the new shape. Check whether a
  newer provider release exists that supports 4.7 before upgrading.
- **NetBox dynamic inventory (Semaphore / Frigg `refresh-netbox-inventory`).**
  The Ansible `netbox.netbox.nb_inventory` plugin consumes the REST API; test the
  cache-refresh job after (the cold-cache silent-success class in CLAUDE.md
  gotchas applies — confirm the inventory has hosts, not just `success`).
- Webhook/email config changes (`EMAIL` server now mandatory to send; `MAILERS`
  replaces `EMAIL_*`) — we set `email_password` empty; only relevant if mail is
  wanted. `housekeeping` management command removed — check no CronJob calls it.
- **Target 4.7.2, not 4.7.0/4.7.1.** 4.7.2 (2026-09-29) fixes plaintext API
  tokens being recorded in background-job results, and 4.7.1 fixes a `pg_dump`
  restore issue (restore of a 4.7.0 dump loses cascade triggers). The 8.3.89 chart
  ships appVersion v4.7.1 — override `image.tag` to the 4.7.2 tag rather than
  taking the chart default.
- Pre-flight: PBS/PG backup of the `netbox` DB (PG leader), and note that this
  is a good candidate to follow the "TF→NetBox standing pattern" re-plan.

Suggested sequence: (a) chart 8.3.x with `image.tag: v4.6.1` kept; (b) add
`ltree`; (c) bump `image.tag` to v4.7.2, watch the migration in the main
container entrypoint (`install.remediation.retries: -1` is already set for this
class); (d) verify OIDC, TF plan, inventory.

---

## 9. external-secrets 0.20.4 → 2.11.0 — two majors, do stepwise

**Confidence: Diff only, plus the upstream support matrix. I found no
breaking-change / migration doc in the repo — I do NOT know the breaking changes
for 1.0 and 2.0.** Read the GitHub release notes for v1.0.0 and v2.0.0 first.

What I can say:

- We only use `apiVersion: external-secrets.io/v1` (39 manifests: 19
  `ExternalSecret`, 20 `ClusterSecretStore`); `v1beta1` CRD versions are
  already `served: false` in the v2.11.0 bundle. That removes the most common
  break.
- Chart diff for our values (`installCRDs: true`, `serviceAccount.name`) shows no
  key removals. The 2.x chart adds NetworkPolicy, webhook serviceaccount, global
  values schema, and stricter `values.schema.json` — **schema validation can
  reject values that used to be ignored**; render with our values.
- **Kubernetes support:** ESO's matrix lists 0.20.x → 1.34, 1.x → 1.34,
  2.0–2.5 → 1.34–1.35, 2.7–2.11 → 1.35/1.36. **We run K3s v1.33.1.** 2.x is
  outside the tested range (chart `kubeVersion` is only `>=1.19`, so it will
  install; support is the question). Consider the K3s bump (§11) first.
- ESO is on the critical path for every `ExternalSecret` (Vault → K8s). A broken
  ESO doesn't delete existing Secrets, but nothing refreshes; Vault-backed pods
  that restart still have their last-synced Secret. Failure is quiet — check
  `kubectl get externalsecret -A` for `SecretSynced` and the ESO logs after.

Suggested: 0.20.4 → 1.x latest → 2.x latest, each after reading notes, with the
CRD bundle diff reviewed (`installCRDs: true` means CRD changes ride the chart).

---

## 10. Flux v2.8.7 → v2.9.5

Not read. The `flux-system` `gotk-components.yaml` is bootstrap-managed —
bump via `flux install --export` / `flux bootstrap` per the repo's usual path,
not by hand-editing. Read the v2.9 release notes (fluxcd/flux2) — `HelmRelease`
`v2` API and `source-controller` behaviour changes matter because every chart
above rides it. Do this **separately and last**, so a Flux regression isn't
confused with a chart regression.

## 11. K3s v1.33.1 is itself behind

`roles/k3s/defaults/main.yml` pins `v1.33.1+k3s1`. Kubernetes 1.33 is old
(verify its EOL date). Current charts are tested against 1.34–1.36 (ESO 2.x,
Vault chart 0.33+: 1.32–1.36). The K3s bump is a separate, bigger, quorum-critical
operation (CPs one at a time, etcd quorum checks, workers drained,
Calico-as-addon interaction — CLAUDE.md "Calico CNI is NOT Flux-managed"). Not
covered here; worth its own plan and it unblocks §9.

---

## Suggested order for the in-homelab session

1. Verify Vault's running image is 1.21.2 (§2), merge the branch (§1 + §2),
   watch Flux, run the per-chart spot-checks in §1.
2. Traefik values rename + bump (§4).
3. Vault chart bump with image pinned (§3, part b).
4. Synology: inspect client-info TLS first (§6), then bump.
5. cert-manager 1.20 → 1.21, then MetalLB (§5) — each after reading notes.
6. Authentik two-hop with trusted-proxy CIDRs (§7); NetBox chart-then-app (§8).
7. ESO stepwise (§9), Vault 2.0 (§3c), K3s (§11), Flux (§10).

## Post-flight for whoever lands these

Per `CLAUDE.md`: add a `decisions.md` row for "Vault server image pinned
explicitly (chart default is version-coupled)" and for the CSI/values renames,
tick/adjust the `open-questions.md` items this touches (generate-root runbook,
Vault-root-token alerting), and add an incident file if anything surprising
happens during the bumps. Concrete pins only — no floats (existing policy).
