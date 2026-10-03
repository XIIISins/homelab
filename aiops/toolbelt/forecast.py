"""Forecasting detectors (Phase 10h1): slow-fill and fast-rise, as pure functions, plus a thin shadow-mode runner.

Two detectors, because homelab series are not smooth (docs/operations/10h-predictive-change.md):
  slow_fill  a least-squares line over a window projects the days until a series crosses its capacity/threshold.
  fast_rise  the recent rate against the prior baseline rate, for step changes a 14-day line cannot see (a log flood).
Neither predicts a sudden fault; those stay alerts. Forecasts are notifications, never pages.

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
    note: str = ""


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
    Target("fleet-disk-used", "vfs.fs.size[*,pused]", needs_data_source=True,
           note="Zabbix agent items on the fleet; needs a Toolbelt Zabbix history/trends read tool and confirmed 30+ day retention."),
    Target("pve-thin-pool", "local-lvm thin pool usage", needs_data_source=True,
           note="Zabbix Proxmox template storage items; confirm the per-node thin-pool item exists and is trended."),
    Target("memory-available", "vm.memory.size[available]", needs_data_source=True, detectors=("slow-fill",),
           note="Zabbix; memory is not linear: use the daily-minimum trend plus an allocation ledger, not a fill date."),
    Target("pbs-datastore-used", "PBS status/datastore-usage", needs_data_source=True,
           note="No automated source: needs an audit-only PBS API token. Fit the post-GC daily minimum."),
)


def validate_targets(targets) -> list[str]:
    """Config sanity: a list of problems, empty when fine."""
    errs, names = [], set()
    for t in targets:
        if t.name in names:
            errs.append(f"{t.name}: duplicate name")
        names.add(t.name)
        if not set(t.detectors) <= {"slow-fill", "fast-rise"} or not t.detectors:
            errs.append(f"{t.name}: bad detectors {t.detectors}")
        if not t.needs_data_source and not t.promql:
            errs.append(f"{t.name}: no promql and not marked needs_data_source")
        if t.needs_data_source and t.promql:
            errs.append(f"{t.name}: marked needs_data_source but has a promql")
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
        f = fast_rise(series, now, floor=t.floor_per_hour, target=label, metric=t.metric)
        if f:
            out.append(f)
    return out


def run_once(query, now: float, log_path: str | Path, targets=DEFAULT_TARGETS, dedup: Dedup | None = None,
             lookback_days: float = 15, step: str = "1h") -> list[Finding]:
    """One shadow pass. `query(promql, start, end, step)` returns [(label, series), ...] and is supplied by the caller
    (production: the Toolbelt's metrics.range; tests: a fake). New findings are appended to `log_path` as JSONL and returned.
    A failing query skips that target; one bad target never hides the others."""
    dedup = dedup or Dedup()
    found = []
    for t in targets:
        if t.needs_data_source:
            continue
        try:
            rows = query(t.promql, now - lookback_days * DAY, now, step)
        except Exception:
            continue
        for label, series in rows:
            for f in evaluate(t, label, series, now):
                if dedup.allow(f, now):
                    found.append(f)
    if found:
        p = Path(log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            for f in found:
                fh.write(json.dumps(f.to_dict(), sort_keys=True) + "\n")
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
