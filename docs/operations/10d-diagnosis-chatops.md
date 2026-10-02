<!-- docs/operations/10d-diagnosis-chatops.md -->

# Phase 10d — Diagnosis-only chat-ops: implementation plan

*Drafted 2026-10-02, **revised 2026-10-02** (n8n as the agent; dual-path alerting). Status: 🟡 planned, nothing built. Parent: [`aiops-roadmap.md`](aiops-roadmap.md) §10d (Stage 1). Consumes the 10c data in [`aiops/`](../../aiops/README.md). Everything marked **Proposed** is a default picked so work can start; none of it is in [`decisions.md`](decisions.md) until the operator confirms.*

---

## Goal and boundary

Every alert takes **two independent paths**:

1. **Notify** — to the existing Discord channels via Hermod, exactly as today.
2. **Analyse** — **directly from the monitoring system to n8n** (not through Hermod), which acts as the agent between notification and resolution: it investigates and posts a **diagnosis** into a Discord thread within ~2 minutes (which layer failed — host / hypervisor / workload / network / drift / external — the evidence, the matching known-issue/runbook, and the registry actions that *would* help).

In 10d the agent **proposes and explains; it never acts.** It has no write credential of any kind (roadmap principle 6, tier T0). The same n8n is the natural home for 10e approvals and the 10f loop, which is why it is built as a standing agent, not a one-off bridge.

The two paths are independent by construction: **an n8n outage, a slow model, or a bad workflow must never delay or drop a notification.**

Out of scope here: executing actions, approval reactions, a Discord bot (10e); kill switch and rate-limited autonomy (10f); SSH to any host (10d has none).

> **Chat platform:** "Slack" in the brief means the homelab's Discord (confirmed by the operator); "direct via webhook" = Hermod's existing Discord webhooks.

---

## Pre-flight findings (what I checked)

| Check | Finding | Consequence |
|---|---|---|
| Design/decisions | Roadmap §10d is three bullets + the replay acceptance. 10c: the consumer calls `normalize()` on each Hermod POST, looks up `runbook_id` in `runbooks.yml`, hands the session alert + `preconditions` + the `source` doc + the *proposable* registry actions ([`aiops/README.md`](../../aiops/README.md)) | Builds on those; no new alert model |
| Open 10c follow-ups ([`open-questions.md`](open-questions.md)) | **(3)** producer-side `runbook_id`, **(4)** Zabbix route regexes unverified, **(10)** `vault-status` sees one node only, **(6)** 5 runbooks are `stub`, **(5)** S4 findings never resolve | (4) and (10) are closed *by* 10d2. (3) is decided below. (5)/(6) degrade diagnosis quality but do not block |
| Hermod ([`services/notifications.md`](../services/notifications.md)) | Single *notification* ingress; Apprise flattens every alert to `title`/`body`/`type`/`tag` | Fine for humans, lossy for analysis: no event id, trigger/item values, host groups or tags. And a Hermod outage would also blind the agent. So the analysis path **bypasses Hermod**: each monitoring system sends its own richer message to n8n (see Architecture) |
| n8n ([`services/n8n.md`](../services/n8n.md)) | The existing instance is in **asgard K3s** and exposes **public** `/webhook*` paths at `n8n.xiiisins.com`; ForwardAuth gates the editor | It is the wrong home for the agent: same failure domain as what it diagnoses, and a public webhook surface beside credentials. See "n8n placement" |
| Frigg ([`known-issues/frigg-control-node.md`](../known-issues/frigg-control-node.md)) | Operator user `ghost` has NOPASSWD sudo + the fleet ssh-agent socket | Nothing the agent touches may run as `ghost` |
| Canaries (10b1) | Canary alerts are capped at `info` | `info` must reach n8n **for canary hosts only**, or there is no live test path |
| Semaphore / Zabbix / NetBox / Proxmox | No read-only identity exists for any | 10d2 mints them |

No Phase 0 closure is required. The one interaction worth naming: 10d is the first reader of the five `stub` runbooks (6) and the unresolved S4 findings (5) — expect "no matching runbook" there and treat it as a finding, not a bug.

---

## Architecture

```
Monitoring systems (each sends TWO messages, independently)
  Zabbix ──── media type "Hermod"  (existing) ──► Hermod ──► Discord channels   [notify]
         └─── media type "n8n"     (new, rich) ─┐
  S4 prober / Patroni / Semaphore / Frigg       │  (same idea per source: a second,
         └─── native n8n POST (per-source cutover)  context-rich message)         [analyse]
                                                 ▼
n8n-aiops  (new dedicated LXC, on-site, outside K3s, internal-only)
   Webhook /ingest/<source> (responds 200 immediately)
     → Toolbelt API /ingest  (source adapter → normalize → fingerprint dedupe → correlation group)
     → leader only: Wait ~90 s → /group → AI Agent node (Anthropic model)
          tools = HTTP calls to the Toolbelt API only
     → structured output (diagnosis.v1) → validate → Discord forum thread
        │ every tool call
        ▼
Toolbelt API (systemd on Frigg, user `aiops-toolbelt`, internal-only)
   holds ALL read-only credentials · allow-list · replay mode · audit log (→ vlagent → VL)
```

**Proposed — why these shapes**

- **Split at the source, not at Hermod (operator call).** Each monitoring system sends two messages: the existing one to Hermod, and a second, **context-rich** one to n8n, so the analysis payload is designed for the agent instead of inherited from the human-notification format. Zabbix gets a second *media type* with its own message template (event id, trigger id/expression, severity, host + groups, trigger **tags**, current item values, recovery status); a failure in one media type is isolated per action operation and cannot block the Hermod one. Benefits: no lossy Apprise flattening, no tag-in-URL workaround, and **the agent no longer shares fate with Hermod**. Cost: this **reverses the 10c choice to leave producers untouched** (for the analysis path only) — each producer needs a small change, so the cutover is **per source, in order of value**:
  1. **Zabbix** (largest source, richest context; the Zabbix media type/action are Ansible-managed, `community.zabbix`). Trigger tags can carry `runbook_id`, which resolves 10c follow-up 3 for Zabbix at the source, and the **recovery event** flows through, so the agent can close/update threads.
  2. **S4 prober** (`infra-health-check.yml`): a task POSTs the structured finding to n8n beside its Hermod POST; this also gives S4 findings a real *resolved* half (follow-up 5) when a re-run is clean.
  3. **Semaphore** run notifications, **Patroni** `on_role_change` callback, **Frigg** listener: each adds a native POST; Patroni/Frigg are live stateful config, so each is a separate small change with its own validation.

  Until a source is migrated it simply is not analysed (its Discord alert is unaffected). The 10c routing table stays as the **fallback classifier** for any source that can't stamp a `runbook_id`; each source gets an adapter in `aiops/tools/` mapping its native payload to the existing alert schema (fixtures per source).
- **The n8n message must never hurt the Discord path.** The two sends are separate (separate Zabbix media types/operations; a separate, short-timeout, no-retry POST in each other producer, failures logged not raised). The n8n webhook **responds 200 immediately** (Respond-to-Webhook first, work after). **Verify per source** that a deliberately dead n8n endpoint leaves the Hermod post and the producer's own result unchanged before enabling it on `critical`.
- **n8n = brain/orchestrator, not credential holder.** n8n holds exactly: one token to the Toolbelt API, the Discord webhook, and the Anthropic key (n8n credential store, encrypted with a Vault-held `N8N_ENCRYPTION_KEY`). It cannot reach Proxmox/Zabbix/kube/NetBox itself — every investigative step is a Toolbelt API call. Compromise of a workflow is bounded by what that API exposes (read-only, allow-listed, audited), and that is the one place the "write-less" guarantee is enforced and tested.
- **Normalization, dedupe and grouping live in the Toolbelt API, not in n8n nodes.** The tested 10c Python normalizer is imported, not re-implemented in JS; dedupe/correlation need durable state (SQLite) that n8n handles poorly. n8n just asks "is this a new incident, and what's in its group?".

### n8n placement

**Proposed:** a **new, dedicated n8n on a small LXC** on Urd (services block 1120–1129; Norse name TBD; NetBox declaration in `terraform/netbox/vms.tf` per the standing rule; `asgard-lxcs` TF module; Ansible role + playbook; PBS-backed; SQLite). Internal-only: Caddy allowlist to operator subnets + n8n owner login; **no public routes, no cloudflared, no webhook exposure beyond Hermod's and Frigg's IPs**. Egress allow-listed (PVE/UCG firewall) to the Anthropic API, Discord, and the Toolbelt API only.

Why not the existing asgard instance: it is inside the failure domain being diagnosed (asgard down ⇒ diagnosis down — the case it matters most) and its public webhook paths would sit beside the agent's credentials. Why not offsite (`do1`): n8n can execute arbitrary code (Code/Execute-Command nodes), `do1` is the internet-facing box that also hosts the watcher, it is `s-1vcpu-1gb`, and everything the agent investigates is on the home network anyway. Cost of the LXC: ~1 GB RAM on Urd (≈7.7 GB headroom; canaries already there).

### The trust boundary: the Toolbelt API on Frigg

Frigg's `ghost` has unrestricted sudo, so the API runs as a **new unprivileged user** under a hardened unit (`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, no-new-privileges, egress only to the specific read endpoints). Static credentials: none on disk — a root-owned loader (the `frigg-ssh-agent-load` pattern) AppRole-logs-in to Vault, reads the read-only tokens, and mints a short-lived kube token, handing them to the service. n8n authenticates to the API with one token; the API additionally allow-lists n8n's IP.

Untrusted input: alert text, log lines and commit messages are attacker-influenceable. The agent prompt fences them as data; the real control is that **there is nothing writable to reach** and n8n's only egress for results is the validated Discord post.

n8n's agent loop gives less hard control than a harness-enforced allow-list would, so the enforcement is **API-side**: an unknown or write-shaped call is rejected by the API, not by the model's good behaviour; per-execution turn/iteration caps are set on the agent node and a **daily execution cap** is enforced in `/ingest`.

---

## 10d0 — Decisions and prerequisites (before code)

Defaults are picked; flag any to flip.

- [ ] **D-a. Anthropic key.** Dedicated API key for the n8n agent in Vault (`secret/ansible/aiops/anthropic-api-key`) — not the operator's subscription login (separate rate limits, no coupling to `frigg-reauth-listener`, spend separately visible). Est. 5–15 k tokens in + a few k out per execution; bounded by the daily cap.
- [ ] **D-b. Discord shape.** A **forum channel `#diagnoses`** + webhook (`secret/ansible/aiops/discord-diagnosis`): one post per incident group (`thread_name`), updates via `?thread_id=`. Webhooks can't create threads in ordinary text channels; a bot could, but 10e needs a bot anyway. *Operator step: create the channel + webhook.*
- [ ] **D-c. Producer-side changes (reverses part of 10c).** Operator decision 2026-10-02: monitoring systems send to n8n directly. Cutover per source in the order above; Zabbix stamps `runbook_id` via trigger tags (closes 10c follow-up 3 for Zabbix); the 10c routing table remains the fallback. Confirm the order and that Patroni/Frigg changes are acceptable as separate small PRs.
- [ ] **D-d. Model.** Sonnet-class default; Opus-class for groups of ≥ 3 alerts or `critical` + unknown layer. A workflow setting, not hard-coded.
- [ ] **D-e. Which alerts reach the agent.** Zabbix: an action on `critical`/`alert`-severity triggers (and `info` for canary hosts only). Everything else still goes to Discord as today; no execution.
- [ ] **D-f. n8n placement:** dedicated LXC (above) vs reuse of the asgard instance. Default dedicated; asgard reuse is cheaper but inherits its failure domain and public webhook surface.
- [ ] Verify Urd RAM headroom for the LXC and Frigg headroom for the API (concurrency 1–2) — read-only check.

---

## 10d1 — Ingress, direct monitoring→n8n path and n8n instance (stub workflow, no LLM)

Build the plumbing end to end with a **stub workflow that just echoes the normalized alert into the thread**, so the LLM is never the thing being debugged.

- [ ] n8n-aiops LXC: TF (`asgard-lxcs`) + NetBox declaration + Ansible role (`n8n` role; reuse `caddy-reverse-proxy` for the allowlisted front door) + playbook; secrets (`N8N_ENCRYPTION_KEY`, owner password) to Vault; reboot-tested.
- [ ] **Workflows in git** (`aiops/n8n/workflows/*.json`): the community edition has no source-control feature, so an Ansible task runs `n8n import:workflow` from the repo checkout; the editor is for exploration, **git is the source of truth** and drift is reported by re-exporting and diffing. CI lints the JSON (no inline secrets, only Toolbelt-API/Discord hosts, no Code/Execute-Command nodes except an allow-listed set).
- [ ] Toolbelt API skeleton on Frigg: `/ingest/<source>` (source adapter → normalize → fingerprint dedupe → correlation group), `/group/<id>`; SQLite in `/var/lib/aiops-toolbelt/`. States `received → grouped → running → posted → resolved`. Same fingerprint within a cooldown (30 min) updates the thread's last-seen/count instead of re-running; a severity escalation or *resolved* half updates/re-opens.
- [ ] **Correlation window (important):** alerts within ~90 s are grouped into **one incident**, one execution. The 2026-09-30 Skuld freeze produced a burst across many guests; per-alert executions would give twenty contradictory "bad release" verdicts instead of one "dead host". Group key proposal: shared hypervisor (NetBox → Proxmox node), else cluster, else time.
- [ ] **AuthN:** each source's IP (Hugin for Zabbix, Semaphore, Patroni nodes, Frigg) and n8n's IP allow-listed at the listeners; long random path token per source on the n8n webhook (TF `random_password` → Vault `secret/ansible/aiops/n8n-ingest-token/<source>`, read by that source's role); separate API token n8n→Toolbelt. AGH rewrites via `terraform/adguard/rewrites.tf` for the n8n-aiops and toolbelt names.
- [ ] **Zabbix first:** new media type `n8n` (webhook media type with a JSON message template carrying the fields above) + an action that fires it for the agreed triggers, in the Zabbix role (Ansible; check [`known-issues/zabbix.md`](../known-issues/zabbix.md) — the S4 decision notes the `community.zabbix` trigger/item modules are untested here, so media types/actions may need direct API calls). Trigger tags carry `runbook_id`; recovery messages enabled. Hermod media type untouched. Other sources follow in 10d3 once the agent works (additive and independent).
- [ ] **Independence test (gate for `critical`):** stop n8n → a Zabbix test trigger still produces the Discord alert via Hermod and the Zabbix action log shows only the n8n operation failed; restart → no backlog flood. Then the happy path: one trigger → Discord alert **and** a `#diagnoses` post; same event within cooldown → no second post; a burst of 5 → 1 thread; recovery → thread updated. Reboot-test both hosts (CLAUDE.md persistence rule).
- [ ] **Circuit breakers:** per-execution wall-clock cap, daily execution cap (default 40), max queue depth; breach → one Discord post "diagnosis budget exhausted", never silent. Audit: structured JSON from the API → journald → vlagent → VictoriaLogs, keyed by fingerprint (roadmap principle 7); n8n execution history is the second record.

## 10d2 — Read-only toolbelt (+ replay mode)

Each tool is an endpoint of the Toolbelt API backed by a read-only credential; n8n's agent node exposes them as HTTP-request tools. No generic `exec`, no shell, no SSH.

| Tool | Credential (all new, all write-less) | Scope / how it is enforced |
|---|---|---|
| `kube` | ServiceAccount `aiops-readonly` (Flux-managed manifests under `k8s/asgard/…`), **1 h token minted by the loader** | ClusterRole `get/list/watch` on workload/node/event/CRD objects incl. HelmRelease/Kustomization status and `pods/log`; **no `secrets`, no `pods/exec`, no `*/proxy`, no non-read verbs**. Proof: `kubectl auth can-i --list` + attempted `delete` / `get secret` denied |
| `logs` / `metrics` | none (VL/VM have no auth); internal niflheim Gateway | API forms only `/select/logsql/*` and `/api/v1/query*`; **verify against [`known-issues/observability.md`](../known-issues/observability.md) that no delete/admin path is exposed on those routes** |
| `zabbix` | user `aiops-ro` with a **read-only role** + API token (`secret/ansible/aiops/zabbix-token`) | API allow-lists `*.get`. First use verifies the 10c route regexes against live triggers (closes follow-up 4). Zabbix is Ansible-managed → role addition, not click-ops |
| `netbox` | local user with **view-only permission**, token `write_enabled=false` | `GET` only (separate from the TF-provider token quirks in [`known-issues/netbox.md`](../known-issues/netbox.md)) |
| `pve` | `aiops@pve` + API token, role **PVEAuditor** on `/` | node/guest status, `/cluster/resources`. Answers "is the hypervisor alive, which guests share it" — **the core of host-vs-workload** |
| `semaphore` | Semaphore user in a **guest/read-only** project role + token | task history/output only; verify the role has no `task:run` |
| `reach` | none | ICMP/TCP-connect from Frigg, bounded count/rate — substitutes for SSH |
| `git` | none (repo is public) | log/show/diff on a read-only clone kept fresh by a timer; known-issues and CLAUDE.md readable |
| `vault-status` | none (`sys/health`, `sys/seal-status` unauthenticated) | per-pod seal state via `kube` — closes follow-up 10 |
| `registry` | none | reads `runbooks.yml`/`actions.yml`; lists actions the agent may *propose* (never invoke) |

- [ ] Mint each identity in its owning IaC (TF for Proxmox/Vault policy, Flux for the SA, Ansible for Zabbix/Semaphore/NetBox), secrets to Vault `secret/ansible/aiops/*`; operator mirrors to 1P (standing rule).
- [ ] **Negative tests per credential** — the actual deliverable of "write-less": a script attempts one write with each and asserts denial; re-run after any credential change.
- [ ] Root loader `aiops-toolbelt-env` (AppRole, narrow policy `aiops-toolbelt-read`: exactly those KV paths).
- [ ] **Replay mode:** a request header `X-AIOPS-Replay: <scenario>` makes every endpoint return recorded responses from `aiops/replays/<scenario>/` keyed by (tool, normalized args) instead of touching the network; an unrecorded call returns `NO_RECORDING` (counted). This makes the acceptance incidents repeatable — you cannot re-freeze Skuld.
- [ ] **Done when:** every endpoint works live read-only and in replay; negative tests pass; the service process shows no static secret; an unknown or write-shaped call from n8n is rejected by the API (tested with a deliberately bad workflow, not assumed).

## 10d3 — Agent workflow, diagnosis template, Discord UX, acceptance

- [ ] **Agent workflow** (`aiops/n8n/workflows/diagnose.json`): group in → AI Agent node (Anthropic chat model, tools = the Toolbelt endpoints, iteration cap) → structured-output parser → validate → Discord. Prompt + task template in `aiops/diagnosis/` (versioned, linted in CI): the alert group, runbook entry, doc excerpt, proposable actions, and the **host-vs-workload rubric** — *establish layer before cause.* Evidence order: (1) Proxmox node/guest status; (2) other guests on the **same hypervisor** going silent together (Zabbix agent availability, VL last-log timestamp, `reach`); (3) K8s node `Ready`/lease heartbeat; (4) only then workload: pod/HelmRelease state, logs, recent commits. "Several `Terminating` pods + Helm timeouts + a silent node" is a **dead host**, not a bad release; rollback/redeploy is never the first suggestion for a host-layer finding (the Immich lesson).
- [ ] **Output contract** `aiops/schema/diagnosis.v1.schema.json`: `layer` (`host|hypervisor|workload|network|drift|external|unknown`), `confidence`, `summary`, `evidence[]` (`tool`, `args`, `finding`), `known_issue_refs[]` (file#anchor / `runbook_id`), `tier`, `proposed_actions[]` (**registry names + args only**, validated against `actions.yml`; informational), `not_checked[]`, `human_next_step`. Validated in the Toolbelt API (`/validate`) before posting; invalid → a short "diagnosis failed validation" post with trimmed raw text, never a retry loop. Fixtures + linter coverage like the other schemas.
- [ ] **Discord rendering:** thread title `[tier/layer] host — check`; first post = summary + layer + confidence; evidence in a follow-up; links to the known-issue/runbook; footer = fingerprint + execution id + model. Updates on re-fire/resolve.
- [ ] **Out-of-contract detection:** the API logs every tool call per execution; a call outside the allow-list raises a `critical` and the execution is cut off.
- [ ] **Replay harness** `aiops/replay/` + `aiops/replays/<scenario>/` (frozen alert group + recorded tool responses + expected-outcome file). It triggers the workflow's test webhook with the replay header; in replay the workflow returns the diagnosis to the caller instead of posting to Discord. Scored by assertions, not prose: `layer == X`, `known_issue_refs ∩ must_cite ≠ ∅`, `proposed_actions ∩ forbidden = ∅`, zero out-of-allow-list calls, `NO_RECORDING` count reported. **3 runs per scenario, all three must pass.** Run manually (needs the key, costs money); CI lints fixtures and workflow JSON only. Recorded data is reconstructed from each incident's retro and any VL history still retained — each scenario's README says so (synthetic-but-faithful where logs have aged out).
- [ ] **Acceptance scenarios** (roadmap) + **negative controls**, so the agent can't pass by always giving the same answer:

  | Scenario | Must conclude | Must cite | Control |
  |---|---|---|---|
  | 2026-09-30 Skuld freeze | `host`/`hypervisor` dead, **not** a bad release | the Skuld-freeze incident + lxc-proxmox gotchas | paired with a *genuine* bad-chart-bump scenario → must say `workload` |
  | 2026-10-01 Calico datastore prune | K3s per-addon pruning is the cause | calico-datastore-prune incident, k3s-lifecycle | benign Calico pod restart → must **not** claim prune |
  | etcd raft-drop syslog flood | disk-fill risk on the surviving CPs | the etcd syslog-flood runbook | — |
  | 2026-05-17 Authentik/Redis CP-taint | scheduling/taint miss | k8s-scheduling gotcha + that incident | — |

- [ ] **Live pass:** inject a fault on a canary (stop a unit) → `info`-tier alert → Hermod → n8n → real execution against the live toolbelt → thread posted; plus one real `critical`-path synthetic (the Hermod smoketest tag) for the non-canary path. No action is possible by construction.
- [ ] Ops doc `docs/procedures/aiops-diagnosis.md` (run, rotate credentials, import workflows, replay, read a thread, disable); post-flight docs per CLAUDE.md; decisions rows once confirmed.

---

## Exit criteria (Phase 10d)

1. Every alert from a migrated source reaches Discord (via Hermod) **and** n8n (directly); **stopping n8n, or Hermod, leaves the other path working** (both tested). Zabbix migrated; the remaining sources are done or have a recorded cutover plan.
2. The four acceptance scenarios + their controls pass 3/3 with zero out-of-allow-list tool calls.
3. A canary fault and a synthetic critical yield a correct thread end to end, live.
4. Negative write tests pass for every credential; n8n holds no read credential of its own; the Toolbelt API process has no sudo and no static secret.
5. Storms group to one thread; duplicate fingerprints don't re-run; budget exhaustion is announced, not silent.
6. n8n-aiops and Frigg reboot-tested with everything enabled; workflows reproducible from git.

**Rollback = disable the n8n media type/action (and each other source's n8n POST, one flag per role)**, and stop the LXC/API if desired. The Discord path never changed; no write path existed.

## Risks

| Risk | Mitigation |
|---|---|
| Touching live producers (Zabbix action, Patroni callback, prober, Frigg) to add the n8n send | Per-source, additive, separately validated PRs; Zabbix first; short-timeout/no-retry/log-only sends; per-source disable flag; independence test before `critical` |
| Prompt injection via logs/alert text | No writable credential; n8n holds no read credential; API-side allow-list; fenced prompt; schema-validated output |
| n8n is a code-execution engine holding the Anthropic key + Discord webhook | Dedicated, internal-only, egress-allow-listed LXC; CI lint of workflow JSON; no public routes; key scoped/dedicated |
| Less hard control over the agent loop than a harness-enforced allow-list | Enforcement lives in the Toolbelt API; iteration + daily caps; out-of-contract detection cuts the run |
| Confident wrong layer (the Skuld failure, by the agent) | Evidence-order rubric; negative-control replays; mandatory `confidence`/`not_checked`; nothing executes in 10d |
| Workflows drift in the n8n UI (no source control in community edition) | Git is source of truth; import role; periodic export-and-diff |
| Frigg or the LXC dies | Frigg already T3 and Gatus-watched; losing SQLite = lost dedupe, not lost alerts (Discord path independent; a Hermod outage no longer blinds the agent) |
| Token/cost creep | Daily cap, per-execution iteration limit, correlation window, separate key |
| Stub runbooks / unresolved S4 findings give weak output | Expected, surfaced as findings; write the stubs before 10e |

## Rollout

Shadow first: for ~2 weeks `#diagnoses` is read by the operator only and each thread is graded by hand; the 10e entry criterion becomes "N diagnoses graded, layer-correct rate ≥ X%" (X to set with the operator after seeing real data — start the conversation at 90 %).

## What I need from the operator

1. Confirm or flip **D-a … D-f** — in particular **dedicated n8n LXC vs the asgard instance**.
2. OK to modify the producers for the analysis path (Zabbix first; order above)?
3. Create the `#diagnoses` forum channel + webhook (D-b); I seed `secret/ansible/aiops/discord-diagnosis`, you mirror to 1P.
4. Nothing else blocks 10d1: the stub workflow + the Zabbix media type needs neither the Anthropic key nor the toolbelt.

## Next

After 10d: **10e** — executor endpoint (allow-listed Semaphore templates), the Discord bot, and approvals as an n8n Wait flow. Pull forward before it: the five `stub` runbooks (10c follow-up 6), the Flux-actions ServiceAccount (follow-up 9, partly delivered by `aiops-readonly` here), and the PBS capacity / restore-drill items for 10g.
