<!-- docs/procedures/aiops-autonomy.md -->

# Procedure — AIOps autonomy: self-healing T1 faults, the guards, the soak (Phase 10f)

*Design: [`operations/10f-autonomous-healing.md`](../operations/10f-autonomous-healing.md). Builds on [`aiops-actions.md`](aiops-actions.md) (proposals, approval, the bot). Registry scope: the `autonomy:` section of [`aiops/actions.yml`](../../aiops/actions.yml).*

## What "autonomous" means here

For a narrow, reviewed class of faults the Toolbelt may **approve and run a proposal itself**, with no human click, and tell you afterwards in the incident thread. It is never the model's decision: the diagnosis only supplies the *proposal*; the Toolbelt applies its own gates and its own independent check of reality. The gates, in the order they are evaluated (the first that fails is recorded as the reason and the proposal simply waits for a human as in 10e):

1. **Master switch ON** (`/aiops autonomy on`; the default after every deploy is **off**), **kill switch off**, **maintenance off**, **circuit breaker closed**.
2. The proposal came from a **diagnosis** (not chat) and is not a replay.
3. An **enabled policy** in `actions.yml` covers the action; the **target host is inside `autonomy.hosts`** (the soak scope: the canaries).
4. The diagnosis **layer** is one the policy allows, its **confidence** is at least the policy's, and it **names the policy's runbook** (an `automatable: auto` runbook).
5. **Rate limits**: at most 2 autonomous actions per target per hour and 10 per policy per day.
6. At run time, the Toolbelt's **own precheck** confirms the fault is real *right now* (a unit that is already active is never restarted; a HelmRelease must read `Stalled=True`; a drift run must show a small, non-empty dry-run diff). If the world has already healed, the proposal ends `skipped`, not executed.
7. After the run, the registry **verify** post-condition must hold. A run that fails or does not verify counts against the **breaker**: 3 within an hour stops all autonomy until an operator resets it.

## Switches (all from Discord, operator only)

| Command | Effect |
|---|---|
| `/aiops autonomy on` / `off` | the master switch (default off) |
| `/aiops autonomy reset-breaker` | re-arm after the breaker tripped (investigate first) |
| `/aiops kill` / `resume` | stops everything, including autonomous starts |
| `/aiops maintenance on` / `off` | no autonomous action while on (use it for `k3s-upgrade`, `os-updates`, a PVE patch) |
| `/aiops status` | all four flags, the day's counters, the breaker |
| `/aiops report [days]` | what autonomy did: by policy, target and outcome; skipped reasons; breaker trips; flapping targets |

## Policies (initial)

| Policy | Heals | Precheck | State |
|---|---|---|---|
| `restart-failed-unit` | an allow-listed unit that stopped on a host that is alive (`restart-unit`) | `service-status` shows ActiveState failed/inactive | **enabled**, canaries only |
| `reset-stalled-helmrelease` | a Stalled HelmRelease of a stateless release (`flux-reconcile-reset`) | `kube.get` shows `Stalled=True` | disabled until the soak passes |
| `replay-drifted-baseline` | role drift on a T1 host (`replay-role`) | the prior `replay-role-check` shows a non-empty diff of at most `max_changed` (10) | disabled until the soak passes and a drift trigger exists |

<!-- runbook: RB-UNIT-STOPPED-T1 -->
## When an allow-listed unit stops on a T1 host while the host is alive

This is the fault class `restart-failed-unit` heals. The unit must be on the `restart-unit` allow-list for that host (`actions.yml`: AdGuardHome on the AdGuard replicas, `tailscaled` on the Tailscale LXCs, apprise-api/caddy on Hermod, vlagent/zabbix-agent2 on the canaries); anything not on that list is never restarted by the loop. A restart has no inverse, so if the post-condition does not hold the thread says so and shows the rollback note: look at the unit's journal, do not loop. The breaker exists precisely to stop a unit that keeps failing from being restarted forever.

## Soak on the canaries (10f3): about 14 days

Goal: prove the loop heals real faults, never flaps, and never acts when it should not. Do **not** widen `autonomy.hosts` or enable another policy until it passes.

1. **Deploy** the 10f code (Frigg: `asgard-control.yml --tags aiops-toolbelt`; Ratatoskr: `asgard-ratatoskr.yml --tags ratatoskr`; Gná: `asgard-gna.yml --tags n8n`), then `/aiops autonomy on`.
2. **Inject faults on the canaries only** with `scripts/canary/fault` (it refuses any host that is not `canary-N`): `scripts/canary/fault stop canary-2 zabbix-agent2` stops the unit, then follows the loop and prints the timeline (alert → diagnosis → autonomous restart → verified). Run the matrix below at least once each, spread over the soak; leave the rest to chance and to the canaries' ordinary behaviour.
3. **Matrix** (each row must end as stated):

| Injected | Expected |
|---|---|
| `zabbix-agent2` stopped on a canary | restarted autonomously, verified, thread card says "Executed automatically by policy restart-failed-unit" |
| `vlagent` stopped on a canary | same (if an alert fires; otherwise nothing, and that is correct) |
| the unit is restarted by hand before the loop acts | proposal ends `skipped` (already active) |
| the same unit stopped 3 times within an hour | the per-target limit stops the 3rd; later faults wait for a human |
| the unit keeps failing after restart (inject a broken unit) | 3 failed/unverified runs trip the breaker; autonomy stops; the bot says so |
| `/aiops maintenance on`, then a fault | nothing runs by itself; the proposal waits for a human |
| `/aiops kill`, then a fault | same |
| a fault on a **non-canary** T1 host, or an unlisted unit | not autonomous (outside scope / not allow-listed); at most a human-approved card |

4. **Check the report** every few days: `/aiops report 14`. **Pass criteria:** every injected fault in the matrix behaved as above; **zero flapping** (no target acted on ≥ 3 times in 6 hours without a reason); **zero breaker trips** except the one you provoked on purpose; every autonomous action has an audit trail (`journalctl -u aiops-toolbelt | grep proposal_` joins by proposal id, and the same lines are in VictoriaLogs).
5. **Then, and only then**, open a PR that adds real replicas to `autonomy.hosts` (start with one), and later enables the next policy. Rollback for any of it is `/aiops autonomy off`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| A fault was diagnosed but nothing ran | `/aiops report` lists the skip reason per proposal (`autonomy-off`, `maintenance`, `breaker-open`, `host-out-of-scope`, `runbook-mismatch`, `confidence`, `target-rate-limit`, ...); the diagnosis must name `RB-UNIT-STOPPED-T1` |
| `skipped` instead of executed | the precheck found the unit already active: the world healed itself; working as intended |
| The breaker keeps tripping | the unit is not restart-healable (config/disk/port): fix the cause; do not raise the limits |
| An autonomous action ran that surprised you | `/aiops autonomy off`, then read the thread card and `journalctl -u aiops-toolbelt` for `proposal_auto_approved`; tighten the policy by PR |
