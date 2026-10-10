<!-- docs/plans/README.md -->

# Build plans: index

Every multi-step build plan lives here, in one of three folders. **This index is the only place a plan's state is recorded.** Live status ("what's left", dates, next steps) is the "What's left" section of [`../operations/build-sequence.md`](../operations/build-sequence.md); open tasks and prerequisites are in [`../operations/open-questions.md`](../operations/open-questions.md). A plan file holds design, steps and as-built notes, not a status headline: its old header status was moved to a "Header status history" section at the bottom (a dated snapshot).

| Folder | Meaning |
|---|---|
| [`active/`](active) | Being built, soaking, or still has open exit criteria. |
| [`done/`](done) | Exit criteria met and live. Kept as the design record; edit only to correct facts. |
| [`deferred/`](deferred) | Researched or designed, deliberately not scheduled. |

**Rules**
- A new plan goes in `active/`; add a row below and a phase row in `build-sequence.md`.
- When a plan's exit criteria are met, `git mv` it to `done/` (the link check catches stale links) and update its row. Don't write status into the plan itself.
- Tick progress in `build-sequence.md`, decisions in [`decisions.md`](../operations/decisions.md), gotchas in [`known-issues/`](../known-issues), retrospectives in [`incidents/`](../incidents).

## Active

| Plan | Phase | Scope |
|---|---|---|
| [`aiops-roadmap.md`](active/aiops-roadmap.md) | 10 | AIOps & self-healing umbrella: principles, blast-radius tiers, phase map 10a–10i. 10a–10e live; 10f–10i below. |
| [`10f-autonomous-healing.md`](active/10f-autonomous-healing.md) | 10f | Autonomous T1 healing, deployed; exit criterion is the ~14-day canary soak. |
| [`10g-rebuild-loop.md`](active/10g-rebuild-loop.md) | 10g | Fleet rebuild loop (canaries → replicas → workers); stage A proven. |
| [`10h-predictive-change.md`](active/10h-predictive-change.md) | 10h | Forecasting, PR author, drift/incident drafts; live, exit criteria open. |
| [`10h-k8s-burst-test.md`](active/10h-k8s-burst-test.md) | 10h | Burst-cluster tests for `k8s/` PRs (child of 10h); slices 1–2 built, 3–4 open. |
| [`10i-rightsizing.md`](active/10i-rightsizing.md) | 10i | Pod rightsizing with VPA; recommend-only live, Toolbelt tool and findings built (shadow), digest/PR stages open. |
| [`5h-jellyfin.md`](active/5h-jellyfin.md) | 5h | Jellyfin LXC + media automation (J0–J6, M0–M4); built, J5 acceptance open. |
| [`chart-bumps-2026-09.md`](active/chart-bumps-2026-09.md) | — | Helm chart / platform bump review and wave handoff, worked by the `chart-bump` agent. |

## Done

| Plan | Phase | Scope |
|---|---|---|
| [`1.0-stabilization.md`](done/1.0-stabilization.md) | S1–S7 | Stabilization waves, complete 2026-05-31. |
| [`10d-diagnosis-chatops.md`](done/10d-diagnosis-chatops.md) | 10d | Diagnosis-only chat-ops: Gná (n8n), Toolbelt read API, Zabbix → n8n. |
| [`10e-approval-actions.md`](done/10e-approval-actions.md) | 10e | Approval-gated actions and the Discord bot (Ratatoskr). |

## Deferred

| Plan | Scope |
|---|---|
| [`vault-2x-assessment.md`](deferred/vault-2x-assessment.md) | Vault 1.21 → 2.x breaking-change assessment (research only; long-term OpenBao question). |
