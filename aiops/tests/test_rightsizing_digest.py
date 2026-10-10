"""Phase 10i3: Gná's rightsizing digest - the Toolbelt side (aiops/toolbelt/rightsizing_digest.py, its routes) and the bot's text for it
(aiops/bot/rsdigest.py, the rightsizing forecast card), without Discord."""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests", "author", "bot"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import actions  # noqa: E402
import change_requests  # noqa: E402
import core  # noqa: E402
import fcast  # noqa: E402
import forecast_store  # noqa: E402
import logic  # noqa: E402
import rsdigest  # noqa: E402
import server  # noqa: E402
import test_change_requests as tcr  # noqa: E402
from test_actions import FakeSemaphore  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

MIB = 1024 * 1024
DAY = 86400
NOW = 1_800_000_000


def rs_finding(name="web", container="main", finding="memory-under-request", metric="memory-request", ns="app", kind="Deployment", **ev):
    target = f"{ns}/{kind}/{name}/{container}"
    return {"fingerprint": f"rightsizing:{target}/{metric}", "kind": "rightsizing", "metric": metric, "target": target, "confidence": "medium",
            "days_to_full": None, "ratio": None, "ts": NOW, "evidence": {"finding": finding, **ev}}


def under(name="web", added=200 * MIB, new=456.0, old=256.0, **kw):
    return rs_finding(name, finding="memory-under-request", request_mib=old, limit_mib=512.0, max_working_set_mib=380.0, proposed_request_mib=new,
                      proposed_limit_mib=None, added_bytes=added, **kw)


def over(name="big", freed=900 * MIB, old=1024.0, new=128.0):
    return rs_finding(name, finding="memory-over-request", request_mib=old, limit_mib=None, proposed_request_mib=new, freed_bytes=freed)


def cpu(name="cpuy", old=500.0, new=60.0, freed=440.0):
    return rs_finding(name, container="c", finding="cpu-over-request", metric="cpu-request", request_millicores=old, proposed_request_millicores=new, freed_millicores=freed)


def creep(name="leaky"):
    return rs_finding(name, finding="memory-creep", metric="memory-creep", slope_mib_per_day=6.0, rise_mib=120.0, days=20, latest_daily_peak_mib=300.0,
                      request_mib=256.0, limit_mib=512.0)


def oom_note(name="odd"):
    return rs_finding(name, finding="memory-under-request", request_mib=512.0, limit_mib=2048.0, oomkilled=True, proposed_request_mib=None, proposed_limit_mib=None,
                      note="OOMKilled, but the 30-day peak working set is far below the limit: check the events; no number is proposed", added_bytes=0)


def worker(node, req, limits, used, cpu_m):
    return {"node": node, "memory_requested_mib": req, "memory_requested_pct": round(req / 140, 1), "memory_limits_mib": limits, "memory_used_mib": used,
            "cpu_requested_millicores": cpu_m, "cpu_requested_pct": round(cpu_m / 20, 1)}


def snapshot(req=11000.0, age=10.0, **kw):
    return {"as_of": NOW, "workers": [worker("einherjar-urd", req, 9000.0, 3500.0, 700.0), worker("einherjar-verd", 6500.0, 12000.0, 3200.0, 660.0)],
            "controllers": 50, "controllers_with_vpa": 49, "controllers_without_vpa": ["app/Deployment/new"], "oldest_vpa_sample_days": age,
            "vpa_min_sample_age_days": 7, "suppressed": {"vpa-immature": 2}, **kw}


class Rig(tcr.Rig):
    """The change-request rig plus a forecast file, so the Toolbelt has the forecasts table, the digest and the routes."""

    def __init__(self, classes=None, **crcfg):
        self.tmp = tempfile.TemporaryDirectory()
        self.file_path = Path(self.tmp.name) / "forecast-current.json"
        self.clock, self.audit = tcr.Clock(), []
        self.clock.t = float(NOW)
        c = core.Config(live=core.tools.LiveConfig(root=REPO), replay_dir=REPO / "aiops" / "replays", forecast_file=self.file_path)
        c.actions = actions.ActionConfig(operators=frozenset({tcr.OP}), semaphore=FakeSemaphore({}), poll_seconds=0, sleep=lambda s: None)
        c.change_requests = change_requests.CRConfig(classes=classes or tcr.CLASSES, **crcfg)
        self.tb = core.Toolbelt(c, tcr.ROUTES, tcr.KNOWN, clock=self.clock, audit=self.audit.append, registry=tcr.REGISTRY)
        h = server.make_handler(self.tb, tcr.T_AGENT, tcr.LOOP, tcr.T_APPR, tcr.LOOP, tcr.T_AUTH, tcr.LOOP, tcr.T_TOOLS, tcr.LOOP)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        super().close()
        self.tmp.cleanup()

    def feed(self, *findings, snap=None, ts=None):
        self.file_path.write_text(json.dumps({"ts": self.clock.t, "findings": list(findings), "stats": {},
                                              "rightsizing": {"ts": ts or self.clock.t, "findings": list(findings), "stats": {}, "snapshot": snap or snapshot()}}))
        self.tb._fc_mtime = 0.0
        return self.tb.ingest_forecasts()

    def appr(self, method, path, body=None):
        return self.call(tcr.T_APPR, method, path, body)


class Build(unittest.TestCase):
    def setUp(self):
        self.r = Rig()

    def tearDown(self):
        self.r.close()

    def test_suggestions_are_ranked_hazards_first_then_by_what_they_free_and_capped_at_five(self):
        self.r.feed(over("a", freed=100 * MIB), over("b", freed=900 * MIB), under("hazard"), cpu(freed=200.0), over("c", freed=300 * MIB), over("d", freed=500 * MIB), over("e", freed=700 * MIB))
        st, out = self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})
        self.assertEqual(st, 200, out)
        d = out["digest"]
        names = [s["target"].split("/")[2] for s in d["suggestions"]]
        self.assertEqual(names, ["hazard", "b", "e", "d", "c"])             # the under-request leads, then 900, 700, 500, 300 MiB; 'a' (100) and the CPU row (440m) are cut
        self.assertEqual(d["suggestions"][0]["summary"], "memory request 256 MiB → 456 MiB")
        self.assertTrue(d["baseline"])
        self.assertEqual(d["coverage"]["open_findings"], 7)

    def test_creep_and_an_oom_with_no_number_are_facts_not_suggestions(self):
        self.r.feed(creep(), oom_note(), over())
        d = self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})[1]["digest"]
        self.assertEqual([s["target"].split("/")[2] for s in d["suggestions"]], ["big"])
        self.assertEqual(sorted(t["target"].split("/")[2] for t in d["tuning"]), ["leaky", "odd"])
        self.assertIn("6.0 MiB a day", next(t for t in d["tuning"] if "leaky" in t["target"])["text"])
        self.assertIn("no number is proposed", next(t for t in d["tuning"] if "odd" in t["target"])["text"])

    def test_the_scoreboard_compares_with_the_last_digest_and_the_baseline(self):
        self.r.feed(over())
        first = self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})[1]["digest"]
        self.assertTrue(first["baseline"])
        self.assertIsNone(first["scoreboard"][0]["vs_baseline"] and None)   # (the first digest compares with itself: all zero)
        self.assertEqual(first["scoreboard"][0]["vs_last"], None)
        self.r.appr("POST", f"/rightsizing/digest/{first['id']}/message", {"message_ref": "1", "thread_id": "2"})
        self.r.clock.t += 7 * DAY
        self.r.feed(over(), snap=snapshot(req=9500.0))
        second = self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})[1]["digest"]
        urd = second["scoreboard"][0]
        self.assertFalse(second["baseline"])
        self.assertEqual(urd["vs_last"]["memory_requested_mib"], -1500.0)
        self.assertEqual(urd["vs_baseline"]["memory_requested_mib"], -1500.0)
        self.assertIn("kube_pod_container_resource_requests", __import__("urllib.parse").parse.unquote(urd["vmui"]))

    def test_a_noise_label_holds_until_the_number_moves_by_more_than_30_percent(self):
        self.r.feed(over("quiet", old=1024.0, new=128.0))
        fid = self.r.tb.fc.list(kind="rightsizing")[0]["id"]
        self.assertEqual(self.r.appr("POST", f"/forecasts/{fid}/label", {"label": "noise", "by": tcr.OP})[0], 200)
        self.assertEqual(self.r.tb.fc.get(fid)["label_value"], 128.0)
        d = self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})[1]["digest"]
        self.assertEqual(d["suggestions"], [])
        self.r.appr("POST", f"/rightsizing/digest/{d['id']}/message", {"message_ref": "1"})
        self.r.feed(over("quiet", old=1024.0, new=150.0))                     # 17 % off: still noise
        d1 = self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})[1]["digest"]
        self.assertEqual(d1["suggestions"], [])
        self.r.appr("POST", f"/rightsizing/digest/{d1['id']}/message", {"message_ref": "2"})
        self.r.feed(over("quiet", old=1024.0, new=256.0))                     # 100 % off: worth a second look
        d2 = self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})[1]["digest"]
        self.assertEqual([s["target"] for s in d2["suggestions"]], ["app/Deployment/quiet/main"])

    def test_useful_does_not_hide_a_suggestion(self):
        self.r.feed(over())
        fid = self.r.tb.fc.list(kind="rightsizing")[0]["id"]
        self.r.appr("POST", f"/forecasts/{fid}/label", {"label": "useful", "by": tcr.OP})
        self.assertEqual(len(self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})[1]["digest"]["suggestions"]), 1)

    def test_no_snapshot_yet_is_a_clear_409(self):
        st, out = self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})
        self.assertEqual(st, 409)
        self.assertIn("snapshot", out["error"])


class Schedule(unittest.TestCase):
    def setUp(self):
        self.r = Rig()
        self.r.feed(over())

    def tearDown(self):
        self.r.close()

    def tick(self):
        st, out = self.r.appr("POST", "/rightsizing/digest/tick", {})
        self.assertEqual(st, 200, out)
        return out

    def test_the_first_digest_waits_for_a_mature_vpa(self):
        self.r.feed(over(), snap=snapshot(age=3.0))
        out = self.tick()
        self.assertEqual((out["digest"], out["why"]), (None, "vpa-immature"))
        self.r.feed(over(), snap=snapshot(age=7.5))
        self.assertEqual(self.tick()["why"], "due")

    def test_a_stale_pass_never_produces_a_digest(self):
        self.r.clock.t += 2 * DAY
        self.r.file_path.write_text(self.r.file_path.read_text())
        out = self.tick()
        self.assertEqual((out["digest"], out["why"]), (None, "stale-pass"))

    def test_an_unposted_digest_is_handed_out_again_until_the_bot_sets_its_message(self):
        d = self.tick()["digest"]
        again = self.tick()
        self.assertEqual((again["why"], again["digest"]["id"]), ("unposted", d["id"]))
        self.r.appr("POST", f"/rightsizing/digest/{d['id']}/message", {"message_ref": "55", "thread_id": "66"})
        out = self.tick()
        self.assertEqual((out["digest"], out["why"]), (None, "not-due"))

    def test_weekly_by_default_then_the_cadence_the_operator_picks(self):
        d = self.tick()["digest"]
        self.r.appr("POST", f"/rightsizing/digest/{d['id']}/message", {"message_ref": "1"})
        self.r.clock.t += 6 * DAY
        self.r.feed(over())
        self.assertEqual(self.tick()["why"], "not-due")
        self.r.clock.t += 1 * DAY
        self.r.feed(over())
        self.assertEqual(self.tick()["why"], "due")
        # bi-weekly: a week later is too early, two weeks is on time
        d2 = self.r.appr("GET", f"/rightsizing/digest/{self.r.tb.rs.latest()['id']}")[1]
        self.r.appr("POST", f"/rightsizing/digest/{d2['id']}/message", {"message_ref": "2"})
        st, status = self.r.appr("POST", "/rightsizing/cadence", {"cadence": "biweekly", "by": tcr.OP})
        self.assertEqual((st, status["cadence"], status["period_days"]), (200, "biweekly", 14))
        self.r.clock.t += 7 * DAY
        self.r.feed(over())
        self.assertEqual(self.tick()["why"], "not-due")
        self.r.clock.t += 7 * DAY
        self.r.feed(over())
        self.assertEqual(self.tick()["why"], "due")

    def test_the_cadence_and_the_on_demand_digest_are_operator_only(self):
        for path, body in (("/rightsizing/cadence", {"cadence": "monthly", "by": "999"}), ("/rightsizing/digest", {"by": "999"})):
            self.assertEqual(self.r.appr("POST", path, body)[0], 403, path)
        self.assertEqual(self.r.appr("POST", "/rightsizing/cadence", {"cadence": "daily", "by": tcr.OP})[0], 400)
        for tok in (tcr.T_AGENT, tcr.T_AUTH):
            self.assertEqual(self.r.call(tok, "POST", "/rightsizing/digest/tick", {})[0], 403)
        self.assertEqual(self.r.appr("GET", "/rightsizing/status")[1]["cadence"], "weekly")

    def test_on_demand_digest_works_while_the_vpa_is_young(self):
        self.r.feed(over(), snap=snapshot(age=2.0))
        st, out = self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})
        self.assertEqual(st, 200)
        self.assertEqual(out["digest"]["coverage"]["oldest_vpa_sample_days"], 2.0)


class Drafting(unittest.TestCase):
    """What the card may offer: a Draft button only for an allow-listed workload, with a number, in an enabled class, with no PR in flight."""

    def classes(self, enabled):
        c = copy.deepcopy(tcr.CLASSES)
        c["classes"]["rightsizing"] = {"enabled": enabled, "summary": "x", "allow": ["k8s/asgard/apps/*/helmrelease.yaml"]}
        return c

    def test_the_button_follows_the_class_the_allow_list_and_the_number(self):
        for enabled, expect in ((False, False), (True, True)):
            r = Rig(classes=self.classes(enabled))
            try:
                r.feed(rs_finding("netbox", ns="netbox", finding="memory-over-request", request_mib=1024.0, proposed_request_mib=128.0, freed_bytes=900 * MIB),
                       rs_finding("vault", ns="vault", kind="StatefulSet", finding="memory-over-request", request_mib=1024.0, proposed_request_mib=128.0, freed_bytes=900 * MIB),
                       creep("leaky"))
                rows = {x["target"].split("/")[2]: x for x in r.tb.fc.list(kind="rightsizing")}
                self.assertEqual(rows["netbox"]["draftable"], expect, enabled)
                self.assertFalse(rows["vault"]["draftable"])      # not on the allow-list
                self.assertFalse(rows["leaky"]["draftable"])      # a creep has no number to apply
                self.assertEqual(fcast.buttons({**rows["netbox"]}), (["useful", "noise", "draft"] if expect else ["useful", "noise"]))
            finally:
                r.close()


class Migration(unittest.TestCase):
    def test_a_database_from_before_label_value_is_migrated(self):
        db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.executescript(forecast_store.SCHEMA.replace(" label_value REAL,", ""))
        self.assertNotIn("label_value", {r["name"] for r in db.execute("PRAGMA table_info(forecasts)")})
        forecast_store.Forecasts(db, threading.RLock(), lambda: NOW, lambda *a, **k: None)
        self.assertIn("label_value", {r["name"] for r in db.execute("PRAGMA table_info(forecasts)")})


class BotText(unittest.TestCase):
    def digest(self):
        return {"id": 4, "created_at": NOW, "as_of": NOW, "baseline": True, "cadence": "weekly", "period_days": 7,
                "scoreboard": [{**worker("einherjar-urd", 11000.0, 9000.0, 3500.0, 700.0), "vs_last": {"memory_requested_mib": -1500.0}, "vs_baseline": None}],
                "suggestions": [{"forecast_id": 1}], "tuning": [{"target": "app/Deployment/leaky/main", "text": "The daily peak is rising about 6 MiB a day."}],
                "results": [{"target": "app/Deployment/web", "verdict": "held", "freed_mib": 512.0, "pr_url": "https://github.com/XIIISins/homelab/pull/300"}],
                "coverage": {"controllers": 50, "with_vpa": 49, "without_vpa": ["app/Deployment/new"], "suppressed": {"vpa-immature": 2}, "oldest_vpa_sample_days": 9.0, "open_findings": 7}}

    def test_the_header_has_the_scoreboard_results_coverage_and_says_nothing_changes(self):
        h = rsdigest.header(self.digest())
        self.assertEqual(h["title"], "Rightsizing digest")
        self.assertIn("baseline", h["description"])
        body = {n: v for n, v, _ in h["fields"]}
        self.assertIn("einherjar-urd", body["Workers (memory requested vs scheduler capacity)"])
        self.assertIn("-1500 vs last", body["Workers (memory requested vs scheduler capacity)"])
        self.assertIn("**held**", body["Results of earlier PRs"])
        self.assertIn("freed 512 MiB", body["Results of earlier PRs"])
        self.assertIn("`app/Deployment/new`", body["Coverage"])
        self.assertIn("Worth a look (no proposal)", body)
        self.assertIn("nothing here changes the cluster", h["footer"])
        for _, v, _ in h["fields"]:
            self.assertLessEqual(len(v), 1024)

    def test_the_header_with_nothing_to_say_still_reads_cleanly(self):
        d = {**self.digest(), "baseline": False, "suggestions": [], "tuning": [], "results": []}
        h = rsdigest.header(d)
        self.assertIn("Nothing to suggest", h["description"])
        self.assertIn("None yet", dict((n, v) for n, v, _ in h["fields"])["Results of earlier PRs"])

    def test_the_card_for_a_suggestion_uses_the_forecast_buttons_and_never_calls_itself_an_alert(self):
        fc = {"id": 9, "kind": "rightsizing", "metric": "memory-request", "target": "app/Deployment/web/main", "state": "open", "confidence": "medium", "label": None,
              "draftable": True, "pr": None, "finding": "memory-under-request", "summary": "memory request 256 MiB → 456 MiB", "under": True,
              "evidence": {"evidence": {"finding": "memory-under-request", "max_working_set_mib": 380.0, "vpa_upper_mib": 400.0, "oomkilled": True}}, "first_seen": NOW}
        c = fcast.card(fc)
        self.assertEqual(c["title"], "Rightsizing: app/Deployment/web/main")
        self.assertEqual(c["description"], "memory request 256 MiB → 456 MiB")
        self.assertEqual(c["buttons"], ["useful", "noise", "draft"])
        fields = {n: v for n, v, _ in c["fields"]}
        self.assertEqual(fields["Finding"], "Memory request too low")
        self.assertIn("**OOMKilled**", fields["Evidence"])
        self.assertIn("proposals only", c["footer"])
        pr = {**fc, "draftable": False, "pr": {"id": 12, "state": "pr-open", "pr_url": "https://github.com/XIIISins/homelab/pull/1"}}
        self.assertEqual(fcast.card(pr)["buttons"], ["useful", "noise"])
        self.assertIn("request #12", dict((n, v) for n, v, _ in fcast.card(pr)["fields"])["Draft PR"])

    def test_the_client_talks_to_the_real_routes(self):
        r = Rig()
        try:
            r.feed(over())
            cfg = logic.Config(toolbelt_url=r.base, approver_token=tcr.T_APPR, n8n_chat_url="", n8n_chat_token="", operator_ids=frozenset({tcr.OP}),
                               guild_id=1, diagnoses_channel_id=2, chat_channel_id=3)
            c = rsdigest.Client(cfg)
            st, out = c.tick()
            self.assertEqual((st, out["why"]), (200, "due"))
            did = out["digest"]["id"]
            self.assertEqual(c.set_message(did, "55", "66")[1]["message_ref"], "55")
            self.assertEqual(c.tick()[1]["why"], "not-due")
            self.assertEqual(c.set_cadence("monthly", tcr.OP)[1]["cadence"], "monthly")
            self.assertEqual(c.set_cadence("monthly", "999")[0], 403)
            self.assertEqual(c.status()[1]["period_days"], 30)
            self.assertEqual(c.now(tcr.OP)[0], 200)
        finally:
            r.close()


if __name__ == "__main__":
    unittest.main()
