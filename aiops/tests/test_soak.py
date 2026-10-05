"""Phase 10f soak: scheduled fault injection on the canaries (aiops/toolbelt/soak.py + the `soak:` registry section + canary-fault).

The rules under test: it injects only while every gate is open (autonomy ON, no kill switch / maintenance / breaker, the template applied,
inside its end date, the interval and the daily cap), on a quiet canary, rotating; it follows each injection to healed / restored(missed) /
failed and always puts the unit back itself; no agent can propose its action and only the scheduler can approve it; and the evidence reaches
the report. A fake Semaphore and a fake clock drive the real executor, so the proposals really run.
"""
from __future__ import annotations

import copy
import datetime as dt
import sqlite3
import sys
import threading
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import actions  # noqa: E402
import soak  # noqa: E402
from test_actions import OP, Clock, FakeSemaphore, result_line  # noqa: E402

RAW = yaml.safe_load((REPO / "aiops" / "actions.yml").read_text())
UNIT = "zabbix-agent2.service"


def stop_ok():
    return ("success", [result_line(action="canary-fault", mode="stop", ok=True, active_state="inactive", was_in_state=False)])


def restore_ok(was_in_state=False):
    return ("success", [result_line(action="canary-fault", mode="restore", ok=True, active_state="active", was_in_state=was_in_state)])


def registry(applied=True, **soak_over):
    d = copy.deepcopy(RAW)
    d["actions"]["canary-fault"]["semaphore"]["applied"] = applied
    d["soak"].update(soak_over)
    return actions.Registry(d)


def settle():
    for t in threading.enumerate():
        if t.name.startswith("exec-"):
            t.join(10)


class Rig:
    def __init__(self, script=None, applied=True, with_executor=True, autonomy=True, **soak_over):
        self.clock = Clock()
        self.db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE counters (name TEXT NOT NULL, day TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (name, day))")
        self.audit: list[dict] = []
        self.sem = FakeSemaphore(script or {})
        reg = registry(applied, **soak_over)

        def bump(name):
            self.db.execute("INSERT INTO counters(name, day, n) VALUES (?, 'd', 1) ON CONFLICT(name, day) DO UPDATE SET n=n+1", (name,))
            return counter(name)

        def counter(name):
            r = self.db.execute("SELECT n FROM counters WHERE name=?", (name,)).fetchone()
            return r["n"] if r else 0

        cfg = actions.ActionConfig(operators=frozenset({OP}), semaphore=self.sem if with_executor else None,
                                   sleep=lambda s: setattr(self.clock, "t", self.clock.t + s))
        self.eng = actions.Engine(self.db, threading.RLock(), self.clock, lambda event, **kw: self.audit.append({"event": event, **kw}), reg, cfg, bump, counter)
        self.soak = soak.Soak(self.eng, soak.SoakConfig.from_registry({"soak": reg.soak_raw}))
        if autonomy:
            self.eng.set_flag("autonomy", True, by=OP, reason="test")

    def tick(self):
        out = self.soak.tick()
        settle()
        return out

    def row(self, n=1):
        return self.db.execute("SELECT * FROM soak_injections WHERE id=?", (n,)).fetchone()

    def plant_heal(self, host, state="succeeded", by="auto:restart-failed-unit", at=None):
        """A finished restart-unit proposal on the host, as the autonomy loop (or a human) would leave it."""
        now = int(self.clock.t)
        self.db.execute("INSERT INTO proposals(source, action_id, params_json, params_hash, tier, target, reason, state, replay, created_at, expires_at, decided_by, finished_at) "
                        "VALUES ('diagnosis','restart-unit','{}',?, 'T1', ?, 'the unit stopped', ?, 0, ?, ?, ?, ?)",
                        (f"h{now}{host}{state}", host, state, now, now + 1000, by, at if at is not None else now))

    def advance(self, seconds):
        self.clock.t += seconds


class Gates(unittest.TestCase):
    def test_every_closed_gate_waits_and_proposes_nothing(self):
        r = Rig(autonomy=False)
        self.assertEqual(r.tick(), {"did": "skipped", "reason": "autonomy-off"})
        for flag, reason in (("kill_switch", "kill-switch"), ("maintenance", "maintenance")):
            r = Rig()
            r.eng.set_flag(flag, True, by=OP, reason="t")
            self.assertEqual(r.tick(), {"did": "skipped", "reason": reason})
        r = Rig()
        r.eng.set_flag("autonomy_breaker", True, by="system", reason="t", system=True)
        self.assertEqual(r.tick(), {"did": "skipped", "reason": "breaker-open"})
        self.assertEqual(Rig(applied=False).tick(), {"did": "skipped", "reason": "not-applied"})  # the Terraform apply has not happened
        self.assertEqual(Rig(with_executor=False).tick(), {"did": "skipped", "reason": "no-executor"})
        r = Rig(ends="2026-09-01")
        self.assertEqual(r.tick(), {"did": "skipped", "reason": "ended"})
        for rig in (r,):
            self.assertEqual(rig.db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0], 0)
            self.assertEqual(rig.sem.started, [])

    def test_a_skip_is_audited_once_an_hour_not_every_pass(self):
        r = Rig(autonomy=False)
        for _ in range(5):
            r.tick()
        self.assertEqual(len([a for a in r.audit if a["event"] == "soak_skipped"]), 1)
        r.advance(3601)
        r.tick()
        self.assertEqual(len([a for a in r.audit if a["event"] == "soak_skipped"]), 2)

    def test_the_end_date_is_inclusive_and_enforced_in_utc(self):
        day = dt.datetime.fromtimestamp(Clock().t, dt.timezone.utc).date().isoformat()
        self.assertEqual(Rig({"aiops-canary-fault": [stop_ok()]}, ends=day).tick()["did"], "started")  # the last day still runs


class Lifecycle(unittest.TestCase):
    def test_injects_waits_and_records_an_autonomous_heal(self):
        r = Rig({"aiops-canary-fault": [stop_ok()]})
        out = r.tick()
        self.assertEqual((out["did"], out["host"], out["unit"]), ("started", "canary-1", UNIT))
        p = r.eng._row(out["proposal"])
        self.assertEqual((p["source"], p["decided_by"], p["action_id"], p["state"]), ("soak", "soak", "canary-fault", "succeeded"))
        self.assertEqual(r.sem.started[0][1], {"target_host": "canary-1", "unit": UNIT, "mode": "stop"})
        self.assertEqual(r.tick()["did"], "injected")
        self.assertEqual(r.row()["state"], "waiting")
        r.advance(240)
        self.assertEqual(r.tick(), {"did": "waiting", "injection": 1, "reason": "fault live"})
        r.advance(60)
        r.plant_heal("canary-1")  # the autonomy loop restarted it
        out = r.tick()
        self.assertEqual((out["did"], out["by"]), ("healed", "autonomy"))
        self.assertEqual(out["seconds"], 300)
        self.assertEqual((r.row()["state"], r.row()["outcome"], r.row()["healed_by"]), ("healed", "healed", "autonomy"))

    def test_a_heal_by_hand_or_a_skipped_proposal_is_attributed_correctly(self):
        for state, by, who in (("succeeded", OP, "operator"), ("skipped", "auto:restart-failed-unit", "external")):
            r = Rig({"aiops-canary-fault": [stop_ok()]})
            r.tick()
            r.tick()
            r.advance(100)
            r.plant_heal("canary-1", state=state, by=by)
            self.assertEqual(r.tick()["by"], who)

    def test_a_heal_on_another_host_or_before_the_stop_is_not_evidence(self):
        r = Rig({"aiops-canary-fault": [stop_ok()]})
        r.plant_heal("canary-1", at=int(r.clock.t) - 500)  # before the stop
        r.tick()
        r.tick()
        r.advance(10)
        r.plant_heal("canary-2")
        self.assertEqual(r.tick()["did"], "waiting")

    def test_nothing_heals_it_so_the_scheduler_restores_it_and_records_a_miss(self):
        r = Rig({"aiops-canary-fault": [stop_ok(), restore_ok()]})
        r.tick()
        r.tick()
        r.advance(1500)
        out = r.tick()  # the deadline: restore
        self.assertEqual(out["did"], "restoring")
        self.assertEqual(r.sem.started[1][1]["mode"], "restore")
        self.assertEqual(r.tick()["did"], "restored")
        self.assertEqual((r.row()["state"], r.row()["outcome"]), ("restored", "missed"))

    def test_a_unit_already_active_at_restore_time_was_healed_by_something_else(self):
        r = Rig({"aiops-canary-fault": [stop_ok(), restore_ok(was_in_state=True)]})
        r.tick()
        r.tick()
        r.advance(1500)
        r.tick()
        out = r.tick()
        self.assertEqual((out["did"], out["by"]), ("healed", "external"))

    def test_a_stop_that_failed_is_undone_and_not_counted_as_a_miss(self):
        r = Rig({"aiops-canary-fault": [("error", ["TASK failed"]), restore_ok()]})
        r.tick()
        out = r.tick()
        self.assertEqual(out["did"], "restoring")
        r.tick()
        self.assertEqual((r.row()["state"], r.row()["outcome"]), ("failed", "inject-failed"))

    def test_a_restore_that_fails_is_loud(self):
        r = Rig({"aiops-canary-fault": [stop_ok(), ("error", ["TASK failed"])]})
        r.tick()
        r.tick()
        r.advance(1500)
        r.tick()
        out = r.tick()
        self.assertEqual((out["did"], out["reason"]), ("failed", "restore-failed"))
        self.assertIn("soak_restore_failed", [a["event"] for a in r.audit])

    def test_the_kill_switch_at_the_deadline_leaves_the_restore_pending_until_it_is_lifted(self):
        r = Rig({"aiops-canary-fault": [stop_ok(), restore_ok()]})
        r.tick()
        r.tick()
        r.eng.set_flag("kill_switch", True, by=OP, reason="t")
        r.advance(1500)
        out = r.tick()
        self.assertEqual(out["did"], "waiting")
        self.assertIn("restore refused", out["reason"])
        self.assertEqual(r.row()["state"], "waiting")  # nothing is forgotten
        r.eng.set_flag("kill_switch", False, by=OP, reason="t")
        self.assertEqual(r.tick()["did"], "restoring")
        self.assertEqual(r.tick()["did"], "restored")

    def test_only_one_injection_is_ever_open(self):
        r = Rig({"aiops-canary-fault": [stop_ok(), stop_ok(), restore_ok()]})
        r.tick()
        r.tick()
        r.advance(30000)  # long past the interval, and past the deadline: it restores this one, it does not start another
        self.assertEqual(r.tick()["did"], "restoring")
        self.assertEqual(r.db.execute("SELECT COUNT(*) FROM soak_injections").fetchone()[0], 1)

    def test_a_stop_proposal_stuck_for_half_an_hour_is_given_up_on(self):
        r = Rig({"aiops-canary-fault": [stop_ok()]})
        r.eng.execute = lambda pid, resume=False: None  # the executor never gets to it: the proposal stays approved
        out = r.soak.tick()
        self.assertEqual(out["did"], "started")
        self.assertEqual(r.soak.tick()["reason"], "stop is approved")
        r.advance(1801)
        self.assertEqual(r.soak.tick()["reason"], "stuck")
        self.assertEqual((r.row()["state"], r.row()["outcome"]), ("failed", "stuck"))


class Schedule(unittest.TestCase):
    def finish(self, r, host_script=None):
        r.tick()
        r.tick()
        r.plant_heal(r.row(r.db.execute("SELECT MAX(id) FROM soak_injections").fetchone()[0])["host"])
        r.tick()

    def test_the_interval_and_the_rotation(self):
        r = Rig({"aiops-canary-fault": [stop_ok()] * 6})
        self.finish(r)
        r.advance(60)
        self.assertEqual(r.tick(), {"did": "skipped", "reason": "interval"})
        r.advance(8 * 3600)
        out = r.tick()
        self.assertEqual(out["host"], "canary-2")  # the least recently injected canary goes next
        self.finish_open(r)
        r.advance(8 * 3600 + 5)
        self.assertEqual(r.tick()["host"], "canary-3")

    def finish_open(self, r):
        r.tick()
        r.plant_heal(r.db.execute("SELECT host FROM soak_injections ORDER BY id DESC LIMIT 1").fetchone()[0])
        r.tick()

    def test_the_daily_cap(self):
        r = Rig({"aiops-canary-fault": [stop_ok()] * 6}, interval_seconds=3600, max_per_day=2)
        for _ in range(2):
            self.assertEqual(r.tick()["did"], "started")
            self.finish_open(r)
            r.advance(3601)
        self.assertEqual(r.tick(), {"did": "skipped", "reason": "day-cap"})
        r.advance(86400)
        self.assertEqual(r.tick()["did"], "started")

    def test_a_busy_or_alerting_canary_is_skipped_and_all_busy_means_wait(self):
        r = Rig({"aiops-canary-fault": [stop_ok()] * 3})
        r.db.execute("CREATE TABLE alerts (fingerprint TEXT, status TEXT, host TEXT, last_seen INTEGER)")
        r.db.execute("INSERT INTO alerts VALUES ('f','firing','canary-1',?)", (int(r.clock.t) - 60,))  # a real problem is open there
        out = r.tick()
        self.assertEqual(out["host"], "canary-2")
        self.finish_open(r)
        r.advance(8 * 3600 + 5)
        for fp, host in (("f2", "canary-1"), ("g", "canary-2"), ("h", "canary-3")):  # a problem is open on every canary now
            r.db.execute("INSERT INTO alerts VALUES (?,'firing',?,?)", (fp, host, int(r.clock.t) - 60))
        self.assertEqual(r.tick(), {"did": "skipped", "reason": "no-quiet-canary"})

    def test_the_units_rotate_when_there_are_several(self):
        r = Rig({"aiops-canary-fault": [stop_ok()] * 8}, units=[UNIT, "vlagent.service"], interval_seconds=3600, max_per_day=12)
        seen = []
        for _ in range(6):
            seen.append((r.tick()["unit"]))
            self.finish_open(r)
            r.advance(3601)
        self.assertEqual(seen, [UNIT, UNIT, UNIT, "vlagent.service", "vlagent.service", "vlagent.service"])


class Authority(unittest.TestCase):
    def test_no_agent_chat_or_author_can_propose_the_action(self):
        r = Rig()
        for source in ("diagnosis", "chat", "author", "operator"):
            with self.assertRaises(actions.Refused) as cm:
                r.eng.propose(action_id="canary-fault", params={"target_host": "canary-1", "unit": UNIT, "mode": "stop"}, reason="please inject", source=source)
            self.assertEqual(cm.exception.status, 422, source)
            self.assertIn("by the Toolbelt itself", str(cm.exception.detail))

    def test_the_scheduler_can_propose_nothing_else(self):
        r = Rig()
        with self.assertRaises(actions.Refused) as cm:
            r.eng.propose(action_id="restart-unit", params={"target_host": "canary-1", "unit": UNIT}, reason="restart it", source="soak")
        self.assertIn("soak scheduler may only propose its own action", str(cm.exception.detail))
        with self.assertRaises(actions.Refused):
            r.eng.propose(action_id="pr-canary-test", params={"branch": "agent/docs/1-x"}, reason="test a pr", source="soak")

    def test_the_guard_confines_it_to_the_canaries_and_their_allow_listed_units(self):
        r = Rig()
        for params in ({"target_host": "mimir", "unit": UNIT, "mode": "stop"},               # a real T1 replica
                       {"target_host": "canary-1", "unit": "sshd.service", "mode": "stop"},   # a unit nobody listed
                       {"target_host": "canary-1", "unit": UNIT, "mode": "kill"},             # not a mode
                       {"target_host": "canary-1; reboot", "unit": UNIT, "mode": "stop"}):
            with self.assertRaises(actions.Refused):
                r.eng.propose(action_id="canary-fault", params=params, reason="scheduled soak injection", source="soak")

    def test_only_a_soak_proposal_can_be_decided_by_the_system(self):
        r = Rig()
        p = r.eng.propose(action_id="restart-unit", params={"target_host": "canary-1", "unit": UNIT}, reason="the unit stopped", source="diagnosis")
        with self.assertRaises(actions.Refused) as cm:
            r.eng.decide(p["id"], "approve", by="soak", system=True)
        self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(actions.Refused) as cm:
            r.eng.decide(p["id"], "approve", by="soak")  # and without `system`, "soak" is not an operator
        self.assertEqual(cm.exception.status, 403)

    def test_an_injection_never_counts_against_the_autonomy_limits_or_the_breaker(self):
        r = Rig({"aiops-canary-fault": [("error", ["TASK failed"])] * 3 + [restore_ok()] * 3}, interval_seconds=3600, max_per_day=12)
        for _ in range(3):
            r.tick()
            r.tick()
            r.tick()
            r.advance(3601)
        self.assertFalse(r.eng.flag("autonomy_breaker"))  # three failed injections, and the breaker stays closed
        self.assertEqual(r.eng._auto_counts("canary-1", "restart-failed-unit", int(r.clock.t)), (0, 0))

    def test_the_agents_never_hear_about_the_action(self):
        sys.path.insert(0, str(REPO / "aiops" / "n8n"))
        import build_ingest  # noqa: E402

        self.assertNotIn("canary-fault", build_ingest.propose_tool_description())  # internal: only the scheduler proposes it
        self.assertIn("restart-unit", build_ingest.propose_tool_description())     # while the normal actions are still advertised


class Evidence(unittest.TestCase):
    def test_the_report_carries_outcomes_who_healed_and_the_median(self):
        r = Rig({"aiops-canary-fault": [stop_ok(), stop_ok(), restore_ok()]}, interval_seconds=3600, max_per_day=12)
        r.tick()
        r.tick()
        r.advance(200)
        r.plant_heal("canary-1")
        r.tick()                      # healed by autonomy after 200 s
        r.advance(3601)
        r.tick()
        r.tick()
        r.advance(1501)
        r.tick()
        r.tick()                      # restored: a miss
        r.advance(3601)
        rep = r.eng.report(1)["soak"]
        self.assertEqual((rep["injections"], rep["by_outcome"], rep["healed_by"], rep["median_heal_seconds"]),
                         (2, {"healed": 1, "missed": 1}, {"autonomy": 1}, 200))
        self.assertIsNone(rep["open"])
        self.assertEqual(rep["last"]["host"], "canary-2")

    def test_an_open_fault_is_shown_and_a_registry_without_the_scheduler_reports_none(self):
        r = Rig({"aiops-canary-fault": [stop_ok()]})
        r.tick()
        r.tick()
        self.assertEqual(r.eng.report(1)["soak"]["open"], {"host": "canary-1", "unit": UNIT, "state": "waiting"})
        r.eng.soak = None
        self.assertIsNone(r.eng.report(1)["soak"])

    def test_the_bot_renders_it(self):
        sys.path.insert(0, str(REPO / "aiops" / "bot"))
        import logic  # noqa: E402

        text = logic.format_report({"days": 7, "soak": {"ends": "2026-10-31", "injections": 3, "by_outcome": {"healed": 2, "missed": 1},
                                                          "healed_by": {"autonomy": 2}, "median_heal_seconds": 310,
                                                          "open": {"host": "canary-2", "unit": UNIT, "state": "waiting"}}})
        self.assertIn("Scheduled fault injection", text)
        self.assertIn("3 injected", text)
        self.assertIn("healed 2", text)
        self.assertIn("median heal 310 s", text)
        self.assertIn("LIVE FAULT", text)


class Playbook(unittest.TestCase):
    def test_the_guard_play_and_the_result_line_are_in_the_playbook(self):
        text = (REPO / "ansible" / "playbooks" / "aiops-canary-fault.yml").read_text()
        plays = yaml.safe_load(text)
        self.assertEqual([p["hosts"] for p in plays][0], "localhost")  # the guard runs first, before anything is touched
        guard = " ".join(str(t) for t in plays[0]["tasks"])
        for must in ("groups['canary']", "_soak_hosts", "_allowed_units", "mode in ['stop', 'restore']", "host_tiers.T1"):
            self.assertIn(must, guard)
        self.assertIn("AIOPS_RESULT", text)
        self.assertIn("was_in_state", text)


if __name__ == "__main__":
    unittest.main()
