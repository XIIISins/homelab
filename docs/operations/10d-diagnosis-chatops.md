<!-- docs/operations/10d-diagnosis-chatops.md -->

# Phase 10d — Diagnosis-only chat-ops: implementation plan

*Drafted 2026-10-02. Status: 🟡 planned, nothing built. Parent: [`aiops-roadmap.md`](aiops-roadmap.md) §10d (Stage 1). Consumes the 10c data in [`aiops/`](../../aiops/README.md). Everything marked **Proposed** is a default I picked so work can start; none of it is in [`decisions.md`](decisions.md) until the operator confirms.*

---

## Goal and boundary

When a `critical`/`alert` fires, a Discord thread appears within ~2 minutes carrying a **diagnosis**: which layer failed (host vs workload vs hypervisor vs network vs drift vs external), the evidence, the matching known-issue/runbook, and the registry actions that *would* help. The agent **proposes and explains; it never acts** — it holds no write credential of any kind (roadmap principle 6, tier T0).

Out of scope here (do not creep): executing actions, approval reactions, a Discord bot (10e); kill switch, rate-limited autonomy (10f); SSH to any host (10d has none, see toolbelt).

---

## Pre-flight findings (what I checked)

| Check | Finding | Consequence |
|---|---|---|
| Design/decisions | Roadmap §10d is three bullets + the replay acceptance. 10c decisions row: bridge calls `normalize()` on each Hermod POST, looks up `runbook_id`, hands the session alert + `preconditions` + the `source` doc + the *proposable* registry actions ([`aiops/README.md`](../../aiops/README.md) "What 10d needs") | Plan builds on those, no new data model for alerts |
| Open 10c follow-ups ([`open-questions.md`](open-questions.md)) | **(3)** producer-side `runbook_id`, **(4)** Zabbix route regexes unverified against live triggers, **(10)** `vault-status` sees one node only, **(6)** 5 runbooks are `stub`, **(5)** S4 findings never resolve | (4) and (10) are closed *by* 10d2 (the toolbelt gives the read access). (3) is decided below. (5)/(6) degrade diagnosis quality but do not block; (6) is scheduled before 10e leans on it |
| Frigg ([`known-issues/frigg-control-node.md`](../known-issues/frigg-control-node.md)) | The operator user `ghost` has **NOPASSWD sudo** and the fleet ssh-agent socket; `claude remote-control` runs there under the operator's login | The diagnosis session **must not run as `ghost`**. This is the main security design point below |
| Hermod ([`services/notifications.md`](../services/notifications.md)) | Single ingress already used by every producer; routing is by `tag`; Apprise can fan one notification out to several URLs; Caddy allowlists source IPs | Reuse Hermod as the one ingress; add a bridge URL per tag |
| Canaries (10b1) | Canary alerts are capped at the `info` tier | The bridge must also accept `info` **for canary hosts only**, otherwise there is no live test path |
| Semaphore/Zabbix/NetBox/Proxmox | Each needs a dedicated read-only identity; none exists today | 10d2 mints them |

No Phase 0 pending-task closure is required. The one *interaction* worth naming: 10d is the first workload that makes the stub runbooks (6) and the unresolved S4 findings (5) visible to a reader — expect "no matching runbook" on those and treat it as a finding, not a bug.

---

## Architecture

```
Producers (Zabbix, S4 prober, Patroni, Semaphore, Frigg)
        │  unchanged wire format
        ▼
Hermod (Apprise, LXC 1103) ── existing Discord webhooks (unchanged)
        │  + one json:// URL per tag, path = /ingest/<tag>
        ▼
aiops-bridge  (systemd on Frigg, user `aiops-bridge`, port 8687, internal-only)
   authn → normalize() → fingerprint dedupe (SQLite) → correlation window → queue
        │
        ▼  spawns, one session per alert *group*
claude -p  (user `aiops-diag`: no sudo, no ssh-agent, no Vault token, no kubeconfig of its own,
            sandboxed unit, allow-listed Bash only = the aiops-* read wrappers)
        │  stdout = JSON diagnosis  (the session has NO outbound channel but stdout)
        ▼
aiops-bridge validates against diagnosis.v1 schema → renders → posts to Discord forum thread
```

**Proposed — why these shapes:**

- **Bridge on Frigg as a systemd service, not a K8s app.** Same reasoning as `frigg-reauth-listener`: the thing that diagnoses "asgard is down" must not live in asgard. Cost: Frigg is a single point (T3, already accepted in the roadmap; Gatus on `do1` is the outside watcher).
- **Hermod stays the only ingress** (one place alerts enter, the 10c normalizer already parses its wire format). Gap accepted: if Hermod itself dies, nothing reaches the bridge — Hermod's death is already caught by Gatus/Zabbix and posted via their own paths. A second feed (Zabbix media type straight to the bridge) is a 10d-later add if that gap bites.
- **The agent does not hold the Discord webhook.** The bridge posts. A session that has been prompt-injected by a log line can emit text, which the bridge validates and renders as data, but cannot reach Discord, Hermod, or anything else.
- **Per-tag bridge URLs.** Apprise's `json://` payload carries title/message/type but **not** the routing tag, so the tag is encoded in the URL path (`/ingest/critical|alert|info`). To verify on the first live POST — the exact payload shape decides the adapter in `normalize.py` (a thin `from_apprise_json()`; the existing fixtures keep working).

### The security design point: who the session runs as

`ghost` has unrestricted sudo and the fleet key. The diagnosis session runs as a **new unprivileged user `aiops-diag`** under a hardened transient unit (`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, no new privileges, writable only its per-session scratch dir, egress allowed only to the Anthropic API + the specific internal read endpoints). Credentials reach it **per session, via env, minted by a root-owned loader** (same pattern as `frigg-ssh-agent-load`): the loader AppRole-logs-in, reads the read-only tokens from Vault, mints a 1 h kube token, and exec's the session with them. Nothing static on disk for `aiops-diag`. If the unit is compromised the blast radius is: read access to the same data the toolbelt table lists, for ≤ the session lifetime.

Untrusted input: alert text, log lines and even commit messages are attacker-influenceable. The prompt fences them as data; the real control is that there is **nothing writable to reach**.

---

## 10d0 — Decisions and prerequisites (before code)

Defaults are picked; flag any you want flipped.

- [ ] **D-a. Session auth.** Proposed: a dedicated **Anthropic API key** in Vault (`secret/ansible/aiops/anthropic-api-key`) for headless sessions, **not** the operator's subscription login that `claude remote-control` uses. Why: sessions then cannot interfere with chat-ops/RC rate limits, the `frigg-reauth-listener` failure mode doesn't take diagnosis down with it, and spend is separately visible. Cost is real (est. 5–15 k tokens in + a few k out per session; at tens of alerts/day this is dollars/month, bounded by the daily cap below).
- [ ] **D-b. Discord shape.** Proposed: a **forum channel `#diagnoses`** + a webhook (`secret/ansible/aiops/discord-diagnosis`); the bridge creates one post per incident group with `thread_name` and appends via `?thread_id=`. Webhooks cannot make threads in ordinary text channels; a bot could, but 10e needs a bot anyway (reactions) — adding it now widens 10d for no 10d benefit. *Operator step: create the channel + webhook.*
- [ ] **D-c. Producer-side `runbook_id` (10c follow-up 3).** Proposed: **no producer changes in 10d.** The routing table already guarantees resolution; revisit only if replays show misrouting.
- [ ] **D-d. Model.** Proposed: Sonnet-class default (diagnosis is read-heavy tool use, latency matters), Opus-class for groups of ≥ 3 alerts or `critical` + unknown layer. Set in bridge config, not hard-coded.
- [ ] **D-e. Which alerts get a session.** Proposed: `critical` and `alert` always; `info` only when `host` is a canary (the live test path). Everything else: normalized + logged, no session.
- [ ] Verify Frigg headroom (VM 2900 CPU/RAM; concurrency 1 to start, 2 max) — read-only check.

---

## 10d1 — Webhook bridge (no LLM yet)

Build the plumbing end to end with a **stub session that just echoes the alert into the thread**, so the LLM is never the thing being debugged.

- [ ] Role `ansible/roles/aiops-bridge/`: Python stdlib + the existing `aiops/tools/normalize.py` (import, don't copy), systemd unit, config templated; mirrors `control-node/files/frigg-reauth-listener.py` conventions. Playbook wired into `asgard-control.yml`.
- [ ] **AuthN, three layers:** Hermod's IP in the listener allowlist (Caddy-style `remote_ip`), a long random token in the URL path (TF-minted `random_password` → Vault `secret/ansible/aiops/bridge-token`, consumed by both ends), TLS not required on the LAN (matches Hermod posture); AGH rewrite `aiops-bridge.niflheim.xiiisins.com` via `terraform/adguard/rewrites.tf` (existing pattern; `frigg-auth` precedent).
- [ ] **Idempotency per fingerprint:** SQLite in `/var/lib/aiops-bridge/`; states `received → grouped → running → posted → resolved`. Same fingerprint within a cooldown (30 min default) updates the existing thread's "last seen"/count instead of starting a session; a **severity escalation** or a *resolved* half re-opens/updates.
- [ ] **Correlation window (important):** alerts arriving within ~90 s of each other are **grouped into one incident** and handed to one session. The 2026-09-30 Skuld freeze produced a burst across many guests; one-session-per-alert would produce twenty contradictory "bad release" diagnoses instead of one "dead host". Group key proposal: shared hypervisor (NetBox → Proxmox node) or shared cluster, else time only.
- [ ] **Circuit breakers:** per-session wall-clock cap (10 min), max daily sessions (default 40), max queue depth; breach → one Discord post "diagnosis budget exhausted", never silent.
- [ ] **Audit:** structured JSON events to journald → vlagent → VictoriaLogs, keyed by fingerprint (roadmap principle 7).
- [ ] Hermod: add the three `json://…/ingest/<tag>` URLs to the Apprise config template (Vault-sourced token), tagged `critical`/`alert`/`info`; existing Discord URLs untouched.
- [ ] **Done when:** a test POST to Hermod `tag: alert` produces the existing Discord alert **and** a `#diagnoses` post with the normalized alert; replaying the same POST within the cooldown does not create a second post; a burst of 5 alerts yields 1 thread. Reboot-test Frigg (CLAUDE.md persistence rule).

## 10d2 — Read-only toolbelt (+ replay mode)

Every tool is a small wrapper `aiops-<name>` installed root-owned in `/usr/local/lib/aiops/bin/`; the session's allow-list is `Bash(aiops-*:*)` plus `Read` on a read-only repo checkout. No bare `kubectl`, `curl`, `ssh`, `terraform`, `ansible`.

| Wrapper | Credential (all new, all write-less) | Scope / how it is enforced |
|---|---|---|
| `aiops-kube` | ServiceAccount `aiops-readonly` (Flux-managed manifests under `k8s/asgard/…`), **1 h token minted per session** by the root loader | ClusterRole `get/list/watch` on workload/node/event/CRD objects incl. HelmRelease/Kustomization status, `pods/log`; **no `secrets`, no `pods/exec`, no `*/proxy`, no verbs but read**. Proof test: `kubectl auth can-i --list` + attempted `delete`/`get secret` must be denied |
| `aiops-vlq` / `aiops-vmq` | none (VL/VM have no auth); reached over the internal niflheim Gateway | Wrapper only forms `/select/logsql/*` and `/api/v1/query*` calls; the credential-less endpoints are read-only by API surface — **verify against [`known-issues/observability.md`](../known-issues/observability.md) that no delete/admin path is exposed on those routes** |
| `aiops-zabbix` | Zabbix user `aiops-ro` with a **read-only user role** + API token (`secret/ansible/aiops/zabbix-token`) | Wrapper allow-lists `*.get` methods. First use = verify the 10c route regexes against the live trigger list (closes follow-up 4). Zabbix is Ansible-managed (`community.zabbix`), so the user/role/token are a role addition, not click-ops |
| `aiops-netbox` | NetBox local user with **view-only permission**, token with `write_enabled=false` | `GET` only. (The TF-provider token quirks in [`known-issues/netbox.md`](../known-issues/netbox.md) are about the *terraform* user; this is a separate token) |
| `aiops-pve` | Proxmox `aiops@pve` + API token, role **PVEAuditor** on `/` | `GET /nodes`, `/cluster/resources`, guest status. This is what answers "is the hypervisor alive / which guests are on it" — **the core of host-vs-workload** |
| `aiops-semaphore` | Semaphore user in a **guest/read-only** project role + API token | Task history/outputs only; no run endpoint. Verify the role has no `task:run` |
| `aiops-reach` | none | ICMP/TCP-connect probe to host/port from Frigg, bounded count and rate. Substitutes for SSH |
| `aiops-git` | none (repo is public) | `git log/show/diff` on a read-only clone refreshed by a timer; recent commits and CLAUDE.md/`docs/known-issues` are readable |
| `aiops-vault-status` | none (`sys/health`, `sys/seal-status` are unauthenticated) | Per-pod seal state via `aiops-kube` — closes follow-up 10 |
| `aiops-registry` | none | Reads `aiops/runbooks.yml`/`actions.yml`; lists which actions the session may *propose* (never invoke) |

- [ ] Mint each identity in its owning IaC (TF for Proxmox/Vault policy, Flux for the SA, Ansible for Zabbix/Semaphore/NetBox), secrets to Vault `secret/ansible/aiops/*`; operator mirrors to 1P (standing rule).
- [ ] **Negative tests per credential** (the actual deliverable of "write-less"): a script that tries one write with each and asserts denial. Run it in CI-adjacent form (manual from Frigg) and again after any credential change.
- [ ] Root loader `aiops-session-env` (Vault AppRole, narrow policy `aiops-diag-read`: read of exactly those KV paths, nothing else).
- [ ] **Replay mode:** every wrapper honours `AIOPS_REPLAY_DIR=<aiops/replays/<scenario>/>` and returns recorded responses keyed by (tool, normalized args) instead of touching the network; an unrecorded call returns `NO_RECORDING` (the harness counts these). This is what makes the acceptance incidents repeatable — you cannot re-freeze Skuld.
- [ ] **Done when:** each wrapper works live read-only and in replay mode; negative tests pass; `sudo -u aiops-diag env` shows no static secret; allow-list proven (a session asked to run `kubectl delete` is refused by the harness, not by the model's good behaviour).

## 10d3 — Session, diagnosis template, Discord UX, acceptance

- [ ] **System prompt + task template** in `aiops/diagnosis/` (versioned, linted in CI like the rest of `aiops/`): the alert group, the runbook entry, doc excerpt, proposable actions, and the **host-vs-workload rubric**, explicitly: *establish layer before cause.* Evidence order — (1) Proxmox: node and guest status; (2) other guests/nodes on the **same hypervisor** going silent together (Zabbix agent availability, VL last-log timestamp, `aiops-reach`); (3) K8s node `Ready`/lease heartbeat; (4) only then workload: pod/HelmRelease state, logs, recent commits. "Several `Terminating` pods + Helm timeouts + a silent node" is a **dead host**, not a bad release. Rollback/redeploy is never the first suggestion for a layer-host finding (the Immich lesson).
- [ ] **Output contract** `aiops/schema/diagnosis.v1.schema.json`: `layer` (`host|hypervisor|workload|network|drift|external|unknown`), `confidence`, `summary`, `evidence[]` (`tool`, `args`, `finding`), `known_issue_refs[]` (file#anchor / `runbook_id`), `tier`, `proposed_actions[]` (**registry names + args only**; schema-validated against `actions.yml`; informational), `not_checked[]`, `human_next_step`. The bridge validates; invalid output → a short "diagnosis failed validation" post with the trimmed raw text, never a retry loop. Fixtures + linter coverage like the other schemas.
- [ ] **Discord rendering:** thread title `[tier/layer] host — check`; first post = summary + layer + confidence; evidence in a collapsed follow-up; links to the known-issue/runbook; footer = fingerprint + session id + model. Updates on re-fire/resolve.
- [ ] **Session limits:** `--max-turns`, wall clock, token budget per session; stream-json captured so the bridge logs every tool call to VL and **asserts only allow-listed tools were used** (a violation raises a `critical`, stops the session).
- [ ] **Replay harness** `aiops/replay/` + `aiops/replays/<scenario>/` (frozen alert group + recorded tool responses + expected-outcome file). Scored by assertions, not by reading prose: `layer == X`, `known_issue_refs ∩ must_cite ≠ ∅`, `proposed_actions ∩ forbidden = ∅`, zero out-of-allow-list calls, `NO_RECORDING` count reported. **3 runs per scenario, all three must pass** (LLM variance; a flaky pass is a fail). Runs manually from Frigg, not in the CI gate (needs the API key and costs money); CI lints the fixtures only. Recorded data is reconstructed from each incident's retro and any VL history still retained — say so honestly in each scenario's README (synthetic-but-faithful where logs have aged out).
- [ ] **Acceptance scenarios** (from the roadmap) — each must pass, plus **negative controls** so the agent can't pass by always saying the same thing:

  | Scenario | Must conclude | Must cite | Control |
  |---|---|---|---|
  | 2026-09-30 Skuld freeze | `host`/`hypervisor` dead, **not** a bad release | the Skuld-freeze incident + lxc-proxmox gotchas | paired with a *genuine* bad-chart-bump scenario → must say `workload` |
  | 2026-10-01 Calico datastore prune | K3s per-addon pruning is the cause | `RB`/incident for calico-datastore-prune, k3s-lifecycle | benign Calico pod restart → must **not** claim prune |
  | etcd raft-drop syslog flood | disk-fill risk on the surviving CPs | the etcd syslog-flood runbook | — |
  | 2026-05-17 Authentik/Redis CP-taint | scheduling/taint miss | k8s-scheduling gotcha + that incident | — |

- [ ] **Live pass:** inject a fault on a canary (stop a unit) → `info`-tier alert → bridge → real session with the live toolbelt → thread posted; and one real `critical`-path synthetic (the Hermod smoketest tag) to prove the non-canary path. No action is possible by construction.
- [ ] Ops doc `docs/procedures/aiops-diagnosis.md` (run, rotate credentials, replay, read a thread, disable); post-flight docs per CLAUDE.md.

---

## Exit criteria (Phase 10d)

1. The four acceptance scenarios + their controls pass 3/3, with zero out-of-allow-list tool calls.
2. A canary fault and a synthetic critical produce a correct thread end to end, live, with the real toolbelt.
3. Negative write tests pass for every credential; `aiops-diag` has no static secret and no sudo.
4. Alert storms group to one thread; duplicate fingerprints do not re-run; budget exhaustion is announced, not silent.
5. Frigg reboot-tested with the bridge enabled.

**Rollback = stop `aiops-bridge` and drop the three Hermod URLs.** Nothing else changed behaviour; no write path existed.

## Risks

| Risk | Mitigation |
|---|---|
| Prompt injection via logs/alert text | No writable credential, no outbound channel but stdout; bridge validates output against a schema; fenced as data |
| Confident wrong layer (the Skuld failure mode, but by the agent) | Rubric orders evidence; negative-control replays; `confidence` + `not_checked` are mandatory fields; humans still decide (nothing executes) |
| Frigg dies / is busy | Already T3 and Gatus-watched; bridge is stateless-ish (SQLite loss = lost dedupe, not lost alerts, since Hermod still posts to Discord as today) |
| Token/cost creep | Daily session cap, per-session budget, correlation window collapses storms, separate API key for visibility |
| Diagnosis erodes trust if it is wordy or wrong | Fixed short template; replays gate go-live; the first two weeks live are "shadow" (see below) |
| Stub runbooks / unresolved S4 findings produce weak output | Expected, surfaced as findings; schedule runbook writing before 10e |

## Rollout

Shadow first: for the first ~2 weeks the forum channel is read by the operator only, and each thread gets a 👍/👎 by hand; the 10e entry criterion becomes "N diagnoses graded, layer-correct rate ≥ X%" (pick X with the operator after seeing real data — I'd start the conversation at 90 %).

## What I need from the operator

1. Confirm or flip **D-a … D-e** (API key vs subscription is the one with real cost/trade-off).
2. Create the `#diagnoses` forum channel + webhook (D-b), then I seed `secret/ansible/aiops/discord-diagnosis` the usual way (you mirror to 1P).
3. Nothing else blocks 10d1; it can start the moment D-a/D-b are answered, and the stub-session bridge needs neither the API key nor the toolbelt.

## Next

After 10d: **10e** (executor + bot + approval). Pull forward before it: the five `stub` runbooks (10c follow-up 6), the Flux-actions ServiceAccount (follow-up 9, partly delivered by `aiops-readonly` here), and the PBS capacity / restore-drill items for 10g.
