<!-- docs/procedures/aiops-forecasting.md -->

# Procedure — AIOps forecasting detectors (Phase 10h1)

*Plan: [`operations/10h-predictive-change.md`](../operations/10h-predictive-change.md). Code: [`aiops/toolbelt/forecast.py`](../../aiops/toolbelt/forecast.py), tests: [`aiops/tests/test_forecast.py`](../../aiops/tests/test_forecast.py). Status 2026-10-03: **detectors built and tested, NOT wired**: nothing starts them, nothing posts to Discord, no scheduler exists yet.*

## What the detectors do

Two pure functions over a series of `(unix_ts, value)`, because homelab series are not smooth:

| Detector | Question | Returns a finding when | Stays silent when |
|---|---|---|---|
| `slow_fill` | "When does this cross its capacity?" (least-squares line over a 14-day window) | the fitted line reaches `capacity` within `horizon_days` (14), with at least `min_points` samples and `r2 >= 0.7` | the slope is flat or negative, the fit is poor, the data has a gap larger than 30% of the window (including a series that stopped days ago), the value is already at or over capacity (that is an alert, not a forecast), or the crossing is further out than the horizon |
| `fast_rise` | "Is it growing much faster than it was?" (rate over the last hour against the rate over the previous day) | the recent rate is above an absolute floor and at least 3x the baseline (or the baseline is flat) | the series is tiny (below the floor), falling, or a cleanup/rotation reset sits in the window (a reset is never read as a rise) |

Confidence is `high` / `medium` / `low` from the fit's R squared. A `Dedup` helper reports a fingerprint at most once per 24 h unless the ETA at least halved. Neither detector predicts a sudden fault: those stay alerts. A forecast is a notification, never a page.

`daily_extreme()` collapses a series to one point per day (max for used-space, min for free-space) before `slow_fill`, to remove the sawtooth of log rotation, GC and nightly backups.

## Shadow mode (the only mode that exists)

`run_once(query, now, log_path)` evaluates the configured targets and **only appends JSONL to a local file** and returns the findings; there is no Discord, ticket or Hermod output. `query(promql, start, end, step)` is supplied by the caller (production: the Toolbelt's `metrics.range`; tests: a fake), and a failing query skips that target without hiding the others. The CLI (`python3 aiops/toolbelt/forecast.py --once --log PATH`) refuses to run without a query function rather than guessing a data source.

Plan: run silently for 14 days, then read the log against what really happened (did the PVC fill when forecast, how many false positives), tune the thresholds, and only then decide whether any finding becomes a notification.

## Targets today

Only series the repo really has are configured with a query: PVC used ratio from kubelet volume stats (CSI/NFS volumes only; local-path volumes are not reported; the 0.85 capacity is an assumed placeholder to tune in shadow) and `vl_data_size_bytes` (fast-rise only; the 1 GB/h floor is a guess). Fleet disk, the PVE thin pool, memory and the PBS datastore are marked `needs_data_source`: the repo has no node_exporter, and Zabbix history/trends are not readable by the Toolbelt yet.

## Wiring it live (the remaining steps)

1. A read tool or helper that returns `[(label, series)]` from VictoriaMetrics (`metrics.range` exists as an agent tool; the runner needs the same call without the agent).
2. A schedule (Toolbelt background thread or a systemd timer on Frigg) and a log path on Frigg.
3. Zabbix history/trends read access for the fleet disk and memory targets, and an audit-only PBS token for the datastore.
4. Real thresholds in a repo file after the shadow period.

## Adding a metric

Add a `Target` to `DEFAULT_TARGETS` with a PromQL that returns one series per label set (one per PVC, host or mount), the capacity in the same unit as the series, an absolute `floor_per_hour` for `fast_rise`, and `daily="max"|"min"` if the signal has a daily sawtooth. `validate_targets()` (tested) rejects a target with no query and no `needs_data_source`, a slow-fill without a capacity, or an unknown detector. Do not invent a metric name: if the repo does not scrape it, mark the target `needs_data_source` and say what source it needs.
