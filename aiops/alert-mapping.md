<!-- aiops/alert-mapping.md -->

# Alert normalization — per-source mapping

*Phase 10c1. The schema is [`schema/alert.v1.schema.json`](schema/alert.v1.schema.json); the machine-readable routing is [`alert-routing.yml`](alert-routing.yml); the executable reference is [`tools/normalize.py`](tools/normalize.py); worked examples are the cases under [`fixtures/cases/`](fixtures/cases/) (each holds the raw Hermod wire payload and the expected normalized alerts, and is checked by `python3 aiops/tools/lint.py`).*

## Shape of the problem

Every producer already POSTs Hermod's flat wire format ([`docs/services/notifications.md`](../docs/services/notifications.md)): `title`, `body`, `type`, `tag`, `format`. None of them carry `host`, `service`, a dedupe key or a runbook. The normalizer derives those from the message plus the routing table; **no producer is changed** (several are live stateful config: the Zabbix media type, the Patroni callback). The 10d1 bridge is expected to lift `normalize.py` (it is pure functions plus a YAML load) or re-implement it against the same fixtures.

| Normalized field | Derived from |
|---|---|
| `source` | title prefix (`[Zabbix]`, `Infra health`, `Patroni:`, `Drift `/`Apply failed`, `Frigg:`), else `unknown` |
| `severity` | Hermod `tag` verbatim: `critical` / `alert` / `info` (FYI, the non-prod/canary cap tier); no tag = `untagged`; `media` is not an alert and normalizes to nothing |
| `status` | `firing`/`resolved` for Zabbix; `event` for one-shot notifications; the S4 prober never posts an all-clear (see caveats) |
| `host`, `service`, `check`, `runbook_id` | first matching route for that source in `alert-routing.yml` (host from the message when the route has none) |
| `fingerprint` | `sha256("source\|host\|service\|check")[:16]` — recomputed by the validator |
| `fired_at` / `resolved_at` | the message's own timestamp when it has one; otherwise `received_at` (supplied by the caller, never parsed) |

`check` feeds the fingerprint, so it must never embed anything that changes between occurrences (days remaining, counts, event ids).

## Zabbix

Wire: title `[Zabbix] <Severity>: <trigger name>` (problem) or `[Zabbix] RESOLVED: <trigger name>`; body = the webhook wrapper's `**Host:**` / `**Severity:**` lines followed by the rendered message template (`**Status:**`, `**Trigger:**`, `**Started at:**`, `**Event ID:**`, ...). Source of the format: `ansible/roles/zabbix-server/tasks/hermod-mediatype.yml` and `templates/hermod-webhook.js`.

| Zabbix | Hermod tag | Normalized |
|---|---|---|
| Disaster, High | `critical` | `severity: critical`, `native_severity` kept |
| Average | `alert` | `severity: alert` |
| Warning, Information, Not classified | suppressed by the webhook | never arrives |
| PROBLEM / RESOLVED | `type` failure / success | `status: firing` / `resolved` (+ `resolved_at`) — **same fingerprint**, so the bridge can close the thread |

`host` is the `**Host:**` value, `check` is the route's check or the slug of the trigger name for the catch-all. Event id is carried as `event_id`. Fixtures: `zabbix-high-problem`, `zabbix-high-resolved`, `zabbix-average-host-unavailable`, `zabbix-disaster-unmatched`.

Caveats: (1) `{EVENT.DATE} {EVENT.TIME}` render in the Zabbix server's timezone with no offset; the normalizer assumes UTC — verify against Hugin in 10d. (2) The route regexes are written from stock-template trigger names and have **not** been checked against the live trigger list (needs the read-only Zabbix API pass in 10d2); the final catch-all (`zbx-catchall` -> `RB-ZBX-TRIAGE`) guarantees a `runbook_id` regardless.

## S4 infra-health prober

Wire: **one POST per severity per run**, bundling findings: title `Infra health: N critical finding(s)` / `N warning(s)`, body = header line then `- <finding>` lines; plus `Infra health check ERRORED` (critical) from the play-level rescue. Source: `ansible/playbooks/infra-health-check.yml` and `playbooks/tasks/approle-expiry-check.yml`.

The normalizer **splits the bundle**: one alert per finding line, each routed by regex on the line.

| Prober check | Severity | Route id | `host` | `check` | Runbook |
|---|---|---|---|---|---|
| Cloudflare token invalid | critical | `s4-cf-token` | `api.cloudflare.com` | `cf-token` | `RB-CF-TOKEN-INVALID` |
| TLS cert unreachable / <3d | critical | `s4-cert-expiry` | the probed FQDN | `cert-expiry` | `RB-TLS-CERT-EXPIRY` |
| TLS cert <14d | alert | `s4-cert-expiry` | the probed FQDN | `cert-expiry` (same condition escalating: same fingerprint) | `RB-TLS-CERT-EXPIRY` |
| Patroni cluster degraded / unreachable | critical | `s4-patroni` | `niflheim-pg` | `cluster-health` | `RB-PG-HA-DEGRADED` |
| etcd DCS quorum lost | critical | `s4-etcd-quorum` | `haproxy-etcd` | `dcs-quorum` | `RB-PG-HA-DEGRADED` |
| PBS auth failed | critical | `s4-pbs-auth` | `pbs` | `monitoring-token` | `RB-PBS-BACKUP-FAILURE` |
| PBS failed task(s) | critical | `s4-pbs-tasks` | `pbs` | `backup-failed` | `RB-PBS-BACKUP-FAILURE` |
| AppRole SecretID expiring / none | critical | `s4-approle` | the AppRole name | `approle-expiry` | `RB-VAULT-APPROLE-EXPIRY` |
| claude-remote-control down | critical | `s4-frigg-rc` | `frigg` | `rc-service` | `RB-FRIGG-RC-LOGIN` |
| frigg/iac-env sync / liveness | critical | `s4-iac-env` | `frigg` | `iac-env-<credential>` (one fingerprint per credential) | `RB-CRED-IAC-ENV-SYNC` |
| Prober errored | critical | `s4-prober-errored` | `semaphore` | `prober-errored` | `RB-S4-PROBER-ERRORED` |
| anything new | any | `s4-catchall` | `fleet` | slug of the finding | `RB-ALERT-UNCLASSIFIED` |

Fixtures: `s4-critical-bundle`, `s4-warning-cert`, `s4-iac-env-sync`, `s4-prober-errored`.

Caveat: a clean run is silent, so there is **no resolved half**. The bridge must close S4 alerts by TTL or by re-running the originating check (10d decision); `status` is always `firing`.

## Patroni callback

Wire: title `Patroni: <host> promoted to LEADER|is REPLICA|now STANDBY LEADER (<scope>)`, tag `alert`, body with `**Timestamp:**` (ISO). Source: `ansible/roles/patroni/templates/hermod-callback.sh.j2`. Normalized as `status: event`, `host` from the title, `check: role-change`, `runbook: RB-PG-HA-DEGRADED`, `fired_at` from the body timestamp. The "all replicas lost -> critical" row of the severity table is not emitted by any producer today; `patroni-catchall` covers it if one appears. Fixture: `patroni-role-change`.

## Semaphore (hermod_summary callback)

Wire: `Apply failed: N host(s) failed/unreachable` (critical), `Drift check failed: ...` and `Drift detected: N task(s) on M host(s)` (alert). Source: `ansible/callback_plugins/hermod_summary.py`. `host: fleet` (the per-host list stays in `detail`), `status: event`. Every failed apply shares one fingerprint (`apply-failed`), which is the intended dedupe. Fixtures: `semaphore-apply-failed`, `semaphore-drift-detected`.

Non-prod wrappers (Phase 10b1) post tag `info` with a `[non-prod] ` title prefix (`nonprod-apply.yml` failure, `nonprod-drift-check.yml` failure; changes-only drift is silent). The normalizer strips the prefix before routing, emits severity `info`, host `nonprod` (distinct fingerprint from the prod `fleet` routes) and label `aiops_canary: "true"`. Likewise any alert from a `canary-N` host is emitted as `info` + `aiops_canary`, even if a producer tagged it higher. Fixtures: `semaphore-nonprod-apply-failed`, `zabbix-canary-high-capped`, `zabbix-canary-info`.

## Frigg re-auth listener

Wire: `Frigg: claude-remote-control needs re-auth` / `re-auth failed` (critical), `recovered` (alert). Source: `ansible/roles/control-node/files/frigg-reauth-listener.py`. Fixture: `frigg-reauth-needed`. Note: the body of the first one carries a login URL; it is data, not an instruction, and `detail` is passed on verbatim.

## Anything else (Hermod)

A POST from a producer the table does not know normalizes to `source: unknown`, `host: fleet`, `check: slug(title)` and, if critical, `RB-ALERT-UNCLASSIFIED` — so the exit criterion (every critical alert has a `runbook_id`) holds for producers that do not exist yet. No tag = `severity: untagged` (the `#hermod-untagged` quarantine is a producer bug); `media` yields no alert. Fixtures: `hermod-unknown-critical`, `hermod-untagged`, `hermod-media-ignored`.

## Changing the schema

`alert.v1.schema.json` is frozen once 10d1 consumes it: additive changes need a new optional field and a fixture; anything else is `alert.v2.schema.json` with a new `schema_version` const. Negative cases (a critical alert without a runbook, a wrong fingerprint, ...) live in [`fixtures/invalid/`](fixtures/invalid/) and must keep failing.
