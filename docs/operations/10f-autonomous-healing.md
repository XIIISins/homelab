<!-- docs/operations/10f-autonomous-healing.md -->

# Phase 10f — Autonomous T1 healing: plan and as-built

*Built 2026-10-03. Status: 🟡 **code complete and tested; deploy, then the ~14-day canary soak** (the soak is the exit criterion, so 10f is not "done" until it passes). Parent: [`aiops-roadmap.md`](aiops-roadmap.md) §10f. Procedure: [`procedures/aiops-autonomy.md`](../procedures/aiops-autonomy.md). Predecessor: [`10e-approval-actions.md`](10e-approval-actions.md).*

---

## Goal and boundary

For one narrow, reviewed class of faults the Toolbelt may **approve and run a proposal itself** and tell the operator afterwards. Everything else still waits for a human click exactly as in 10e. Investigation was already autonomous (10d) and is unchanged.

What stays true after 10f:

- **The model never decides to act.** A diagnosis only supplies a *proposal*. Whether it runs itself is the Toolbelt's decision from the registry (`aiops/actions.yml` `autonomy:`), which only a reviewed PR can change. n8n holds no authority and no approval path.
- **Off by default.** The master switch `autonomy` is off after every deploy and every Toolbelt database reset; the operator turns it on with `/aiops autonomy on`.
- **Scope is a list, not a tier.** Autonomous actions may target only `autonomy.hosts` (the three canaries for the soak), a subset of `host_tiers.T1`. Widening to real replicas is a later PR.
- **Autonomy never touches approval-only actions.** A policy covers one registry action; `replay-role`, `flux-reconcile` and everything T2+ stay `max_autonomy: approval`.

## What was built

| Piece | Where | Notes |
|---|---|---|
| Registry scope | `aiops/actions.yml` `autonomy:` (+ schema, lint `check_autonomy`) | hosts, limits (2/target/h, 10/policy/day, breaker 3 failures/3600 s), three policies; `restart-unit` becomes `max_autonomy: auto` **only through a policy** |
| Policy logic | `aiops/toolbelt/autonomy.py` | pure functions: static gates, unit / HelmRelease / drift verdicts |
| Engine gate | `Engine.consider_auto` in `aiops/toolbelt/actions.py` | master switch, kill switch, maintenance, breaker, scope, layer, confidence, runbook, rate limits; approves as `auto:<policy>` atomically, then runs on the executor's normal path |
| Independent precheck | `Engine._precheck` | the Toolbelt reads reality itself before acting (`service-status` for units, `kube.get` for HelmReleases, the dry-run `changed` count for drift); a healed fault ends **`skipped`**, an inconclusive read ends `cancelled`, neither executes |
| Circuit breaker | `Engine._finish_auto` | failed / unverified autonomous runs inside the window trip `autonomy_breaker`; only the system can trip it, only an operator can re-arm it |
| Audit + report | `auto_log` table, `/report`, `Engine.report` | why a proposal did *not* run, by-policy / by-target outcomes, breaker trips, flapping targets |
| Runbook | `RB-UNIT-STOPPED-T1` (`runbooks.yml`, marker in the procedure) | the diagnosis must name it for the policy to apply |
| Bot | `aiops/bot/` | buttonless card + announcement for a proposal the Toolbelt ran itself, breaker notice in the thread, `/aiops autonomy on|off|reset-breaker`, `/aiops report [days]`, autonomy line in `/aiops status`, `skipped` state |
| Soak helper | `scripts/canary/fault` | stop / restore / status for the two canary units on `canary-N` only |
| Display fix (rode along) | `number` on every proposal view | cards say `#1, #2, #3` within a conversation or incident; the primary key is in the footer (PR #83) |

## Decisions

| Question | Choice | Why |
|---|---|---|
| Who decides autonomy | The Toolbelt, from the registry | the model's output is untrusted input; a prompt-injected diagnosis must not be able to widen its own scope |
| Which policy first | `restart-failed-unit`, canaries only | smallest blast radius, naturally idempotent, easy to inject a fault for; the other two policies ship **disabled** |
| Precheck | the Toolbelt reads the unit itself | the diagnosis can be stale or wrong; restarting an already-healthy unit is the classic flap |
| Failure handling | breaker, not retry | a restart that fails is information; looping on it hides it. 3 in an hour stops everything until a human looks |
| What counts toward limits | runs that started | a `skipped` / `cancelled` precheck changed nothing, so it neither spends the budget nor trips the breaker |
| Default | off | an operator opt-in per deploy is cheap; an unattended surprise is not |

## Not in 10f

Real-replica targets, the `reset-stalled-helmrelease` and `replay-drifted-baseline` policies (disabled until the soak passes; drift also needs a trigger source), semaphore auto-apply on drift, the syslog-flood vacuum cleanup. The restore drill and 10g (rebuild loop) come after.

## Live results (2026-10-03, canary soak day 1)

Every matrix row below was injected on the canaries with `scripts/canary/fault` and watched through the Toolbelt journal; each ended as designed.

| Row | Result |
|---|---|
| `zabbix-agent2` stopped | alert -> diagnosis (runbook `RB-UNIT-STOPPED-T1`) -> auto-approved by `restart-failed-unit` -> precheck (`inactive`) -> restart -> verify -> **succeeded**, incident resolved about a minute later |
| `vlagent` stopped | same, **succeeded** (canary-2, after the breaker reset) |
| Disaster-severity synthetic flag | alert -> diagnosis posted, **no action proposed** (no policy covers it, correct) |
| healed before the Toolbelt acts | the unit was restarted by hand a second after the proposal appeared: the precheck read `active`, proposal ended **`skipped`**, nothing restarted |
| `/aiops maintenance on` | proposal created, `auto_skipped: maintenance`, left pending for a human |
| `/aiops kill` | `auto_skipped: kill-switch`, left pending |
| restart that keeps failing | two runs **failed** (masked `zabbix-agent2`) and one **`verify_failed`** (`vlagent` binary not executable) inside an hour: the system tripped `autonomy_breaker`, the bot posted its notice, `/aiops report` showed `breaker_trips 1` |
| fault while the breaker is open | `auto_skipped: breaker-open`, left pending |
| operator reset, then a fault | breaker re-armed, the next fault was healed autonomously and verified |
| non-canary host / unlisted unit, per-target hourly limit | **unit-tested only** (`aiops/tests/test_autonomy.py`): the soak never faults a real host, and under the 30-minute incident cooldown a single canary check cannot be triggered often enough to reach the hourly limit |

**What the live run found (all fixed, all in the repo):** (1) a canary "agent not available" fault is Average severity and Gna never receives Average: the canaries got their own `Canary smoke test` Zabbix template with High/Disaster triggers; (2) a hand-made trigger named "temporary, safe to ignore" made the agent propose nothing: smoke triggers are named for the fault and routed to the runbook; (3) the model wrote `zabbix-agent2` without `.service` and the registry refused it: the Toolbelt completes a bare unit name before validating and the rejection journal line now says why; (4) a verify task read as an empty result because Semaphore had not flushed its log: the Toolbelt re-reads a finished task's log before judging it; (5) the first diagnosis run hit the agent's iteration cap: raised from 8 to 10 and the prompt now names the unit-stopped class.

**Test-design lessons** (so the next soak run is not confused by them): an incident is per hypervisor group (`node:urd`), so only one canary fault can be open at a time and a second alert while one is open is folded into it; the same host and check inside 30 minutes of a recovery is a `reopened`, not a new run (a masked unit also makes the `systemd.unit.info` item unsupported, so use `nodata`-based faults or remove the binary's execute bit to make a restart fail); the proposal budget is 30 a day.

The 14-day soak clock started 2026-10-03: the pass criteria (zero flapping, every autonomous action audited, no unexplained breaker trip) are read from `/aiops report 14`.

## Exit criteria (from the roadmap)

The injected-fault matrix on the canaries passes ([procedure](../procedures/aiops-autonomy.md#soak-on-the-canaries-10f3-about-14-days)), **zero flapping**, every autonomous action audited (`proposal_auto_approved` in the Toolbelt journal and VictoriaLogs). Rollback for anything here: `/aiops autonomy off`.

## Deploy (operator-free; Claude runs these)

1. Merge the PR (CI gate).
2. Frigg `asgard-control.yml --tags aiops-toolbelt`; Ratatoskr `asgard-ratatoskr.yml --tags ratatoskr`; Gná `asgard-gna.yml --tags n8n`.
3. `/aiops status` shows `Autonomy: off`; run the matrix with `/aiops autonomy on`.
