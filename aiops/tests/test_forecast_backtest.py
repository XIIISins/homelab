"""Phase 10h1 backtest: replay a known event against the detectors exactly as the forecast job runs them (`forecast.evaluate` with the
real `fleet-fs-used` target) and check how far ahead of the alert the first note would have appeared.

The event is the etcd raft-drop syslog flood on a control-plane node (a 10d replay scenario): a root filesystem that sat flat for days
began filling about 4 points an hour and would have crossed the 80 % filesystem alert about ten hours later. The plan asks for a
fast-rise note at least a few hours ahead of that alert; a quiet filesystem and ordinary log-rotation sawtooth must stay silent."""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))

import forecast as fc  # noqa: E402

HOUR = 3600.0
DAY = fc.DAY
T0 = 1_800_000_000.0  # the flood starts here
TARGET = next(t for t in fc.DEFAULT_TARGETS if t.name == "fleet-fs-used")
ALERT = 0.80


def quiet(days: float, level: float = 0.40, seed: int = 3):
    """Pre-flood history: a flat root filesystem sampled every 10 minutes, a hair of noise."""
    rnd = random.Random(seed)
    n = int(days * DAY / 600)
    return [(T0 - days * DAY + i * 600, level + rnd.uniform(-0.002, 0.002)) for i in range(n)]


def flood(hours: float, per_hour: float = 0.04, level: float = 0.40):
    return [(T0 + i * 600, level + per_hour * (i * 600) / HOUR) for i in range(int(hours * 6) + 1)]


def first_note_hours_before_alert(series):
    """Walk 'now' forward in 10-minute steps the way the hourly job would see it; the lead time of the first finding."""
    crossing = next(t for t, v in series if v >= ALERT)
    for t, _ in series:
        if t < T0:
            continue
        seen = [p for p in series if p[0] <= t]
        if fc.evaluate(TARGET, "cp-node:/", seen, t):
            return (crossing - t) / HOUR
        if t >= crossing:
            return None
    return None


class SyslogFlood(unittest.TestCase):
    def test_the_flood_is_noticed_hours_before_the_alert(self):
        lead = first_note_hours_before_alert(quiet(5) + flood(12))
        self.assertIsNotNone(lead, "the detectors never fired before the 80 % alert")
        self.assertGreaterEqual(lead, 3.0, f"first note only {lead:.1f} h ahead of the alert")

    def test_a_slower_leak_is_still_caught_before_the_alert(self):
        # 1.5 points an hour: ~27 h from 40 % to 80 %. Caught by either detector, well ahead.
        lead = first_note_hours_before_alert(quiet(5) + flood(30, per_hour=0.015))
        self.assertIsNotNone(lead)
        self.assertGreaterEqual(lead, 6.0)

    def test_a_quiet_filesystem_and_log_rotation_sawtooth_stay_silent(self):
        end = T0 + 5 * DAY
        calm = quiet(5) + [(T0 + i * 600, 0.40 + ((i * 600) % DAY) / DAY * 0.03) for i in range(int(5 * DAY / 600))]  # rises 3 pts a day, resets nightly
        for now in (T0 + 2 * DAY, T0 + 3.5 * DAY, end - 600):
            self.assertEqual(fc.evaluate(TARGET, "cp-node:/", [p for p in calm if p[0] <= now], now), [], now)


if __name__ == "__main__":
    unittest.main()
