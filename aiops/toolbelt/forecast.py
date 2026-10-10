"""Forecasting detectors (Phase 10h1): slow-fill and fast-rise, as pure functions, plus a thin shadow-mode runner.

Four detectors, because homelab series are not smooth (docs/plans/active/10h-predictive-change.md):
  slow_fill  a least-squares line over a window projects the days until a series crosses its capacity/threshold.
  fast_rise  the recent rate against the prior baseline rate, for step changes a 14-day line cannot see (a log flood).
  creep      this week's p95 against last week's, for a latency that drifts upward (an NVMe getting slower).
  step_up    a counter that moved at all inside the window (SMART media errors, a jump in wear).
None predicts a sudden fault; those stay alerts. The Zabbix and VictoriaMetrics sources hand over HOURLY points, so every window
here is sized for hourly data (a one-hour window holds one point and could never fire). Forecasts are notifications, never pages.

SHADOW MODE: run_once() only appends findings to a local JSONL file and returns them. There is no Discord, ticket or
Hermod output in this slice, and nothing starts this module; wiring it into the Toolbelt is a separate, deliberate step.

A series is a list of (unix_ts, value), oldest first. Values and capacity share one unit (a ratio, or bytes).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path

Series = list  # list[tuple[float, float]]
DAY = 86400.0


@dataclass(frozen=True)
class Finding:
    target: str
    metric: str
    kind: str                       # "slow-fill" | "fast-rise"
    confidence: str                 # low | medium | high
    evidence: dict = field(default_factory=dict)
    days_to_full: float | None = None   # slow-fill
    ratio: float | None = None          # fast-rise: recent rate / baseline rate (None when the baseline is flat)
    ts: float = 0.0

    @property
    def fingerprint(self) -> str:
        return f"forecast:{self.kind}:{self.metric}:{self.target}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fingerprint"] = self.fingerprint
        return d


def daily_extreme(series: Series, pick=max) -> Series:
    """Collapse a series to one point per UTC day (max for used-space, min for free-space/available) to drop the sawtooth
    of log rotation, GC and nightly backups. Each day is stamped at its first sample's time bucket midpoint."""
    days: dict[int, list] = {}
    for ts, v in series:
        days.setdefault(int(ts // DAY), []).append(v)
    return [(d * DAY + DAY / 2, pick(vs)) for d, vs in sorted(days.items())]


def _clean(series: Series) -> Series:
    """Drop non-finite points and sort by time."""
    return sorted((float(t), float(v)) for t, v in series if v is not None and math.isfinite(v) and math.isfinite(t))


def _after_last_reset(pts: Series, drop: float) -> Series:
    """A counter reset / cleanup shows as a drop larger than `drop` between neighbours: keep only what follows the last one,
    so a fit never straddles it (a straddling fit would read a cleanup as 'flat')."""
    cut = 0
    for i in range(1, len(pts)):
        if pts[i - 1][1] - pts[i][1] > drop:
            cut = i
    return pts[cut:]


def _fit(pts: Series) -> tuple[float, float, float]:
    """Least squares y = a + b*t over (t seconds shifted to the first point). Returns (slope per second, intercept, r2)."""
    t0 = pts[0][0]
    xs = [t - t0 for t, _ in pts]
    ys = [v for _, v in pts]
    n = len(pts)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my, 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    sst = sum((y - my) ** 2 for y in ys)
    sse = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 if sst == 0 else max(0.0, 1.0 - sse / sst)
    return b, a, r2


def _confidence(r2: float) -> str:
    return "high" if r2 >= 0.95 else "medium" if r2 >= 0.85 else "low"


def slow_fill(series: Series, capacity: float, now: float, horizon_days: float = 14, min_points: int = 7,
              r2_floor: float = 0.7, window_days: float = 14, max_gap_frac: float = 0.3,
              reset_drop_frac: float = 0.2, target: str = "", metric: str = "") -> Finding | None:
    """Days until the fitted line reaches `capacity`, or None when there is nothing worth saying.

    None when: too few points in the window; the largest gap exceeds `max_gap_frac` of the window (the data does not cover
    it); the fit is poor (r2 < r2_floor); the slope is flat or negative; the series is already at/over capacity (that is
    an alert, not a forecast); or the projected crossing is further than `horizon_days` away.
    """
    lo = now - window_days * DAY
    pts = [(t, v) for t, v in _clean(series) if lo <= t <= now]
    pts = _after_last_reset(pts, reset_drop_frac * capacity)
    if len(pts) < min_points:
        return None
    gaps = [b[0] - a[0] for a, b in zip(pts, pts[1:])]
    # The leading and trailing edges count too: a series that stopped days ago must not forecast from stale data.
    if max(gaps + [pts[0][0] - lo, now - pts[-1][0]]) > max_gap_frac * window_days * DAY:
        return None
    slope, a, r2 = _fit(pts)
    if slope <= 0 or r2 < r2_floor:
        return None
    t0 = pts[0][0]
    current = a + slope * (now - t0)   # the fitted level now, not the last (possibly noisy) sample
    if pts[-1][1] >= capacity or current >= capacity:
        return None
    days = (capacity - current) / slope / DAY
    if days > horizon_days:
        return None
    return Finding(target=target, metric=metric, kind="slow-fill", days_to_full=round(days, 2),
                   confidence=_confidence(r2), ts=now,
                   evidence={"points": len(pts), "r2": round(r2, 3), "slope_per_day": slope * DAY,
                             "current": round(current, 6), "capacity": capacity, "window_days": window_days})


def _p95(vals: list) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, int(math.ceil(0.95 * len(s))) - 1)]


def creep(series: Series, now: float, week: float = 7 * DAY, ratio: float = 1.5, floor: float = 0.0, min_points: int = 72,
          target: str = "", metric: str = "") -> Finding | None:
    """This week's p95 against the previous week's. A finding when the latest is at least `ratio` times the earlier one AND above the
    absolute `floor` (so 0.1 ms becoming 0.2 ms is not news). Each week needs `min_points` samples (72 of 168 hourly)."""
    pts = _clean(series)
    cur = [v for t, v in pts if now - week < t <= now]
    prev = [v for t, v in pts if now - 2 * week < t <= now - week]
    if len(cur) < min_points or len(prev) < min_points:
        return None
    p_cur, p_prev = _p95(cur), _p95(prev)
    if p_cur < floor or p_prev <= 0 or p_cur / p_prev < ratio:
        return None
    r = p_cur / p_prev
    return Finding(target=target, metric=metric, kind="creep", ratio=round(r, 2), confidence="high" if r >= 2 * ratio else "medium", ts=now,
                   evidence={"p95_this_week": round(p_cur, 4), "p95_last_week": round(p_prev, 4), "points_this_week": len(cur),
                             "points_last_week": len(prev)})


def step_up(series: Series, now: float, window: float = 7 * DAY, min_delta: float = 1.0, min_points: int = 3,
            target: str = "", metric: str = "") -> Finding | None:
    """The series rose by at least `min_delta` inside the window (latest value minus the lowest value in it): a counter that moved."""
    pts = [(t, v) for t, v in _clean(series) if now - window <= t <= now]
    if len(pts) < min_points:
        return None
    low = min(v for _, v in pts)
    delta = pts[-1][1] - low
    if delta < min_delta:
        return None
    return Finding(target=target, metric=metric, kind="step-up", confidence="high", ts=now,
                   evidence={"delta": delta, "latest": pts[-1][1], "lowest_in_window": low, "window_days": window / DAY, "points": len(pts)})


def fast_rise(series: Series, now: float, window: float = 3600, baseline: float = DAY, factor: float = 3.0,
              floor: float = 0.0, min_points: int = 3, reset_drop: float = math.inf,
              target: str = "", metric: str = "") -> Finding | None:
    """Sudden growth: the rate over the last `window` seconds against the rate over the `baseline` seconds before it.

    `floor` is an absolute rate (units per hour) the recent rate must exceed, so a tiny series doubling is not a finding.
    A flat or falling baseline counts as zero, so any rise above the floor qualifies (ratio None). `reset_drop` ignores
    points before a drop larger than it (a rotation/cleanup) so a reset is not read as a rise.
    """
    pts = [(t, v) for t, v in _clean(series) if t <= now]
    recent = _after_last_reset([p for p in pts if p[0] >= now - window], reset_drop)
    base = _after_last_reset([p for p in pts if now - window - baseline <= p[0] < now - window], reset_drop)
    if len(recent) < min_points or len(base) < min_points:
        return None
    r_rate = (recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0]) * 3600
    b_rate = max(0.0, (base[-1][1] - base[0][1]) / (base[-1][0] - base[0][0]) * 3600)
    if r_rate <= 0 or r_rate < floor:
        return None
    ratio = r_rate / b_rate if b_rate > 0 else None
    if ratio is not None and ratio < factor:
        return None
    return Finding(target=target, metric=metric, kind="fast-rise", ratio=None if ratio is None else round(ratio, 2),
                   confidence="medium" if len(recent) >= 6 else "low", ts=now,
                   evidence={"recent_rate_per_hour": r_rate, "baseline_rate_per_hour": b_rate,
                             "recent_points": len(recent), "baseline_points": len(base), "window_s": window})


class Dedup:
    """Report a fingerprint at most once per `hours`, unless it got materially worse (ETA at most halved)."""

    def __init__(self, hours: float = 24.0):
        self.seconds = hours * 3600
        self._seen: dict[str, tuple[float, float | None]] = {}

    def allow(self, f: Finding, now: float) -> bool:
        prev = self._seen.get(f.fingerprint)
        if prev is not None:
            ts, days = prev
            worse = f.days_to_full is not None and days is not None and f.days_to_full <= days / 2
            if now - ts < self.seconds and not worse:
                return False
        self._seen[f.fingerprint] = (now, f.days_to_full)
        return True


@dataclass(frozen=True)
class Target:
    """One forecastable signal. `needs_data_source` targets have no series in the repo today: they document the gap and are
    skipped by the runner until a source (and a promql) exists."""
    name: str
    metric: str
    promql: str = ""
    detectors: tuple = ("slow-fill", "fast-rise")
    capacity: float | None = None       # the existing alert threshold, in the series' unit; None = no slow-fill
    floor_per_hour: float = 0.0         # fast-rise absolute floor
    daily: str | None = None            # "max" | "min": collapse to daily extrema before slow-fill
    needs_data_source: bool = False
    zabbix: tuple | None = None         # ("fs",) | ("memory",) | ("pve", (storage, ...)) | ("await",) | ("smart", metric): forecast_zabbix.ZabbixSource
    note: str = ""
    fast_window: float = 6 * 3600.0     # fast-rise window; the sources are hourly, so 6 h = 6 points (a 1 h window could never fire)
    creep_ratio: float = 1.5            # creep: this week's p95 / last week's
    creep_floor: float = 0.0            # creep: ignore a p95 below this absolute value (units of the series)
    step_min: float = 1.0               # step-up: the rise inside the window that counts


# Real series only. The repo's vmagent scrapes kubelet, cAdvisor and kube-state-metrics (no node_exporter), and VictoriaLogs
# self-metrics (vl_data_size_bytes is on the 04-victoria-self dashboard). The fleet itself is watched by Zabbix, which the
# Toolbelt cannot read yet.
DEFAULT_TARGETS = (
    Target("k8s-pvc-used-ratio", "kubelet_volume_stats_used_bytes/capacity_bytes",
           promql="kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes", capacity=0.85,
           floor_per_hour=0.005, daily="max",
           note="CSI/NFS PVCs only; local-path volumes are not reported by kubelet. 0.85 is an assumed alert threshold, tune in shadow."),
    Target("victorialogs-data-size", "vl_data_size_bytes", promql="vl_data_size_bytes", detectors=("fast-rise",),
           floor_per_hour=1e9, note="No capacity known here (retention-bound), so fast-rise only; floor 1 GB/h is an initial guess."),
    # The fleet, from Zabbix (31 days of history, 365 days of hourly trends). Thresholds are the alert levels these would cross; the
    # Zabbix template defaults are 90 % (warn) for filesystems, the rest are the plan's values. Tune through a reviewed PR.
    Target("fleet-fs-used", "vfs.fs.dependent.size[*,pused]", zabbix=("fs",), capacity=0.90, floor_per_hour=0.01, daily="max",
           note="Every monitored filesystem (/, /boot, /data, LXC rootfs on the PVE hosts). The daily maximum removes log-rotation sawtooth."),
    Target("pve-storage-used", "proxmox.node.disk/maxdisk", zabbix=("pve", ("local-lvm", "local")), capacity=0.85,
           floor_per_hour=0.01, daily="max",
           note="The node-local pools: local-lvm is the thin pool. It fills while every guest still looks fine, which is why it is its own series; "
                "a fast rise here (a runaway guest disk) is worth a note."),
    # Split from the above on 2026-10-10: the operator called the PBS datastore's fast-rise note noise (three copies of one event, one per node, "
    # during the nightly backup), so the shared storages get ONE series each and the slow-fill detector only. A backup writing a night's worth of
    # chunks is how a datastore is supposed to behave; a datastore that is steadily filling is the signal.
    Target("pve-shared-storage-used", "proxmox.node.disk/maxdisk", zabbix=("pve", ("pbs-backup", "munin-nfs")), detectors=("slow-fill",), capacity=0.85,
           daily="max",
           note="pbs-backup is the PBS datastore as PVE sees it (the plan's PBS-capacity signal), munin-nfs the NAS share; every node reports the same one, so "
                "the series is labelled shared/<storage>. Slow-fill only: the nightly backup is a legitimate fast rise."),
    # Disk health on the hypervisors (10h1 leftovers, 2026-10-04). Await items exist on every host (hourly trends for 365 days); the SMART items
    # come from the "SMART by Zabbix agent 2" template linked on the PVE hosts, so their series start the day it was linked.
    Target("nvme-latency-creep", "vfs.dev.await", zabbix=("await", "nvme"), detectors=("creep",), creep_ratio=1.5, creep_floor=1.0,
           note="NVMe devices only (the hypervisors; guest virtual disks swing 2-3x week to week and would be noise: checked live 2026-10-04). "
                "Per device and direction, p95 of hourly average await (ms): this week against last. Urd's DRAM-less drive already runs several times "
                "its siblings; this watches for it getting WORSE, not for the absolute level. Floor 1 ms keeps a quiet drive from alerting on noise."),
    Target("nvme-media-errors", "smart.disk.media_errors", zabbix=("smart", "media_errors"), detectors=("step-up",), step_min=1.0,
           note="Any new media or data-integrity error on an NVMe drive in the last 7 days. A counter that moved is news by itself."),
    Target("nvme-wear", "smart.disk.percentage_used", zabbix=("smart", "percentage_used"), detectors=("step-up",), step_min=3.0,
           note="NVMe 'Percentage Used' (rated endurance consumed, %): a rise of 3 points or more inside a week is abnormal wear (normal is a "
                "point or two a YEAR; Urd's NM790 read 7 % on 2026-10-04)."),
    Target("memory-used", "vm.memory.size[pavailable]", zabbix=("memory",), detectors=("slow-fill",), capacity=0.90, daily="max",
           note="1 - available memory, daily high. Memory is not linear: this catches a steady creep, not a leak that fills it in hours."),
)


def validate_targets(targets) -> list[str]:
    """Config sanity: a list of problems, empty when fine."""
    errs, names = [], set()
    for t in targets:
        if t.name in names:
            errs.append(f"{t.name}: duplicate name")
        names.add(t.name)
        if not set(t.detectors) <= {"slow-fill", "fast-rise", "creep", "step-up"} or not t.detectors:
            errs.append(f"{t.name}: bad detectors {t.detectors}")
        if not t.needs_data_source and not t.promql and not t.zabbix:
            errs.append(f"{t.name}: no promql, no zabbix selector and not marked needs_data_source")
        if t.needs_data_source and t.promql:
            errs.append(f"{t.name}: marked needs_data_source but has a promql")
        if t.zabbix and (t.promql or t.needs_data_source):
            errs.append(f"{t.name}: a zabbix target has no promql and is not needs_data_source")
        if t.zabbix and (t.zabbix[0] not in ("fs", "memory", "pve", "await", "smart") or (t.zabbix[0] in ("pve", "smart") and len(t.zabbix) < 2)
                         or (t.zabbix[0] == "pve" and not t.zabbix[1])):
            errs.append(f"{t.name}: bad zabbix selector {t.zabbix}")
        if "slow-fill" in t.detectors and not t.needs_data_source and t.capacity is None:
            errs.append(f"{t.name}: slow-fill needs a capacity")
        if t.daily not in (None, "max", "min"):
            errs.append(f"{t.name}: bad daily {t.daily}")
    return errs


def evaluate(t: Target, label: str, series: Series, now: float) -> list[Finding]:
    """Run a target's detectors over one series (one label set, e.g. one PVC)."""
    out = []
    if "slow-fill" in t.detectors and t.capacity is not None:
        s = daily_extreme(series, max if t.daily == "max" else min) if t.daily else series
        f = slow_fill(s, t.capacity, now, target=label, metric=t.metric, min_points=7 if t.daily else 24)
        if f:
            out.append(f)
    if "fast-rise" in t.detectors:
        f = fast_rise(series, now, window=t.fast_window, floor=t.floor_per_hour, target=label, metric=t.metric)
        if f:
            out.append(f)
    if "creep" in t.detectors:
        f = creep(series, now, ratio=t.creep_ratio, floor=t.creep_floor, target=label, metric=t.metric)
        if f:
            out.append(f)
    if "step-up" in t.detectors:
        f = step_up(series, now, min_delta=t.step_min, target=label, metric=t.metric)
        if f:
            out.append(f)
    return out


def run_once(query, now: float, log_path: str | Path, targets=DEFAULT_TARGETS, dedup: Dedup | None = None,
             lookback_days: float = 15, step: str = "1h", sources: dict | None = None, stats: dict | None = None,
             current_path: str | Path | None = None) -> list[Finding]:
    """One pass. `query(promql, start, end, step)` returns [(label, series), ...] for VictoriaMetrics targets; `sources["zabbix"]`
    is `fn(target, start, end)` for Zabbix targets (forecast_zabbix.ZabbixSource.query). New findings (after de-duplication) are
    appended to `log_path` as JSONL and returned. Every finding that holds RIGHT NOW goes to `current_path` (JSON, replaced
    atomically) so a reader can tell "still open" from "gone". A failing query skips that target and says so in `stats`
    ({target: {"series": n} | {"error": "..."}}): one bad source never hides the others, and is never silent either."""
    dedup = dedup or Dedup()
    stats = {} if stats is None else stats
    found, active = [], []
    start, end = now - lookback_days * DAY, now
    for t in targets:
        if t.needs_data_source:
            stats[t.name] = {"skipped": "no data source yet"}
            continue
        try:
            if t.zabbix:
                src = (sources or {}).get("zabbix")
                if src is None:
                    stats[t.name] = {"error": "no zabbix source configured"}
                    continue
                rows = src(t, start, end)
            else:
                rows = query(t.promql, start, end, step)
        except Exception as e:  # noqa: BLE001 - one target must not hide the rest
            stats[t.name] = {"error": f"{type(e).__name__}: {str(e)[:100]}"}
            continue
        stats[t.name] = {"series": len(rows), "points": sum(len(s) for _, s in rows)}
        for label, series in rows:
            for f in evaluate(t, label, series, now):
                active.append(f)
                if dedup.allow(f, now):
                    found.append(f)
    if found:
        p = Path(log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            for f in found:
                fh.write(json.dumps(f.to_dict(), sort_keys=True) + "\n")
    if current_path:
        c = Path(current_path)
        c.parent.mkdir(parents=True, exist_ok=True)
        tmp = c.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ts": now, "findings": [f.to_dict() for f in active], "stats": stats}, sort_keys=True))
        tmp.replace(c)
    return found


def main(argv=None, query=None) -> int:
    """`python3 forecast.py --once --log PATH`: one shadow pass using a query function passed in by the caller. Not started
    by anything; with no query function it refuses rather than guessing a data source."""
    import argparse
    import time
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", required=True)
    ap.add_argument("--log", default="forecast-shadow.jsonl")
    a = ap.parse_args(argv)
    if query is None:
        print("no query function supplied (call forecast.main(query=...)); nothing was run")
        return 2
    n = len(run_once(query, time.time(), a.log))
    print(f"{n} new finding(s) appended to {a.log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
