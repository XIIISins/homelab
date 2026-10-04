"""Phase 10h1: forecast findings as rows the operator can act on (aiops/toolbelt/forecast_store.py) and the Toolbelt's ingest and routes."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import forecast_store as fs  # noqa: E402
import test_change_requests as tcr  # noqa: E402

DAY = 86400


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def finding(target="hugin:/", days=10.0, kind="slow-fill", metric="vfs.fs.dependent.size[*,pused]", conf="high"):
    return {"target": target, "metric": metric, "kind": kind, "confidence": conf, "days_to_full": days, "ratio": None, "ts": 0,
            "fingerprint": f"forecast:{kind}:{metric}:{target}", "evidence": {"slope_per_day": 0.01, "current": 0.8, "capacity": 0.9, "r2": 0.97, "points": 15}}


class Store(unittest.TestCase):
    def setUp(self):
        db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        db.row_factory = sqlite3.Row
        self.clock, self.audit = Clock(), []
        self.s = fs.Forecasts(db, threading.RLock(), self.clock, lambda event, **kw: self.audit.append({"event": event, **kw}))

    def cur(self, *findings, age=0):
        return {"ts": self.clock.t - age, "findings": list(findings), "stats": {}}

    def kinds(self):
        return [e["kind"] for e in self.s.feed(0)["events"]]

    def test_a_new_finding_is_created_once_with_its_eta(self):
        out = self.s.sync(self.cur(finding(days=10)))
        self.assertEqual((out["created"], out["seen"]), (1, 1))
        v = self.s.list()[0]
        self.assertEqual((v["state"], v["kind"], v["target"], v["confidence"]), ("open", "slow-fill", "hugin:/", "high"))
        self.assertEqual(v["eta_at"], int(self.clock.t + 10 * DAY))
        self.assertEqual(v["evidence"]["evidence"]["capacity"], 0.9)
        self.clock.t += 3600
        self.assertEqual(self.s.sync(self.cur(finding(days=9.9)))["created"], 0)  # the same finding is not announced again
        self.assertEqual(self.kinds(), ["created"])

    def test_the_eta_halving_escalates_once_and_a_second_halving_escalates_again(self):
        self.s.sync(self.cur(finding(days=10)))
        self.clock.t += DAY
        self.assertEqual(self.s.sync(self.cur(finding(days=6)))["escalated"], 0)   # worse, not twice as bad
        self.clock.t += DAY
        self.assertEqual(self.s.sync(self.cur(finding(days=4.9)))["escalated"], 1)  # at most half of the 10 days announced
        self.clock.t += DAY
        self.assertEqual(self.s.sync(self.cur(finding(days=3.0)))["escalated"], 0)  # half of 4.9 is 2.45
        self.clock.t += DAY
        self.assertEqual(self.s.sync(self.cur(finding(days=2.4)))["escalated"], 1)
        self.assertEqual(self.kinds(), ["created", "escalated", "escalated"])

    def test_still_open_after_a_week_is_reposted_unless_it_was_labelled_noise(self):
        self.s.sync(self.cur(finding(days=12), finding(target="urd/pbs-backup", metric="m2", days=12)))
        self.s.label(2, "noise", "op")
        self.clock.t += 7 * DAY + 60
        out = self.s.sync(self.cur(finding(days=11.9), finding(target="urd/pbs-backup", metric="m2", days=11.9)))
        self.assertEqual(out["reposted"], 1)
        reposted = [e["forecast"]["target"] for e in self.s.feed(0)["events"] if e["kind"] == "reposted"]
        self.assertEqual(reposted, ["hugin:/"])  # the noisy one stays quiet

    def test_a_finding_resolves_after_two_passes_without_it_and_one_miss_is_forgiven(self):
        self.s.sync(self.cur(finding()))
        self.clock.t += 3600
        self.s.sync(self.cur())                                               # one miss
        self.assertEqual(self.s.list()[0]["missing"], 1)
        self.clock.t += 3600
        self.s.sync(self.cur(finding(days=9)))                                # it is back: the miss counter resets
        self.assertEqual(self.s.list()[0]["missing"], 0)
        self.clock.t += 3600
        self.s.sync(self.cur())
        self.clock.t += 3600
        self.assertEqual(self.s.sync(self.cur())["resolved"], 1)
        self.assertEqual((self.s.list(), self.kinds()), ([], ["created", "resolved"]))

    def test_a_returning_finding_is_a_new_occurrence_with_a_clean_label(self):
        self.s.sync(self.cur(finding()))
        self.s.label(1, "noise", "op")
        for _ in range(2):
            self.clock.t += 3600
            self.s.sync(self.cur())
        self.assertEqual(self.s.get(1)["state"], "resolved")
        self.clock.t += DAY
        self.assertEqual(self.s.sync(self.cur(finding(days=8)))["created"], 1)
        v = self.s.get(1)
        self.assertEqual((v["state"], v["label"], v["message_ref"], v["first_seen"]), ("open", None, None, int(self.clock.t)))
        self.assertEqual(self.kinds(), ["created", "labeled", "resolved", "created"])

    def test_a_stale_file_means_the_job_is_down_and_resolves_nothing(self):
        self.s.sync(self.cur(finding()))
        self.clock.t += 8 * 3600
        out = self.s.sync(self.cur(age=7 * 3600))                              # last written 7 h ago: older than the 6 h limit
        self.assertTrue(out["stale"])
        self.assertEqual(self.s.list()[0]["missing"], 0)
        self.assertTrue(any(a.get("event") == "forecast_sync_stale" for a in self.audit))

    def test_a_first_run_cannot_flood_the_channel_and_the_rest_follow(self):
        many = [finding(target=f"h{i}:/", days=5 + i) for i in range(20)]
        first = self.s.sync(self.cur(*many))
        self.assertEqual((first["created"], first["deferred"]), (8, 12))
        self.clock.t += 60
        second = self.s.sync(self.cur(*many))
        self.assertEqual((second["created"], second["deferred"]), (8, 4))
        self.clock.t += 60
        self.assertEqual(self.s.sync(self.cur(*many))["created"], 4)
        self.assertEqual(len(self.s.list()), 20)
        self.assertEqual(self.s.list()[0]["days_to_full"], 5)                   # the most urgent first

    def test_labels_are_validated_recorded_and_a_missing_row_is_a_404(self):
        self.s.sync(self.cur(finding()))
        with self.assertRaises(fs.Refused) as cm:
            self.s.label(1, "maybe", "op")
        self.assertEqual(cm.exception.status, 400)
        v = self.s.label(1, "useful", "op")
        self.assertEqual((v["label"], v["labeled_by"]), ("useful", "op"))
        with self.assertRaises(fs.Refused) as cm:
            self.s.label(99, "noise", "op")
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(self.s.summary()["labels"], {"useful": 1})

    def test_the_feed_is_a_cursor_and_set_message_binds_the_card(self):
        self.s.sync(self.cur(finding(), finding(target="x:/", days=9)))
        feed = self.s.feed(0)
        self.assertEqual([e["forecast"]["target"] for e in feed["events"]], ["hugin:/", "x:/"])
        self.assertEqual(self.s.feed(feed["next"])["events"], [])
        self.assertEqual(self.s.set_message(1, "555", "777")["message_ref"], "555")

    def test_garbage_findings_are_ignored_not_fatal(self):
        out = self.s.sync({"ts": self.clock.t, "findings": [None, {}, {"fingerprint": ""}, "x", finding()]})
        self.assertEqual((out["seen"], out["created"]), (1, 1))


class Wiring(unittest.TestCase):
    def setUp(self):
        self.r = tcr.Rig()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "forecast-current.json"
        tb = self.r.tb
        tb.fc = fs.Forecasts(tb.db, tb._lock, tb.clock, tb.audit)
        tb.cfg.forecast_file = self.file

    def tearDown(self):
        self.r.close()

    def write(self, *findings, ts=None):
        self.file.write_text(json.dumps({"ts": ts if ts is not None else self.r.clock.t, "findings": list(findings), "stats": {}}))

    def test_the_toolbelt_ingests_the_file_once_per_change_and_survives_a_bad_one(self):
        self.assertIsNone(self.r.tb.ingest_forecasts())                      # no file yet: nothing, no error
        self.write(finding())
        self.assertEqual(self.r.tb.ingest_forecasts()["created"], 1)
        self.assertIsNone(self.r.tb.ingest_forecasts())                      # unchanged mtime: not re-read
        self.file.write_text("{not json")
        self.file.touch()
        import os
        os.utime(self.file, (self.file.stat().st_atime, self.file.stat().st_mtime + 5))
        self.assertIsNone(self.r.tb.ingest_forecasts())
        self.assertTrue(any(a.get("event") == "forecast_ingest_error" for a in self.r.audit))
        self.assertEqual(self.r.tb.status()["forecasts"]["by_state"], {"open": 1})

    def test_routes_are_approver_only_and_list_feed_label_and_message_work(self):
        self.write(finding(), finding(target="urd/pbs-backup", metric="m2", days=6))
        self.r.tb.ingest_forecasts()
        for token in (tcr.T_AGENT, tcr.T_AUTH, tcr.T_TOOLS):
            for method, path in (("GET", "/forecasts"), ("GET", "/forecasts/feed"), ("POST", "/forecasts/1/label")):
                self.assertEqual(self.r.call(token, method, path, {} if method == "POST" else None)[0], 403, (token[:1], path))
        st, body = self.r.call(tcr.T_APPR, "GET", "/forecasts")
        self.assertEqual((st, [f["target"] for f in body["forecasts"]]), (200, ["urd/pbs-backup", "hugin:/"]))   # the nearer ETA first
        self.assertEqual(len(self.r.call(tcr.T_APPR, "GET", "/forecasts/feed")[1]["events"]), 2)
        self.assertEqual(self.r.call(tcr.T_APPR, "GET", "/forecasts/1")[1]["fingerprint"], "forecast:slow-fill:vfs.fs.dependent.size[*,pused]:hugin:/")
        st, v = self.r.call(tcr.T_APPR, "POST", "/forecasts/1/label", {"label": "useful", "by": tcr.OP})
        self.assertEqual((st, v["label"]), (200, "useful"))
        self.assertEqual(self.r.call(tcr.T_APPR, "POST", "/forecasts/1/label", {"label": "noise", "by": "999"})[0], 403)   # not an operator
        self.assertEqual(self.r.call(tcr.T_APPR, "POST", "/forecasts/1/label", {"label": "bogus", "by": tcr.OP})[0], 400)
        self.assertEqual(self.r.call(tcr.T_APPR, "POST", "/forecasts/1/message", {"message_ref": "555", "thread_id": "777"})[1]["message_ref"], "555")
        self.assertEqual(self.r.call(tcr.T_APPR, "GET", "/forecasts/99")[0], 404)

    def test_without_a_findings_file_the_routes_say_501(self):
        self.r.tb.fc = None
        self.assertEqual(self.r.call(tcr.T_APPR, "GET", "/forecasts")[0], 501)
        self.assertEqual(self.r.call(tcr.T_APPR, "POST", "/forecasts/1/label", {"label": "useful", "by": tcr.OP})[0], 501)


if __name__ == "__main__":
    unittest.main()
