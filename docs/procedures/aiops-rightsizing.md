<!-- docs/procedures/aiops-rightsizing.md -->

# Procedure — pod rightsizing: findings, the digest, PRs and the post-merge watch (Phase 10i)

*Plan and rationale: [`10i-rightsizing.md`](../plans/active/10i-rightsizing.md). The data and the quiet forecast rows are described in [`aiops-forecasting.md`](aiops-forecasting.md) ("Rightsizing rows"). Everything here proposes; a human reviews every PR, and nothing touches a pod.*

## The digest (10i3)

Gná posts one **Rightsizing digest** per period in the forecasts channel (the chat channel when none is set). The header is a card with the per-worker scoreboard (memory requested, limits and used, CPU requested; now against the previous digest and the first one, the baseline; the vmui link per worker is in the Toolbelt's digest JSON), the results of earlier PRs and the coverage (controllers without a VPA, suppressed findings with the reason). A thread under it holds up to five suggestion cards, memory under-requests first, then by what they free. A card is a forecast row: **Useful / Noise** labels work as on a forecast card, and a suggestion called Noise is not repeated until its proposed number has moved by more than 30 %. "Worth a look" in the header lists memory creep and OOMKilled containers that have no number to propose.

The very first digest is the **baseline** and waits until the VPA recommendations are `vpa.min_sample_age_days` (7) old.

| Command (operator only) | What |
|---|---|
| `/aiops rightsizing now` | build and post a digest immediately (works while the VPA is young; it says how old the data is) |
| `/aiops rightsizing cadence weekly\|biweekly\|monthly` | change the period; stored in the Toolbelt's database, the repo's `digest.cadence` is the default |
| `/aiops rightsizing status` | cadence, last digest, next due |

Mechanics: the bot asks the Toolbelt `POST /rightsizing/digest/tick` every ten minutes; the Toolbelt builds a digest only when the cadence says so, from the forecast job's last pass (`forecast-current.json`: the quiet rows plus a per-worker snapshot). A digest stays *unposted* until the bot records its message, so a failed Discord post is retried, never skipped. A stale pass (the job has not run for 36 h) produces no digest.

Not built: the **LLM-written** tuning text of the plan ("Gná's read of memory-creep and high-baseline workloads against the chart's values"). The digest lists the creep facts and the OOM notes; ask Gná in the chat (`kube.rightsizing`) for the chart-level reading until that workflow exists.

## Rightsizing PRs (10i4)

A suggestion card's **Draft PR** button (shown only for an allow-listed workload with a number to apply, and only while the `rightsizing` author class is enabled) files **one change request per workload**: every open finding of that workload in one old → new table, written by the Toolbelt, with the evidence per container and the per-worker memory requested before and after. You approve the request on its card; the author drafts the PR; **you** merge it (merging `k8s/` is the deploy). At most three rightsizing requests are in flight at once, and a second press for a workload with a PR in flight is refused.

What keeps the PR to resources:

- the class's `allow` list (`aiops/author-classes.yml`): `helmrelease.yaml`, `deployment*.yaml`, `statefulset*.yaml` and `redis.yaml` under `k8s/asgard/apps/<app>/`, and Authentik's `helmrelease.yaml` / `redis.yaml`; the policy file `aiops/rightsizing.yml` is never editable by an agent PR;
- [`.github/scripts/ci-resources-only.py`](../../.github/scripts/ci-resources-only.py), run by the dispatcher before it pushes and by CI's `agent-scope` job (from the PR's base commit) on every `agent/rightsizing/*` PR. It fails on any difference that is not a container `requests`/`limits` × `cpu`/`memory` value (or a `resourcesPreset` becoming `"none"` beside an explicit `resources:` block), on any removed key, new file, non-YAML file or changed list, on an added or changed CPU limit, a memory limit below its request, and on values below the floors in `rightsizing.yml`. A probe PR that also edits an image tag fails (`aiops/tests/test_resources_only.py` is that probe);
- the **burst test** (the `k8s` class's): the Toolbelt proposes `pr-burst-test` when the PR opens; it proves the pod starts and becomes Ready inside the new limits. Authentik lives under `infrastructure/`, so its PRs say why they are not burst-tested; the watch is the real proof for everything.

The PR description carries `## Evidence` (the Toolbelt's table, copied verbatim, not the session's words), the checks, the burst-test section and a rollback line (`git revert`; Flux rolls the workload back).

The class has been enabled since 2026-10-10. To switch it on or off, change `enabled` in `aiops/author-classes.yml` in a reviewed PR; the Draft button appears and disappears with it (the Toolbelt reads the file at start, so restart `aiops-toolbelt` after a deploy that changes it, which the code-deploy template does).

## The 72-hour watch (10i5)

When a `rightsizing` change request is reported **merged**, the Toolbelt starts a watch on the workload (the request's body carries a `<!-- rightsizing-spec ... -->` block the Toolbelt wrote: workload, and the old and new values per container). It first **waits** until the new values are really live: every pod of the workload started after the merge, carries the new requests and limits, is Ready, and the controller is fully available. That is the data-driven reading of "the HelmRelease is Ready at the new revision"; values that never show up within 24 h end as **inconclusive** (look at Flux and the HelmRelease). Then it **watches for 72 h**, reading VictoriaMetrics every ten minutes:

| Signal since the values went live | Result |
|---|---|
| a container OOMKilled · CrashLoopBackOff · working set above 90 % of the new limit · two or more restarts · the controller not fully available on two checks in a row · an alert (severity alert/critical) that names the workload | **regressed** at once |
| one restart | a warning in the verdict |
| none of the above for 72 h | **held** |

The verdict is a `watch` event on the change request: Ratatoskr replies under the request's card (held, regressed with the reasons, inconclusive), the next digest lists it under "Results of earlier PRs", and a regression carries a **Draft revert PR** button. The button files a `rightsizing` change request that restores the old values (it waits for its own Approve and is watched like any other change); nothing reverts by itself. `GET /rightsizing/watches?state=watching,held,regressed,inconclusive,waiting` (approver role) lists them.

A **limit cut** (`memory-over-limit`) is only suggested for a workload whose latest request cut **held**, and `memory.over_limit.enabled` in `rightsizing.yml` is now `true` because that gate lives in the Toolbelt.

Not covered: a Zabbix problem that does not name the workload is not correlated to it (the alert check reads the Toolbelt's own alert table, matching the workload's name as a whole word).

## Checks

```bash
journalctl -u aiops-toolbelt-forecast -n 12 -o cat          # one `rightsizing:` line per daily pass: containers, findings, suppressed reasons, errors
journalctl -u ratatoskr -n 20 -o cat | grep rightsizing      # rightsizing_digest_posted / apply_error
```
