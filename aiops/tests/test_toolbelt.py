"""Phase 10d2: the Toolbelt API (aiops/toolbelt): dedupe, correlation, breakers, auth.

Core rules run against an in-memory SQLite with a fake clock; the HTTP shell is driven
over a real loopback socket so the auth and refusal behaviour is the shipped behaviour.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))
sys.path.insert(0, str(REPO / "aiops" / "tools"))

import core  # noqa: E402
import normalize  # noqa: E402
import server  # noqa: E402

CASES = REPO / "aiops" / "fixtures" / "zabbix-native"
ROUTES = normalize.load_routes()
KNOWN = {r["id"] for r in yaml.safe_load((REPO / "aiops" / "runbooks.yml").read_text())["runbooks"]}
TOKEN = "t" * 40


def base_event() -> dict:
    return copy.deepcopy(json.loads((CASES / "native-high-problem-routed.json").read_text())["event"])


def ev(host="hlin", trigger="Etcd: Service is unavailable", status="PROBLEM", severity="High", event_id="1"):
    e = base_event()
    e.update(host=host, trigger_name=trigger, status=status, severity=severity, event_id=event_id)
    if status == "RESOLVED":
        e.update(recovery_event_id="9", resolved_at="2026-10-01T10:40:00Z")
    return e


class Clock:
    def __init__(self, t=1_790_000_000):
        self.t = t

    def __call__(self):
        return self.t


def make(clock=None, **cfg):
    clock = clock or Clock()
    audit: list[dict] = []
    tb = core.Toolbelt(core.Config(**cfg), ROUTES, KNOWN, clock=clock, audit=audit.append)
    return tb, clock, audit


class Dedupe(unittest.TestCase):
    def test_first_alert_leads_and_names_the_wait(self):
        tb, _, _ = make()
        r = tb.ingest_zabbix(ev())
        self.assertEqual((r["action"], r["wait_seconds"]), ("leader", 90))

    def test_same_fingerprint_inside_cooldown_is_a_duplicate_not_a_new_run(self):
        tb, clock, _ = make()
        first = tb.ingest_zabbix(ev())
        clock.t += 300
        r = tb.ingest_zabbix(ev(event_id="2"))
        self.assertEqual((r["action"], r["incident_id"], r["count"]), ("duplicate", first["incident_id"], 2))

    def test_duplicate_is_still_deduped_in_a_later_window_than_the_correlation_one(self):
        tb, clock, _ = make()
        first = tb.ingest_zabbix(ev())
        clock.t += 1000  # long past the 90 s window, inside the 30 min cooldown
        self.assertEqual(tb.ingest_zabbix(ev())["incident_id"], first["incident_id"])

    def test_after_the_cooldown_it_is_a_fresh_incident(self):
        tb, clock, _ = make()
        first = tb.ingest_zabbix(ev())
        clock.t += 1801
        r = tb.ingest_zabbix(ev())
        self.assertEqual(r["action"], "leader")
        self.assertNotEqual(r["incident_id"], first["incident_id"])

    def test_severity_escalation_updates_the_thread(self):
        tb, clock, _ = make()
        tb.ingest_zabbix(ev(severity="High"))
        clock.t += 60
        # High and Disaster both map to `critical`, so craft the escalation via a canary-cap change:
        # same fingerprint, higher rank is only reachable alert(1) -> critical(2).
        a = tb.db.execute("SELECT severity FROM alerts").fetchone()["severity"]
        self.assertEqual(a, "critical")
        tb.db.execute("UPDATE alerts SET severity='alert'")  # as if first seen at a lower tier
        r = tb.ingest_zabbix(ev(event_id="3"))
        self.assertEqual(r["action"], "escalated")

    def test_recovery_closes_and_a_refire_inside_cooldown_reopens_the_same_incident(self):
        tb, clock, _ = make()
        first = tb.ingest_zabbix(ev())
        clock.t += 120
        r = tb.ingest_zabbix(ev(status="RESOLVED"))
        self.assertEqual((r["action"], r["incident_id"]), ("resolved", first["incident_id"]))
        clock.t += 120
        r = tb.ingest_zabbix(ev(event_id="5"))
        self.assertEqual((r["action"], r["incident_id"]), ("reopened", first["incident_id"]))

    def test_recovery_for_an_unknown_problem_is_an_orphan_not_an_error(self):
        tb, _, _ = make()
        r = tb.ingest_zabbix(ev(status="RESOLVED"))
        self.assertEqual(r["action"], "orphan_resolved")
        self.assertEqual(tb.stats()["orphan_resolved_today"], 1)

    def test_a_second_recovery_is_also_an_orphan(self):
        tb, _, _ = make()
        tb.ingest_zabbix(ev())
        tb.ingest_zabbix(ev(status="RESOLVED"))
        self.assertEqual(tb.ingest_zabbix(ev(status="RESOLVED"))["action"], "orphan_resolved")

    def test_not_alertable_severity_is_ignored(self):
        tb, _, _ = make()
        self.assertEqual(tb.ingest_zabbix(ev(severity="Warning"))["action"], "ignored")


class Correlation(unittest.TestCase):
    def test_burst_inside_the_window_is_one_incident(self):
        tb, clock, _ = make()
        leader = tb.ingest_zabbix(ev(host="a1", trigger="T one"))
        members = []
        for i, h in enumerate(["a2", "a3", "a4", "a5"]):
            clock.t += 5
            members.append(tb.ingest_zabbix(ev(host=h, trigger="T one", event_id=str(10 + i))))
        self.assertTrue(all(m["action"] == "member" and m["incident_id"] == leader["incident_id"] for m in members))
        clock.t += 200
        g = tb.group(leader["incident_id"])
        self.assertEqual((g["alert_count"], g["state"], g["model_hint"]), (5, "grouped", "opus"))

    def test_after_the_window_closes_a_new_alert_starts_its_own_incident(self):
        tb, clock, _ = make()
        a = tb.ingest_zabbix(ev(host="a1"))
        clock.t += 91
        b = tb.ingest_zabbix(ev(host="a2"))
        self.assertEqual(b["action"], "leader")
        self.assertNotEqual(a["incident_id"], b["incident_id"])

    def test_different_hypervisors_do_not_merge(self):
        tb, _, _ = make(placement={"a1": "urd", "a2": "verd", "a3": "urd"})
        a = tb.ingest_zabbix(ev(host="a1"))
        b = tb.ingest_zabbix(ev(host="a2"))
        c = tb.ingest_zabbix(ev(host="a3"))
        self.assertEqual(b["action"], "leader")
        self.assertEqual((c["action"], c["incident_id"]), ("member", a["incident_id"]))
        self.assertEqual(tb.group(a["incident_id"])["hypervisors"], ["urd"])

    def test_group_view_before_the_window_closes_says_so(self):
        tb, clock, _ = make()
        r = tb.ingest_zabbix(ev())
        g = tb.group(r["incident_id"])
        self.assertTrue(g["window_open"])
        self.assertEqual(g["state"], "received")
        clock.t += 90
        g = tb.group(r["incident_id"])
        self.assertEqual((g["window_open"], g["state"]), (False, "grouped"))

    def test_canary_only_incident_is_low_priority_but_still_analysed(self):
        tb, _, _ = make()
        r = tb.ingest_zabbix(ev(host="canary-1"))
        g = tb.group(r["incident_id"])
        self.assertEqual((g["severity"], g["priority"]), ("info", "low"))

    def test_real_critical_is_normal_priority(self):
        tb, _, _ = make()
        r = tb.ingest_zabbix(ev())
        self.assertEqual(tb.group(r["incident_id"])["priority"], "normal")


class Breakers(unittest.TestCase):
    def test_queue_full_drops_loudly_and_flags_the_first_drop(self):
        tb, clock, audit = make(max_open_incidents=2)
        for h in ("a1", "a2"):
            clock.t += 100  # outside each other's window: separate incidents
            tb.ingest_zabbix(ev(host=h))
        clock.t += 100
        r = tb.ingest_zabbix(ev(host="a3"))
        self.assertEqual((r["action"], r["first_drop_today"]), ("dropped", True))
        self.assertFalse(tb.ingest_zabbix(ev(host="a4"))["first_drop_today"])
        self.assertTrue(any(a["event"] == "dropped" for a in audit))

    def test_daily_cap_blocks_the_run_and_says_first_once(self):
        tb, clock, _ = make(daily_run_cap=2)
        ids = []
        for h in ("a1", "a2", "a3"):
            clock.t += 100
            ids.append(tb.ingest_zabbix(ev(host=h))["incident_id"])
        tb.set_state(ids[0], "running")
        tb.set_state(ids[1], "running")
        with self.assertRaises(core.Rejected) as cm:
            tb.set_state(ids[2], "running")
        self.assertEqual((cm.exception.status, cm.exception.message), (429, "daily-run-cap first"))
        with self.assertRaises(core.Rejected) as cm:
            tb.set_state(ids[2], "running")
        self.assertEqual(cm.exception.message, "daily-run-cap")

    def test_the_cap_resets_the_next_utc_day(self):
        tb, clock, _ = make(daily_run_cap=1)
        a = tb.ingest_zabbix(ev(host="a1"))["incident_id"]
        tb.set_state(a, "running")
        clock.t += 86400
        b = tb.ingest_zabbix(ev(host="a2"))["incident_id"]
        self.assertEqual(tb.set_state(b, "running")["state"], "running")

    def test_a_stuck_run_stops_holding_a_queue_slot_after_the_wall_clock_cap(self):
        tb, clock, _ = make(max_open_incidents=1, run_wall_clock_seconds=100)
        a = tb.ingest_zabbix(ev(host="a1"))["incident_id"]
        tb.set_state(a, "running")  # its workflow then dies and never reports back
        clock.t += 50
        self.assertEqual(tb.ingest_zabbix(ev(host="a2"))["action"], "dropped")  # still in flight: the slot is held
        clock.t += 100
        self.assertEqual(tb.ingest_zabbix(ev(host="a3"))["action"], "leader")  # expired: new work proceeds
        self.assertTrue(tb.group(a)["timed_out"])  # and the stuck one is reported as timed out

    def test_watchdog_offers_a_dead_run_once_it_outlives_the_cap_and_stops_after_it_is_posted(self):
        tb, clock, _ = make(run_wall_clock_seconds=100)
        a = tb.ingest_zabbix(ev(host="a1"))["incident_id"]
        tb.set_state(a, "running")
        clock.t += 50
        self.assertEqual(tb.watchdog(), [])  # still within the cap: a slow run is not a dead one
        clock.t += 100
        offered = tb.watchdog()
        self.assertEqual([o["incident_id"] for o in offered], [a])
        self.assertIn("a1", offered[0]["content"])
        self.assertIn("(no analysis)", offered[0]["thread_name"])
        self.assertLessEqual(len(offered[0]["thread_name"]), 95)
        self.assertEqual([o["incident_id"] for o in tb.watchdog()], [a])  # offered again until it is actually posted
        tb.set_state(a, "posted", thread_id="42")
        self.assertEqual(tb.watchdog(), [])

    def test_watchdog_ignores_incidents_that_are_posted_or_never_started(self):
        tb, clock, _ = make(run_wall_clock_seconds=100)
        done = tb.ingest_zabbix(ev(host="a1"))["incident_id"]
        tb.set_state(done, "running")
        tb.set_state(done, "posted", thread_id="7")
        clock.t += 200
        tb.ingest_zabbix(ev(host="a2"))  # received, never started
        clock.t += 200
        self.assertEqual(tb.watchdog(), [])

    def test_a_run_past_the_wall_clock_cap_is_reported_timed_out(self):
        tb, clock, _ = make(run_wall_clock_seconds=100)
        a = tb.ingest_zabbix(ev())["incident_id"]
        tb.set_state(a, "running")
        clock.t += 101
        self.assertTrue(tb.group(a)["timed_out"])


class States(unittest.TestCase):
    def test_only_forward_transitions(self):
        tb, _, _ = make()
        a = tb.ingest_zabbix(ev())["incident_id"]
        tb.set_state(a, "running")
        tb.set_state(a, "posted", thread_id="123")
        self.assertEqual(tb.group(a)["thread_id"], "123")
        with self.assertRaises(core.Rejected) as cm:
            tb.set_state(a, "running")
        self.assertEqual(cm.exception.status, 409)

    def test_unknown_state_and_incident_are_rejected(self):
        tb, _, _ = make()
        a = tb.ingest_zabbix(ev())["incident_id"]
        with self.assertRaises(core.Rejected) as cm:
            tb.set_state(a, "deleted")
        self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(core.Rejected) as cm:
            tb.group(999)
        self.assertEqual(cm.exception.status, 404)

    def test_recovery_of_every_alert_resolves_a_posted_incident(self):
        tb, _, _ = make()
        a = tb.ingest_zabbix(ev())["incident_id"]
        tb.set_state(a, "running")
        tb.set_state(a, "posted", thread_id="9")
        r = tb.ingest_zabbix(ev(status="RESOLVED"))
        self.assertEqual(r["thread_id"], "9")
        self.assertEqual(tb.group(a)["state"], "resolved")


class Validation(unittest.TestCase):
    def test_bad_events_are_rejected_with_400(self):
        tb, _, _ = make()
        for bad in ("nope", {}, {**ev(), "schema_version": "v0"}, {**ev(), "status": "WAT"}, {**ev(), "tags": "x"}):
            with self.assertRaises(core.Rejected) as cm:
                tb.ingest_zabbix(bad)
            self.assertEqual(cm.exception.status, 400)

    def test_audit_trail_carries_the_fingerprint_and_never_the_payload_secrets(self):
        tb, _, audit = make()
        e = ev()
        e["opdata"] = "password=hunter2"
        tb.ingest_zabbix(e)
        self.assertTrue(all("hunter2" not in json.dumps(a) for a in audit))
        self.assertIn("fingerprint", audit[-1])


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tb, cls.clock, cls.audit = make()
        handler = server.make_handler(cls.tb, TOKEN, [ipaddress.ip_network("127.0.0.0/8")])
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def call(self, method, path, body=None, token=TOKEN, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_healthz_needs_no_token(self):
        self.assertEqual(self.call("GET", "/healthz", token=None), (200, {"ok": True}))

    def test_missing_or_wrong_token_is_forbidden(self):
        self.assertEqual(self.call("POST", "/ingest/zabbix", ev(), token=None)[0], 403)
        self.assertEqual(self.call("POST", "/ingest/zabbix", ev(), token="x" * 40)[0], 403)

    def test_a_client_outside_the_allow_list_is_forbidden_even_with_the_token(self):
        handler = server.make_handler(self.tb, TOKEN, [ipaddress.ip_network("10.99.0.0/16")])
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/stats")
            req.add_header("Authorization", f"Bearer {TOKEN}")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 403)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_ingest_then_group_over_http(self):
        status, out = self.call("POST", "/ingest/zabbix", ev(host="httpbox", trigger="Http only"))
        self.assertEqual((status, out["action"]), (200, "leader"))
        status, g = self.call("GET", f"/group/{out['incident_id']}")
        self.assertEqual((status, g["alert_count"]), (200, 1))
        status, g = self.call("POST", f"/group/{out['incident_id']}/state", {"state": "running"})
        self.assertEqual((status, g["state"]), (200, "running"))

    def test_write_shaped_and_unknown_routes_are_refused_by_the_api(self):
        for method, path in (("PUT", "/group/1"), ("DELETE", "/group/1"), ("PATCH", "/ingest/zabbix"),
                             ("POST", "/exec"), ("GET", "/admin"), ("POST", "/ingest/unknown-source")):
            self.assertEqual(self.call(method, path, {} if method != "GET" else None)[0], 404, (method, path))

    def test_bad_body_and_oversized_body(self):
        self.assertEqual(self.call("POST", "/ingest/zabbix", raw=b"{not json")[0], 400)
        self.assertEqual(self.call("POST", "/ingest/zabbix", {"x": "y" * (server.MAX_BODY + 1)})[0], 413)

    def test_denied_attempts_are_audited_without_the_token(self):
        self.call("POST", "/ingest/zabbix", ev(), token="x" * 40)
        denied = [a for a in self.audit if a["event"] == "denied"]
        self.assertTrue(denied)
        self.assertTrue(all("x" * 40 not in json.dumps(a) and TOKEN not in json.dumps(a) for a in self.audit))


if __name__ == "__main__":
    unittest.main()
