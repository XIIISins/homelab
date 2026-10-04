"""Phase 10h1: forecasting detectors (aiops/toolbelt/forecast.py), pure functions, no network.

The rules under test: slow-fill needs enough points, coverage and a good fit and says nothing for flat, falling or
already-full series; a reset never produces a fill date; fast-rise needs a real rate step above an absolute floor;
a finding is not repeated within the dedup window; shadow mode only appends to a local file.
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))

import forecast as fc  # noqa: E402

DAY = fc.DAY
NOW = 1_800_000_000.0


def linear(start_val, per_day, days=14, step_h=6, noise=0.0, seed=1):
    rnd = random.Random(seed)
    n = int(days * 24 / step_h)
    return [(NOW - days * DAY + i * step_h * 3600,
             start_val + per_day * (i * step_h / 24) + rnd.uniform(-noise, noise)) for i in range(n + 1)]


class SlowFillTests(unittest.TestCase):
    def test_clean_linear_fill(self):
        # 0.60 -> 0.74 over 14 days at 0.01/day; 0.11 left to the 0.85 threshold
        f = fc.slow_fill(linear(0.60, 0.01), 0.85, NOW, target="pvc-a", metric="m")
        self.assertIsNotNone(f)
        self.assertEqual(f.kind, "slow-fill")
        self.assertAlmostEqual(f.days_to_full, 11.0, delta=0.2)  # 0.74 now, 0.11 left at 0.01/day
        self.assertEqual(f.confidence, "high")
        self.assertEqual(f.fingerprint, "forecast:slow-fill:m:pvc-a")

    def test_noisy_fill_still_found(self):
        f = fc.slow_fill(linear(0.60, 0.01, noise=0.01), 0.85, NOW)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f.days_to_full, 11.0, delta=3.0)

    def test_pure_noise_is_not_a_trend(self):
        s = [(t, 0.5 + v) for t, v in linear(0.0, 0.0, noise=0.05)]
        self.assertIsNone(fc.slow_fill(s, 0.85, NOW))

    def test_flat(self):
        self.assertIsNone(fc.slow_fill(linear(0.7, 0.0), 0.85, NOW))

    def test_decreasing(self):
        self.assertIsNone(fc.slow_fill(linear(0.8, -0.01), 0.95, NOW))

    def test_too_few_points(self):
        s = linear(0.60, 0.01, step_h=48)  # 8 points ok at min 7
        self.assertIsNotNone(fc.slow_fill(s, 0.85, NOW))
        self.assertIsNone(fc.slow_fill(s[:5], 0.85, NOW))

    def test_beyond_horizon(self):
        self.assertIsNone(fc.slow_fill(linear(0.30, 0.005), 0.95, NOW, horizon_days=14))

    def test_already_full(self):
        self.assertIsNone(fc.slow_fill(linear(0.80, 0.01), 0.85, NOW))

    def test_gap_in_the_middle_is_refused(self):
        s = [p for p in linear(0.60, 0.01) if not (NOW - 11 * DAY < p[0] < NOW - 4 * DAY)]
        self.assertIsNone(fc.slow_fill(s, 0.85, NOW))
        self.assertIsNotNone(fc.slow_fill(s, 0.85, NOW, max_gap_frac=0.6))

    def test_stale_series_is_refused(self):
        s = [p for p in linear(0.60, 0.01) if p[0] < NOW - 6 * DAY]  # stopped reporting 6 days ago
        self.assertIsNone(fc.slow_fill(s, 0.85, NOW))

    def test_counter_reset_does_not_forecast_across_it(self):
        s = linear(0.60, 0.01)
        cut = len(s) // 2
        reset = s[:cut] + [(t, v - 0.35) for t, v in s[cut:]]   # a cleanup dropped usage 35 points
        self.assertIsNone(fc.slow_fill(reset, 0.85, NOW))       # too little left after the reset to say anything

    def test_reset_then_long_regrowth_uses_only_the_new_segment(self):
        old = [(NOW - 14 * DAY + i * 6 * 3600, 0.9) for i in range(8)]
        new = [(NOW - 12 * DAY + i * 6 * 3600, 0.10 + 0.05 * (i * 6 / 24)) for i in range(49)]  # 0.70 now
        f = fc.slow_fill(old + new, 0.85, NOW, max_gap_frac=0.5)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f.days_to_full, 3.0, delta=0.2)

    def test_non_finite_points_ignored(self):
        s = linear(0.60, 0.01)
        s[3] = (s[3][0], float("nan"))
        self.assertIsNotNone(fc.slow_fill(s, 0.85, NOW))

    def test_daily_extreme_removes_sawtooth(self):
        saw = []
        for d in range(14):
            for h in range(24):
                base = 0.60 + 0.01 * d
                saw.append((NOW - 14 * DAY + d * DAY + h * 3600, base - (0.08 if h % 24 == 3 else 0.0)))
        daily = fc.daily_extreme(saw, max)
        self.assertGreaterEqual(len(daily), 14)
        self.assertIsNotNone(fc.slow_fill(daily, 0.85, NOW))


class FastRiseTests(unittest.TestCase):
    def _series(self, base_rate, recent_rate):
        base = [(NOW - 25 * 3600 + i * 600, 10 + base_rate * (i * 600 / 3600)) for i in range(138)]  # to NOW-1h-ish
        end = base[-1]
        recent = [(NOW - 3600 + i * 600, end[1] + recent_rate * (i * 600 / 3600)) for i in range(7)]
        return [p for p in base if p[0] < NOW - 3600] + recent

    def test_true_positive(self):
        f = fc.fast_rise(self._series(1.0, 10.0), NOW, factor=3.0, floor=2.0, target="vm", metric="m")
        self.assertIsNotNone(f)
        self.assertEqual(f.kind, "fast-rise")
        self.assertGreaterEqual(f.ratio, 3.0)

    def test_steady_growth_is_not_a_rise(self):
        self.assertIsNone(fc.fast_rise(self._series(5.0, 6.0), NOW, factor=3.0, floor=1.0))

    def test_below_floor_is_ignored(self):
        self.assertIsNone(fc.fast_rise(self._series(0.01, 0.5), NOW, factor=3.0, floor=2.0))

    def test_flat_baseline_then_rise(self):
        f = fc.fast_rise(self._series(0.0, 10.0), NOW, factor=3.0, floor=2.0)
        self.assertIsNotNone(f)
        self.assertIsNone(f.ratio)

    def test_falling_is_ignored(self):
        self.assertIsNone(fc.fast_rise(self._series(1.0, -10.0), NOW, floor=0.0))

    def test_cleanup_drop_in_recent_window_is_not_a_rise(self):
        s = self._series(1.0, 0.0)
        s = [(t, v - 500 if t > NOW - 1800 else v) for t, v in s]
        self.assertIsNone(fc.fast_rise(s, NOW, floor=2.0, reset_drop=100))

    def test_not_enough_data(self):
        self.assertIsNone(fc.fast_rise(self._series(1.0, 10.0)[-4:], NOW))


class DedupTests(unittest.TestCase):
    def f(self, days):
        return fc.Finding("t", "m", "slow-fill", "high", days_to_full=days, ts=NOW)

    def test_window(self):
        d = fc.Dedup(hours=24)
        self.assertTrue(d.allow(self.f(10), NOW))
        self.assertFalse(d.allow(self.f(10), NOW + 3600))
        self.assertTrue(d.allow(self.f(10), NOW + 25 * 3600))

    def test_eta_halving_escalates_inside_the_window(self):
        d = fc.Dedup(hours=24)
        d.allow(self.f(10), NOW)
        self.assertFalse(d.allow(self.f(7), NOW + 60))
        self.assertTrue(d.allow(self.f(4), NOW + 120))

    def test_distinct_targets_independent(self):
        d = fc.Dedup()
        a, b = fc.Finding("a", "m", "fast-rise", "low", ts=NOW), fc.Finding("b", "m", "fast-rise", "low", ts=NOW)
        self.assertTrue(d.allow(a, NOW))
        self.assertTrue(d.allow(b, NOW))


class ConfigTests(unittest.TestCase):
    def test_defaults_are_sane(self):
        self.assertEqual(fc.validate_targets(fc.DEFAULT_TARGETS), [])

    def test_no_invented_metrics(self):
        for t in fc.DEFAULT_TARGETS:
            if t.needs_data_source:
                self.assertEqual(t.promql, "", t.name)
                self.assertTrue(t.note, t.name)  # a gap must say what is missing
            else:
                self.assertTrue(t.promql or t.zabbix, t.name)  # every live target reads a real source
            if t.zabbix:
                self.assertIn(t.zabbix[0], ("fs", "memory", "pve"), t.name)
                self.assertTrue(t.note, t.name)
        self.assertTrue({"fleet-fs-used", "pve-storage-used", "memory-used"} <= {t.name for t in fc.DEFAULT_TARGETS})
        self.assertFalse([t.name for t in fc.DEFAULT_TARGETS if t.needs_data_source])  # the 2026-10-04 gap is closed

    def test_validator_catches_problems(self):
        bad = [fc.Target("x", "m"), fc.Target("x", "m", promql="q", detectors=("bogus",)),
               fc.Target("y", "m", promql="q"), fc.Target("z", "m", promql="q", needs_data_source=True)]
        errs = "\n".join(fc.validate_targets(bad))
        for needle in ("duplicate", "bad detectors", "no promql", "needs a capacity", "has a promql"):
            self.assertIn(needle, errs)


class ShadowRunTests(unittest.TestCase):
    def test_run_once_appends_jsonl_and_dedups(self):
        t = fc.Target("pvc", "m", promql="q", capacity=0.85, daily="max", floor_per_hour=10)
        # 6-hourly data -> 4 points a day -> daily maxima, 15 days
        series = linear(0.60, 0.01, days=15)

        def query(promql, start, end, step):
            self.assertEqual(promql, "q")
            return [("ns/data-0", series)]

        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "sub" / "shadow.jsonl"
            dd = fc.Dedup()
            got = fc.run_once(query, NOW, log, targets=(t,), dedup=dd)
            self.assertEqual(len(got), 1)
            rows = [json.loads(x) for x in log.read_text().splitlines()]
            self.assertEqual(rows[0]["target"], "ns/data-0")
            self.assertEqual(rows[0]["fingerprint"], "forecast:slow-fill:m:ns/data-0")
            self.assertEqual(fc.run_once(query, NOW + 60, log, targets=(t,), dedup=dd), [])
            self.assertEqual(len(log.read_text().splitlines()), 1)

    def test_failing_query_and_data_gaps_are_skipped(self):
        ok = fc.Target("a", "m", promql="q", capacity=0.85)
        gap = fc.Target("b", "m2", needs_data_source=True, note="n")

        def query(*a):
            raise RuntimeError("down")

        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "s.jsonl"
            self.assertEqual(fc.run_once(query, NOW, log, targets=(ok, gap)), [])
            self.assertFalse(log.exists())

    def test_cli_refuses_without_a_query_function(self):
        self.assertEqual(fc.main(["--once", "--log", "/nonexistent/never-written"]), 2)


if __name__ == "__main__":
    unittest.main()
