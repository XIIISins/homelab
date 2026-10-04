"""The forecast card renders the 10h1 disk-health kinds (latency creep, SMART step-up) with their own wording, never as a fill date."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("bot", "toolbelt", "tools"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import fcast  # noqa: E402

NOW = 1_800_000_000


def fc(kind, evidence, ratio=None, metric="vfs.dev.await", target="urd:nvme0n1:read"):
    return {"id": 9, "kind": kind, "metric": metric, "target": target, "state": "open", "confidence": "medium", "days_to_full": None, "ratio": ratio,
            "first_seen": NOW - 3600, "last_seen": NOW, "eta_at": None, "label": None, "evidence": {"evidence": evidence, "ratio": ratio}}


class Kinds(unittest.TestCase):
    def test_a_creep_card_names_both_weeks_and_says_it_is_not_a_fill(self):
        c = fcast.card(fc("creep", {"p95_this_week": 2.5, "p95_last_week": 1.0}, ratio=2.5))
        self.assertIn("2.5x last week", c["description"])
        self.assertIn("1.0 ms", c["description"])
        fields = dict((n, v) for n, v, _ in c["fields"])
        self.assertEqual(fields["Signal"], "Disk latency")
        self.assertIn("trend change", fields["Estimated"])

    def test_a_step_up_card_says_how_much_it_rose(self):
        c = fcast.card(fc("step-up", {"delta": 2.0, "latest": 2.0, "window_days": 7.0}, metric="smart.disk.media_errors", target="urd:nvme0"))
        self.assertIn("Rose by 2", c["description"])
        self.assertEqual(dict((n, v) for n, v, _ in c["fields"])["Signal"], "NVMe media errors")

    def test_a_step_up_with_odd_evidence_still_renders(self):
        self.assertIn("counter", fcast.card(fc("step-up", {}, metric="smart.disk.percentage_used"))["description"])


if __name__ == "__main__":
    unittest.main()
