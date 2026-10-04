<!-- docs/procedures/aiops-forecasting.md -->

# Procedure — AIOps forecasting detectors (Phase 10h1)

*Plan: [`operations/10h-predictive-change.md`](../operations/10h-predictive-change.md). Code: [`aiops/toolbelt/forecast.py`](../../aiops/toolbelt/forecast.py), tests: [`aiops/tests/test_forecast.py`](../../aiops/tests/test_forecast.py). Status 2026-10-04: **live**. An hourly pass reads VictoriaMetrics and Zabbix; findings land in the Toolbelt's `forecasts` table and the bot posts one quiet card per finding with Useful / Noise buttons. Nothing here pages, ever. Code: [`forecast_zabbix.py`](../../aiops/toolbelt/forecast_zabbix.py), [`forecast_store.py`](../../aiops/toolbelt/forecast_store.py), [`aiops/bot/fcast.py`](../../aiops/bot/fcast.py).*

## What the detectors do

Two pure functions over a series of `(unix_ts, value)`, because homelab series are not smooth:

| Detector | Question | Returns a finding when | Stays silent when |
|---|---|---|---|
| `slow_fill` | "When does this cross its capacity?" (least-squares line over a 14-day window) | the fitted line reaches `capacity` within `horizon_days` (14), with at least `min_points` samples and `r2 >= 0.7` | the slope is flat or negative, the fit is poor, the data has a gap larger than 30% of the window (including a series that stopped days ago), the value is already at or over capacity (that is an alert, not a forecast), or the crossing is further out than the horizon |
| `fast_rise` | "Is it growing much faster than it was?" (rate over the last hour against the rate over the previous day) | the recent rate is above an absolute floor and at least 3x the baseline (or the baseline is flat) | the series is tiny (below the floor), falling, or a cleanup/rotation reset sits in the window (a reset is never read as a rise) |

Confidence is `high` / `medium` / `low` from the fit's R squared. A `Dedup` helper reports a fingerprint at most once per 24 h unless the ETA at least halved. Neither detector predicts a sudden fault: those stay alerts. A forecast is a notification, never a page.

`daily_extreme()` collapses a series to one point per day (max for used-space, min for free-space) before `slow_fill`, to remove the sawtooth of log rotation, GC and nightly backups.

## From finding to card

1. **The pass** (`aiops-toolbelt-forecast.timer`, hourly) runs every target, appends NEW findings (after the 24 h de-duplication) to `forecast-shadow.jsonl` and
   rewrites `forecast-current.json` with every finding that holds right now. A failing source is reported per target in the unit's journal
   (`forecast: <target>: error=...`), never skipped silently.
2. **The Toolbelt** ingests that file (every minute, only when it changed) into the `forecasts` table, one row per fingerprint, `open` or `resolved`, and writes events:
   `created` (first sight, or a resolved one coming back), `escalated` (the ETA is at most half of what was last announced), `reposted` (still open after a week, unless
   labelled noise), `resolved` (absent from two passes in a row). A findings file older than 6 h is ignored (the job is down; absence proves nothing). At most 8 new
   findings are announced per ingest, so a first run cannot flood the channel.
3. **Ratatoskr** turns events into cards in the forecasts channel (`ratatoskr_forecasts_channel_id`, default: the AIOps chat channel): the signal in words, the target, the
   confidence, the estimated date, and the fit (now vs the limit). **Useful** and **Noise** record a label (operator only); noise stops the weekly repost. Escalations, reposts and
   resolutions add a short reply under the card. `/aiops forecasts` lists the open ones, nearest ETA first.

The labels are the tuning evidence: thresholds change only through a reviewed PR to `aiops/toolbelt/forecast.py` (`DEFAULT_TARGETS`), never in place.

## Policy (changed 2026-10-04)

The plan said: shadow for 14 days, then decide whether anything posts. Operator decision: **post quietly from day one** (a forecast is a heads-up that never pages, cards are cheap to
dismiss, and Useful / Noise gives the tuning signal immediately instead of after a silent fortnight). The shadow JSONL is still written.

## Targets

| Target | Source | Limit | Notes |
|---|---|---|---|
| `k8s-pvc-used-ratio` | VictoriaMetrics, kubelet volume stats | 0.85 (assumed) | CSI/NFS PVCs only; local-path volumes are not reported by kubelet |
| `victorialogs-data-size` | VictoriaMetrics | none (fast-rise only) | 1 GB/h floor is a guess |
| `fleet-fs-used` | Zabbix `vfs.fs.dependent.size[<mount>,pused]`, hourly trends, daily max | 0.90 | `/`, `/boot`, `/data`, LXC rootfs on the PVE hosts; monitored hosts only (template items have no data) |
| `pve-storage-used` | Zabbix `proxmox.node.disk` / `maxdisk` for `local-lvm`, `pbs-backup`, `munin-nfs`, `local` | 0.85 | `local-lvm` is the thin pool; `pbs-backup` is the PBS datastore as PVE sees it (the plan's PBS-capacity signal, no PBS API token needed); items are de-duplicated (every PVE host carries a copy) |
| `memory-used` | Zabbix `vm.memory.size[pavailable]`, daily low-water mark | 0.90 | a steady creep only: memory does not fill linearly |

Zabbix keeps 31 days of raw history and 365 days of hourly trends (verified 2026-10-04), more than the 14-day fit needs. The forecast unit reads Zabbix with the Toolbelt's read-only credential
(`*.get` methods only); the API unit itself has no route to metrics or Zabbix-history reads by design.

## How it runs

`forecast_run.py` (oneshot, its own unit and timer; the API unit has `IPAddressDeny=any`) takes `--url` (VictoriaMetrics), `--zabbix-creds` (the runtime credentials directory), `--current`
(the findings file) and `--state` (de-duplication across runs). The Toolbelt takes `--forecast-current` pointing at the same file. Check it:

```bash
systemctl list-timers aiops-toolbelt-forecast.timer; journalctl -u aiops-toolbelt-forecast -n 12 -o cat   # one line per target: series=N points=M, or error=...
```

## Not built

- The **Draft fix PR** button from the plan: no finding has a known remedy mapped to an author class yet (PBS capacity is a retention decision, a full disk is a human call); the `capacity` class waits on it.
- A repo-held `aiops/forecast.yml` for thresholds (they live in `DEFAULT_TARGETS`, changed by PR).
- The backtest on the etcd syslog-flood replay, an NVMe latency / SMART signal (needs `smartctl` data in Zabbix), a memory allocation ledger, a GitHub-issue sink.

## Adding a metric

A VictoriaMetrics series: add a `Target` with a PromQL that returns one series per label set, the limit in the series' unit, an absolute `floor_per_hour` for `fast_rise` and `daily="max"|"min"` for a daily
sawtooth. A Zabbix family: add a selector to `forecast_zabbix.ZabbixSource` (it must return `[(label, [(ts, ratio)])]`) and a `Target` with `zabbix=(kind, ...)`. `validate_targets()` (tested) rejects a target with
no source, a slow-fill without a limit, or an unknown selector.
