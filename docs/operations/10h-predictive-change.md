<!-- docs/operations/10h-predictive-change.md -->

# Phase 10h — Predictive and agent-authored change: plan

*Drafted 2026-10-03. Status: **planned, not started** (design only). Parent: [`aiops-roadmap.md`](aiops-roadmap.md) §10h. Structure mirrors [`10e-approval-actions.md`](10e-approval-actions.md) and [`10f-autonomous-healing.md`](10f-autonomous-healing.md). Builds on the 10d Toolbelt (read-only tools, audit log, grounding gate) and the 10e bot; reuses the [`chart-bump`](../../.claude/agents/chart-bump.md) agent's machinery for 10h2.*

---

## Goal and boundary

Stage 5: move the loop **upstream of the alert**. Three independent pieces, none of which gives the agent any new power over live systems:

- **10h1 — Forecasting (T0).** Notice slow-moving problems (a disk that fills in 12 days, headroom eroding, the backup datastore, an SSD getting slower) and tell the operator **before** the alert threshold, with the evidence.
- **10h2 — Agent-authored PRs.** Turn a finding (drift, an incident follow-up, a forecast with a known remedy) into a **reviewed pull request** with a plan-diff. A human merges; the agent never merges, never applies.
- **10h3 — Incident drafts.** Draft `docs/incidents/` entries and known-issues updates from the audit trail, for human edit.

What stays true after 10h:

- **10h is read-and-propose only.** The Toolbelt's registry, autonomy scope, rebuild scope and the executor are untouched. Nothing in 10h can widen them: the agent-PR CI check forbids an agent-authored PR from touching the files that define its own authority (below).
- **A human merges every agent-authored PR.** Merging `k8s/` IS the deploy (Flux), so this is the same gate as any other change.
- **Untrusted text stays on the brain side.** Alert text, logs and chat are read through an LLM that has no write credentials. The PR-authoring session runs on a different host with a different identity and starts only on an operator click.
- **Forecasts are notifications, not pages.** They go to a quiet channel and never raise Hermod `critical`/`alert`; the existing alert thresholds are unchanged.

## Dependencies and sequencing

The roadmap says 10h depends on 10f. That holds for 10h2 and 10h3 (they need the approval/click plumbing and a stable engine). **10h1 is T0 read-only and does not need autonomy**, so its shadow-mode baseline (below) can start during the 10f soak and in parallel with 10g, which gives it the weeks of history forecasts need. Recommended order: 10h1 shadow now-ish, 10h3 (lowest risk, docs only), 10h2.

---

## 10h1 — Forecasting

### Where it runs and what it produces

A **scheduled T0 job inside the Toolbelt** (daily, plus an hourly fast-rise pass), using the read-only credentials the Toolbelt already holds. No new service and no new write path. Each finding becomes a row in a Toolbelt `forecasts` table (signal, target, ETA, evidence, state `open|acked|resolved`, fingerprint `forecast:<signal>:<target>`), posted by the bot to a quiet `#forecasts` channel as one thread per finding with a vmui or Zabbix graph link, the fitted line, and the recommended remedy. Deduplicated by fingerprint; re-posted weekly while unchanged; escalated (still not a page) when the ETA halves.

**"Ticket" means this row plus its thread.** There is no ticket system in the homelab and adding one is out of scope; `/aiops forecasts` lists open ones and the card has a **Draft fix PR** button that hands a finding with a known remedy to 10h2. A GitHub issue sink is an open question, not a requirement.

### Method (honest about what it can and cannot catch)

Two detectors, because homelab series are not smooth:

1. **Slow-fill.** Robust linear fit (Theil-Sen, resistant to outliers) over 14 days of **daily maxima** (daily *minima* of free space or available memory), which removes the sawtooth from log rotation, GC and nightly backups. Notice when the projected crossing of the **existing alert threshold** is within 14 days (that is what makes it "before the alert").
2. **Fast-rise.** Slope over the last 6 hours projected to full within 24 hours. This is what would have caught the etcd raft-drop syslog flood on the CPs (a step change that a 14-day linear fit cannot see).

Neither predicts a sudden fault (a freeze, a runaway process that fills a disk in minutes); those remain alerts. Linear extrapolation is also wrong for memory, which does not fill linearly: memory uses low-water-mark trend plus the allocation ledger, not a fill date.

**Shadow mode first.** For the first 14 days the job computes and stores findings but posts nothing, so thresholds are tuned on real history, then notes are enabled. Each note has useful/noise buttons; the labels feed threshold changes made through reviewed PRs to a repo-held `aiops/forecast.yml` (no in-place tuning).

### Signals: what exists, what is new

| Signal | Source that exists today | New data needed | Method / threshold basis | Honest caveats |
|---|---|---|---|---|
| **Disk fill** (LXC, VM, PVE host filesystems) | Zabbix "Linux by Zabbix agent" `vfs.fs.size[*,pused]` on the fleet (history and trends) | a Toolbelt read tool for Zabbix history/trends (typed args: host, key, days <= 90); **verify Zabbix history/trend retention covers 30+ days** (housekeeping settings not confirmed in the docs) | slow-fill + fast-rise on daily max; threshold = the Zabbix disk-fill trigger | LXC rootfs on thin LVM: the thin pool itself needs a separate series (the pool fills while each guest looks fine) |
| **Thin-pool / VM storage** (`local-lvm`) | Zabbix Proxmox template (HTTP) storage items | confirm the per-node thin-pool usage item exists and is trended | same | the Proxmox template has known 3x discovery redundancy (open question) |
| **K8s volume and node-pressure** (PVC fill, VictoriaMetrics/VictoriaLogs own disk) | VictoriaMetrics: kubelet volume stats for CSI/NFS PVCs and the Victoria self-metrics (data size, already on the `04-victoria-self` dashboard); the Toolbelt already has a VM query tool | none for CSI-backed PVCs; **local-path volumes are not reported by kubelet volume stats** (they live on the worker's `/data`, which Zabbix covers via the agent on the worker VM) | MetricsQL `predict_linear` for the Toolbelt query, same two detectors | cardinality and retention growth in VM/VL show up here first |
| **Memory headroom** (PVE hosts, workers) | Zabbix `vm.memory.size[available]`; K8s requests vs allocatable from kube-state-metrics (already scraped) | an **allocation ledger**: sum of guest `memory` per node (PVE API / NetBox VM records) vs host RAM | trend of the daily minimum available + ledger; notice on trend slope or ledger above a cap | the real K8s constraint is **CPU requested** (workers are ~85-90 % requested, see the chart-bump notes), so forecast requests/allocatable, not usage |
| **PBS capacity** | nothing automated: the datastore sits on the Munin NFS share and its usage is only seen by hand (75 % on 2026-10-03) | **PBS API `status/datastore-usage` read** via a new audit-only PBS API token (Vault), and the Synology volume free space | fit the **post-GC daily minimum** (daily GC frees ~13 GB, so the raw series is a sawtooth); notice at ETA to 85 % within 30 days | also answers the open "datastore-usage alert at 80 %" item; retention policy is a human decision (grow vs prune) |
| **NVMe latency creep** (Urd's DRAM-less Gen 4 drive, and its peers) | Zabbix agent block-device items (`vfs.dev.read.await` / `write.await` per device) **if enabled on the PVE hosts: confirm**; etcd slow-fsync lines in VictoriaLogs from the CP VMs (log-derived, available today) | **SMART data** (`smart.disk.*` needs `smartctl` and the agent2 SMART plugin on the PVE hosts: new, a `proxmox-host` role change); optionally **etcd metrics** (`etcd_disk_wal_fsync_duration_seconds`) which are **not scraped** today (vmagent scrapes kubelet, cAdvisor and kube-state-metrics only; exposing them needs a K3s config change on the CPs and a scrape job: a deliberate, separately approved change) | creep = week-over-week p95 await growth on the same device vs its sibling hosts (Urd vs Verd/Skuld, same hardware class); plus NVMe `Percentage Used`, spare and media errors once SMART exists | await depends on load: normalise to a fixed quiet window; 14 days of baseline before any note; 03:00 backup IO must be excluded |

### Acceptance

- **Backtest, not a promise:** replay Zabbix history up to a point before a known event and check the detectors. The etcd syslog-flood disk-fill (a 10d replay scenario) must produce a fast-rise note at least a few hours ahead of the High alert; the slow-fill detector is validated on the real PBS datastore series.
- **False-notice budget:** after tuning, at most 2 notes per week fleet-wide in steady state; every note carries evidence the operator can open in one click.
- **No new alert path:** a forecast can never page.

---

## 10h2 — Agent-authored PRs

### Shape (the `chart-bump` pattern, generalised)

`chart-bump` is the proven model: investigate read-only, work in a worktree, check before changing (render-diff, image existence), one item at a time, commit, test what the change touches, update docs. 10h2 adds a **second agent definition** (`.claude/agents/`, written at build time) that shares the helper-script approach with `chart-bump` and does **not** replace it: version bumps stay `chart-bump`'s job; 10h2 handles findings that are not version bumps.

### Who may start a draft, and on what authority

A draft starts from a **change request** the operator approves with a button (Discord user id, same trust model as 10e approvals), created from: a forecast finding with a known remedy; a drift-check result that is not role-convergeable; an incident follow-up; or a person asking in `AIOps-chat`. The approval authorises *drafting a PR*, nothing else. Gna (n8n) can create a change request but cannot approve it or start the author.

The author runs on **Frigg** as a separate unix user (`aiops-author`), as a headless Claude Code session under a systemd unit with a wall-clock limit (30 min), a per-day budget cap and at most 2 concurrent drafts. It works in a git worktree of a fresh clone, never in the operator's checkout and never in the rebuild runner's. It holds:

| Has | Does not have |
|---|---|
| a **fine-grained PAT** (operator decision 2026-10-04: a long-lived token is accepted over a GitHub App) belonging to a dedicated machine user, scoped to this repo only with Contents + Pull requests read/write and a one-year expiry (calendar the renewal) | Vault, Terraform state, a kubeconfig with write, the Semaphore executor token, the PVE token, any 1Password access |
| the Toolbelt's **read-only** tools through an *author* role token (so it can look at live state) | the approver and agent-propose tokens |
| `terraform` / `helm` / `kubeconform` / `ansible-lint` binaries, offline | the ability to apply anything, the fleet SSH key, a DigitalOcean token |

### The PR gate (what makes "a human merges" real)

1. **Scope check as a required CI job (GitHub-hosted, no Frigg dependency).** Keyed on the **PR author identity** (the machine user's login, never the operator's own, which is why the PAT belongs to a separate account), never on a label the agent could omit. For an agent-authored PR it fails if the diff touches any of: `.github/` and `terraform/github/` (the ruleset and CI), `CLAUDE.md`, `aiops/actions.yml` and `aiops/runbooks.yml` and `aiops/schema/`, `aiops/toolbelt/`, `aiops/bot/`, `aiops/n8n/`, `terraform/vault/` (policies), `terraform/semaphore/`, `ansible/inventory/group_vars/all/vault.yml`, `docs/operations/decisions.md`, or any file it did not declare in the change request's allowed-path list. In other words **an agent cannot edit the files that define what agents may do**.
2. **No ruleset change (operator decision 2026-10-04).** A four-eyes review rule is not enforced: this is a single-operator homelab, so the existing `CI gate`-only ruleset stays. "A human merges" holds because the operator does the merge. Honest limit: a write token *can* merge a PR once the `CI gate` is green and nothing technical stops the author doing so, so this is a convention backed by the scope check (it limits what a merged agent PR can contain) and by a negative probe in acceptance; accepted for a single-operator homelab; the agent never enables auto-merge on its own PRs by convention, and the author-identity scope check in 1 is what keeps it away from its own guardrails.
3. **Test on throwaway substrate, never on prod (operator decision 2026-10-04).** The repo's CI is deliberately static: it never plans or applies (no state, no credentials). The author therefore proves a change on the substrates built for exactly that, and the **evidence** it attaches is the result of a real run, not a read-only guess against prod. The author holds **no fleet SSH key, no PVE/Vault/state credential and no DigitalOcean token**; it asks for a substrate and the operator approves, the same button pattern as 10e (the Toolbelt/runner does the stand-up and tear-down).

   | PR touches | Tested on | How | Needs |
   |---|---|---|---|
   | `k8s/**`, Helm values | an ephemeral **burst K3s** ([`procedures/burst-substrate.md`](../procedures/burst-substrate.md)) | `render-diff.sh` + `images-exist.sh` + `kubeconform` offline first; then applied to the burst cluster by the Toolbelt/runner and the touched workload checked (pods ready, the chart's own smoke test). One burst cluster per PR, TTL 4 h, the Frigg reaper is the cost guard | operator approval of the burst test; nothing new built |
   | `ansible/roles/**` for LXC/VM roles | the **canary pool** (10b1, `site-nonprod.yml`), via the existing `replay-role-check` then `replay-role` shapes on a canary | `--check --diff` then a real run on a canary, then a second run for idempotence (`changed=0`); canary alerts stay capped at the Hermod `info` tier | operator approval; the executor runs it through Semaphore on the `aiops` project, the author never holds the SSH key |
   | `k3s` role / cluster-level Ansible | burst K3s | the role is the one that builds burst clusters, so a PR to it is proven by building one | operator approval |
   | `terraform/**` | not planned against prod. Pure-value changes (a size, a variable) that a rebuilt canary or burst cluster exercises are tested there; anything else (live Proxmox, Vault, NetBox, AdGuard state) carries **no plan-diff** and is limited to docs and values the reviewer can reason about | n/a | a state-read-only identity is **not** built for this; revisit only if Terraform PRs become common |
   | `docs/**`, `aiops/` code, tests | the repo's own CI plus the offline linters in the author's worktree | n/a | nothing |

   Every test run is bounded by the existing caps (rate limits, TTL, the kill switch and the maintenance flag apply). The **summary** (what ran where, resource counts, pass/fail per check, burst/canary ids) goes in the PR description; the **full output goes to the private Discord thread**, because a PR comment on a public repo is public. Everything passes the same secret scrubber the Toolbelt uses on chat replies, plus the repo's gitleaks.
4. **Human review and merge.** The PR links the evidence (alert fingerprint or forecast row, audit-trail ids), the test summary (what ran on which burst/canary) and a rollback line. Stale agent PRs close after 14 days; at most 3 are open; one PR per finding fingerprint; no force-push; commit identity is the bot's.

### PR classes (start small)

| Class | Trigger | Allowed paths | Tested on | Starts |
|---|---|---|---|---|
| Docs / known-issue edits | incident, review comment | `docs/known-issues/**`, `docs/procedures/**`, `docs/incidents/**` (this is 10h3) | repo CI only | first |
| Capacity remedies | forecast with a known remedy | a Terraform disk/size value in one module, an Ansible variable (e.g. PBS prune policy), a Zabbix template threshold | a canary/burst run (table above) | second |
| Repo-matches-intent after drift | non-convergeable drift | the role/var that differs; the PR states the direction (Git is truth, so "change the live state" is the default and is a human action; "change Git" needs the operator's reason) | canary check-mode + real run where a canary exercises the role, else none | third |
| Version bumps | `platform-version-drift` | stays with `chart-bump` | its own | not 10h2 |

Not in 10h2: any change to the registry, autonomy or rebuild scope, Flux structure, Vault policies, secrets, firewall (UCG is not in IaC anyway), or anything that deletes data.

### Acceptance

At least 3 agent-authored PRs merged with evidence and a burst/canary test summary attached; a deliberate probe PR that touches a forbidden path fails the scope check; the agent never merges (probe: a green agent PR stays open until the operator merges); a drafting session that exceeds its budget is stopped and says so; every draft traceable to its change request and approval.

---

## 10h3 — Incident drafts

**Trigger:** an incident reaches a terminal state in the Toolbelt (resolved, or an executed proposal finished) and it crossed a bar (>= 30 min, an action executed or refused, >= 3 correlated alerts, or the operator runs `/aiops draft-incident <id>`).

**Inputs, all already recorded:** the incident's alerts and diagnosis (`diagnosis.v1`), every tool call served (the grounding audit log), proposals and their events, executor results, VictoriaLogs for the window, `git log` around it, the matched runbook and known-issue, and whether the known-issue existed before.

**Output, as one PR (via the 10h2 machinery, docs-only class):**

- `docs/incidents/YYYY-MM-DD-<slug>.md` in the existing incident format plus a row in `docs/incidents/README.md`.
- A proposed patch to the matching `docs/known-issues/<subject>.md` entry (rule, Why, symptom/diagnostic, recovery), or a new entry; never gotcha text in `CLAUDE.md`.
- A "follow-ups" list in the incident doc. The draft does **not** edit `decisions.md`, `open-questions.md`, `build-sequence.md` or `CLAUDE.md`: it lists what the post-flight checklist would update, for the human.

**Facts vs inference, mechanically separated:** the timeline and evidence sections are generated deterministically from timestamps and audit ids (no LLM, each line links its source); the narrative sections are LLM-written under the same grounding gate as diagnoses (every claim cites an evidence id, ungrounded sentences are dropped); the root-cause section is labelled **hypothesis**; the document opens with a `DRAFT: agent-written, operator to edit` banner. Secrets and token-shaped strings are scrubbed before the PR is opened (the repo is public).

**Honest data gap:** the Toolbelt only sees what Zabbix sends it (High/Disaster) plus what it was asked. Incidents worked interactively in an operator session leave only git history and logs; for those the draft is a timeline skeleton, and the operator's own notes are the substance. Audit retention in the Toolbelt's SQLite versus VictoriaLogs needs checking before drafts can reach back further than weeks (open question).

**Acceptance:** the next three incidents each start from a draft; the operator rates each (kept / heavily rewritten); success is most kept. A draft that invents a fact (a claim with no evidence id) is a bug and blocks the class.

---

## Decisions

| Question | Choice | Why |
|---|---|---|
| Where forecasts run | in the Toolbelt as a scheduled T0 job | same read-only credentials, audit and bot feed; no new service or write path |
| What a "ticket" is | a Toolbelt `forecasts` row + a quiet Discord thread, with a "Draft fix PR" button | no ticket system exists; revisit a GitHub-issue sink only if wanted |
| Forecast vs Zabbix `forecast()` triggers | the Toolbelt job, not server-side triggers | Zabbix problems below High reach neither Hermod nor n8n, one method must span Zabbix and VictoriaMetrics sources, and dedupe/escalation live in one place |
| Detector design | Theil-Sen on daily extrema + a fast-rise pass; shadow mode first | removes sawtooth; the flood class needs the fast pass; avoids a noisy launch |
| Who authors PRs | a Frigg-side session as `aiops-author` with the machine user's fine-grained PAT, started by an operator click | the brain host reads untrusted text and must hold no write credentials |
| How "human merges" is enforced | scope check keyed on author identity + the operator does the merge (no four-eyes rule; single-operator homelab) | labels can be omitted; identity cannot |
| Testing | on throwaway substrate (burst K3s, canary pool), approved per run; summary in the PR, full output in the private thread | CI never has state or credentials; PR comments are public; the author holds no host or cloud credentials |
| Terraform coverage | no plan against prod; value-only changes are exercised on canary/burst, the rest carries no plan-diff | a read-only state identity is not worth building until Terraform PRs are common |
| Incident drafts | generated timeline, grounded narrative, hypothesis label, docs-only paths | an incident write-up that invents facts is worse than none |

## Not in 10h

Autonomous merge of anything; agent edits to autonomy/rebuild/registry/CI/ruleset/Vault policy files; paging from a forecast; forecasting sudden faults; a ticket system; fully automatic incident publication; machine-learning forecasters (robust linear plus a rate detector is the bar until it is shown to be insufficient); version-bump PRs (that is `chart-bump`).

## Exit criteria

- **10h1:** shadow mode ran 14 days; after tuning, >= 3 real notes arrived >= 24 h ahead of what would have been an alert, <= 2 false notes per week; the backtest on the CP syslog flood passes; NVMe latency has a baseline for Urd/Verd/Skuld.
- **10h2:** >= 3 agent-authored PRs merged by a human with evidence and a burst/canary test summary; the forbidden-path probe fails CI; the agent never merges its own PR.
- **10h3:** the next three incidents each began from a draft with no invented facts.

## Operator steps (the ones Claude cannot do)

- Create the machine user (collaborator with write on this repo), generate its fine-grained PAT (this repo only, Contents + Pull requests read/write, 1-year expiry), store it in Vault (`secret/ansible/aiops/author-pat`) and mirror to 1Password (the mirror is the operator's); calendar the renewal.
- Create the PBS audit-only API token, the Zabbix read scope for history/trends if the current token lacks it; each is a console or `terraform apply` step.
- Hardware/host: install `smartctl` + the agent2 SMART plugin on the PVE hosts (a `proxmox-host` role change plus an operator-run playbook), and decide whether to expose etcd metrics (K3s config change on the CPs).
- Decisions: the ticket sink, the author's per-day budget, and which PR classes are allowed first.
