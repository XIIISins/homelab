"""Phase 10h2 class `capacity`: a forecast card's "Draft fix PR" files a change request for the PR author, only where the fix is a value in
the repository (aiops/toolbelt/capacity_draft.py, core.draft_capacity, the /forecasts/<id>/draft route)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests", "bot"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import capacity_draft as cd  # noqa: E402
import fcast  # noqa: E402
import forecast_store as fst  # noqa: E402
import test_change_requests as tcr  # noqa: E402
import test_forecast_store as tfs  # noqa: E402


class Request(unittest.TestCase):
    FC = {"id": 7, "metric": "vfs.fs.dependent.size[*,pused]", "target": "hugin:/", "kind": "slow-fill", "days_to_full": 9.6,
          "evidence": {"evidence": {"current": 0.8, "capacity": 0.9, "slope_per_day": 0.012, "r2": 0.97, "points": 15}}}

    def test_the_bots_button_list_and_the_toolbelts_remedy_table_agree(self):
        self.assertEqual(set(fcast.REMEDY_METRICS), set(cd.REMEDIES))

    def test_the_request_names_the_finding_the_file_and_the_bounds(self):
        title, body = cd.capacity_request(self.FC)
        self.assertEqual(title, "Capacity: hugin:/")
        for needle in ("Forecast #7", "hugin:/", "disk { size = N }", "never more than double", "terraform apply", "change nothing and say why"):
            self.assertIn(needle, body)
        self.assertLess(len(body), 4000)

    def test_only_metrics_with_a_fix_in_the_repository_have_a_remedy(self):
        self.assertTrue(cd.has_remedy("vm.memory.size[pavailable]"))
        for m in ("proxmox.node.disk/maxdisk", "kubelet_volume_stats_used_bytes/capacity_bytes", "vl_data_size_bytes"):
            self.assertFalse(cd.has_remedy(m))


class Route(unittest.TestCase):
    def setUp(self):
        self.r = tcr.Rig()
        tb = self.r.tb
        tb.fc = fst.Forecasts(tb.db, tb._lock, tb.clock, tb.audit)
        tb.fc.sync({"ts": self.r.clock.t, "findings": [tfs.finding(days=4)], "stats": {}})
        self.fc = tb.fc.get(1)

    def tearDown(self):
        self.r.close()

    def draft(self, by=tcr.OP, fid=1):
        return self.r.call(tcr.T_APPR, "POST", f"/forecasts/{fid}/draft", {"by": by})

    def test_an_operator_files_a_pending_capacity_request_once(self):
        if not cd.has_remedy(self.fc["metric"]):
            self.skipTest("fixture metric has no remedy")
        st, cr = self.draft()
        self.assertEqual((st, cr["class"], cr["source"], cr["source_ref"], cr["state"]), (200, "capacity", "forecast", "forecast-1", "pending"))
        self.assertEqual(self.draft()[0], 409)                      # one active request per finding

    def test_a_stranger_an_unknown_forecast_and_a_metric_without_a_remedy_are_refused(self):
        self.assertEqual(self.draft(by="999")[0], 403)
        self.assertEqual(self.draft(fid=99)[0], 404)
        self.r.tb.db.execute("UPDATE forecasts SET metric=? WHERE id=1", ("proxmox.node.disk/maxdisk",))
        st, out = self.draft()
        self.assertEqual(st, 409)
        self.assertIn("outside", out["error"])


if __name__ == "__main__":
    unittest.main()
