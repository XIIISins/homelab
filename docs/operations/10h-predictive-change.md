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
| a GitHub App installation token (preferred over a PAT) with Contents + Pull requests write on this repo only | Vault, Terraform state, a kubeconfig with write, the Semaphore executor token, the PVE token, any 1Password access |
| the Toolbelt's **read-only** tools through an *author* role token (so it can look at live state) | the approver and agent-propose tokens |
| `terraform` / `helm` / `kubeconform` / `ansible-lint` binaries, offline | the ability to apply anything |

### The PR gate (what makes "a human merges" real)

1. **Scope check as a required CI job (GitHub-hosted, no Frigg dependency).** Keyed on the **PR author identity** (the App's bot login), never on a label the agent could omit. For an agent-authored PR it fails if the diff touches any of: `.github/` and `terraform/github/` (the ruleset and CI), `CLAUDE.md`, `aiops/actions.yml` and `aiops/runbooks.yml` and `aiops/schema/`, `aiops/toolbelt/`, `aiops/bot/`, `aiops/n8n/`, `terraform/vault/` (policies), `terraform/semaphore/`, `ansible/inventory/group_vars/all/vault.yml`, `docs/operations/decisions.md`, or any file it did not declare in the change request's allowed-path list. In other words **an agent cannot edit the files that define what agents may do**.
2. **Ruleset tightening (operator apply of `terraform/github/`).** For `main`: require one approving review from someone other than the last pusher (the bot cannot approve its own PR), and do not grant the App the ability to enable auto-merge. Today the ruleset requires only the `CI gate`, so this is a real change and an operator decision.
3. **Plan-diff, as evidence not enforcement.** The repo's CI is deliberately static: it never plans (no state, no credentials; `terraform plan` on PRs is listed as a later option pending a read-only state role). 10h2 therefore produces plan-diffs **on Frigg**, from the author's worktree (planning from a worktree is allowed; only apply is restricted):

   | PR touches | Plan-diff produced | Needs |
   |---|---|---|
   | `k8s/**` | `render-diff.sh` (old vs new render with our values) + `images-exist.sh`; kubeconform | nothing new |
   | `ansible/**` | `ansible-playbook --check --diff --limit <host>` from the worktree under the one-at-a-time lock, or the `replay-role-check` shape | the existing fleet SSH agent on Frigg |
   | `terraform/**` | `terraform plan` of the touched module | **new read-only identities**: a state-read-only IAM user, a PVE audit-only token, read access for providers that read Vault. Until they exist, Terraform PRs carry no plan-diff and are limited to docs/vars the reviewer can reason about |

   The **summary** (resource counts, addresses, pass/fail of each check) goes in the PR description; the **full diff goes to the private Discord thread**, because a PR comment on a public repo is public. Everything passes the same secret scrubber the Toolbelt uses on chat replies, plus the repo's gitleaks.
4. **Human review and merge.** The PR links the evidence (alert fingerprint or forecast row, audit-trail ids), the plan-diff summary and a rollback line. Stale agent PRs close after 14 days; at most 3 are open; one PR per finding fingerprint; no force-push; commit identity is the bot's.

### PR classes (start small)

| Class | Trigger | Allowed paths | Plan-diff | Starts |
|---|---|---|---|---|
| Docs / known-issue edits | incident, review comment | `docs/known-issues/**`, `docs/procedures/**`, `docs/incidents/**` (this is 10h3) | none | first |
| Capacity remedies | forecast with a known remedy | a Terraform disk/size value in one module, an Ansible variable (e.g. PBS prune policy), a Zabbix template threshold | the matching row above | second |
| Repo-matches-intent after drift | non-convergeable drift | the role/var that differs; the PR states the direction (Git is truth, so "change the live state" is the default and is a human action; "change Git" needs the operator's reason) | check-mode diff | third |
| Version bumps | `platform-version-drift` | stays with `chart-bump` | its own | not 10h2 |

Not in 10h2: any change to the registry, autonomy or rebuild scope, Flux structure, Vault policies, secrets, firewall (UCG is not in IaC anyway), or anything that deletes data.

### Acceptance

At least 3 agent-authored PRs merged with evidence and plan-diff attached; a deliberate probe PR that touches a forbidden path fails the scope check; the bot cannot approve or merge its own PR (negative test); a drafting session that exceeds its budget is stopped and says so; every draft traceable to its change request and approval.

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
| Who authors PRs | a Frigg-side session as `aiops-author` with a GitHub App token, started by an operator click | the brain host reads untrusted text and must hold no write credentials |
| How "human merges" is enforced | scope check keyed on author identity + review-from-another-user ruleset + no auto-merge for the App | labels can be omitted; identity cannot |
| Plan-diff | produced on Frigg, summary in the PR, full diff in the private thread | CI never has state or credentials; PR comments are public |
| Terraform plan coverage | after read-only identities exist; until then no plan-diff for Terraform PRs | no read-only state role today |
| Incident drafts | generated timeline, grounded narrative, hypothesis label, docs-only paths | an incident write-up that invents facts is worse than none |

## Not in 10h

Autonomous merge of anything; agent edits to autonomy/rebuild/registry/CI/ruleset/Vault policy files; paging from a forecast; forecasting sudden faults; a ticket system; fully automatic incident publication; machine-learning forecasters (robust linear plus a rate detector is the bar until it is shown to be insufficient); version-bump PRs (that is `chart-bump`).

## Exit criteria

- **10h1:** shadow mode ran 14 days; after tuning, >= 3 real notes arrived >= 24 h ahead of what would have been an alert, <= 2 false notes per week; the backtest on the CP syslog flood passes; NVMe latency has a baseline for Urd/Verd/Skuld.
- **10h2:** >= 3 agent-authored PRs merged by a human with evidence and plan-diff; the forbidden-path probe fails CI; the bot cannot approve or merge its own PR.
- **10h3:** the next three incidents each began from a draft with no invented facts.

## Operator steps (the ones Claude cannot do)

- Apply `terraform/github/` (ruleset tightening) and create/install the GitHub App; store its key in Vault and mirror to 1Password (the mirror is the operator's).
- Create the PBS audit-only API token, the Zabbix read scope for history/trends if the current token lacks it, and (for Terraform plan-diff) the read-only state IAM user and PVE audit token; each is a console or `terraform apply` step.
- Hardware/host: install `smartctl` + the agent2 SMART plugin on the PVE hosts (a `proxmox-host` role change plus an operator-run playbook), and decide whether to expose etcd metrics (K3s config change on the CPs).
- Decisions: the ticket sink, the ruleset change, the author's per-day budget, and which PR classes are allowed first.
