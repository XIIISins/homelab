"""Phase 10f: autonomous T1 healing (aiops/toolbelt/autonomy.py + the gates in actions.Engine.consider_auto).

The rules under test: autonomy is OFF by default; only a registry policy can make a proposal run itself, only on the
registry's hosts, only for a diagnosis that names the runbook with enough confidence; the Toolbelt reads reality itself
before acting; rate limits and the circuit breaker stop it; the kill switch and maintenance always win.
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import actions  # noqa: E402
import autonomy  # noqa: E402
from test_actions import OP, REGISTRY, FakeSemaphore, make, result_line  # noqa: E402

DIAG = {"layer": "workload", "confidence": "high", "runbook_id": "RB-UNIT-STOPPED-T1"}
UNIT = "zabbix-agent2.service"


def status(state):
    return ("success", [result_line(action="service-status", ok=True, active_state=state)])


def restart_ok():
    return ("success", [result_line(action="restart-unit", ok=True, active_state="active")])


def settle():
    for t in threading.enumerate():
        if t.name.startswith("auto-"):
            t.join(10)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.au = REGISTRY.autonomy
        self.pol = self.au.policy_for("restart-unit")
        self.p = {"source": "diagnosis", "replay": False, "params": {"target_host": "canary-1", "unit": UNIT}}

    def test_only_the_enabled_policy_covers_an_action(self):
        self.assertEqual(self.pol.name, "restart-failed-unit")
        self.assertIsNone(self.au.policy_for("flux-reconcile-reset"))  # defined but disabled
        self.assertIsNone(self.au.policy_for("replay-role"))
        self.assertIsNone(self.au.policy_for("flux-reconcile"))

    def test_static_gates(self):
        b = self.au.static_block
        self.assertIsNone(b(self.pol, self.p, DIAG))
        self.assertEqual(b(self.pol, {**self.p, "replay": True}, DIAG), "replay")
        self.assertEqual(b(self.pol, {**self.p, "source": "chat"}, DIAG), "not-a-diagnosis")
        self.assertEqual(b(None, self.p, DIAG), "no-policy")
        self.assertEqual(b(self.pol, {**self.p, "params": {"target_host": "mimir", "unit": "AdGuardHome.service"}}, DIAG), "host-out-of-scope")
        self.assertEqual(b(self.pol, self.p, {**DIAG, "layer": "network"}), "layer")
        self.assertEqual(b(self.pol, self.p, {**DIAG, "confidence": "low"}), "confidence")
        self.assertEqual(b(self.pol, self.p, {**DIAG, "confidence": "medium"}), None)
        self.assertEqual(b(self.pol, self.p, {**DIAG, "runbook_id": "RB-OTHER"}), "runbook-mismatch")
        self.assertEqual(b(self.pol, self.p, {"layer": "workload"}), "confidence")

    def test_unit_verdicts(self):
        v = autonomy.Autonomy.unit_verdict
        self.assertEqual(v("failed")[0], "go")
        self.assertEqual(v("inactive")[0], "go")
        self.assertEqual(v("active")[0], "skip")
        for odd in ("activating", "deactivating", "reloading", None, ""):
            self.assertEqual(v(odd)[0], "stop")

    def test_helmrelease_verdicts(self):
        v = autonomy.Autonomy.helmrelease_verdict
        self.assertEqual(v([{"type": "Stalled", "status": "True"}, {"type": "Ready", "status": "False"}])[0], "go")
        self.assertEqual(v([{"type": "Ready", "status": "True"}])[0], "skip")
        self.assertEqual(v([{"type": "Ready", "status": "Unknown"}])[0], "stop")
        self.assertEqual(v(None)[0], "stop")
        self.assertEqual(v("garbage")[0], "stop")

    def test_drift_verdicts(self):
        pol = self.au.policies["replay-drifted-baseline"]
        v = autonomy.Autonomy.drift_verdict
        self.assertEqual(v(pol, 3)[0], "go")
        self.assertEqual(v(pol, 0)[0], "skip")
        self.assertEqual(v(pol, pol.max_changed + 1)[0], "stop")
        for bad in (None, "3", True, 2.5):
            self.assertEqual(v(pol, bad)[0], "stop")

    def test_no_autonomy_section_means_nothing_can_run_itself(self):
        self.assertIsNone(autonomy.Autonomy.from_registry({"actions": {}}))


class EngineGateTests(unittest.TestCase):
    def build(self, script=None, on=True):
        self.sem = FakeSemaphore(script or {})
        self.eng, self.clock, self.audit, self.db = make(self.sem)
        if on:
            self.eng.set_flag("autonomy", True, by=OP, reason="test")
        return self.eng

    def propose(self, host="canary-1", unit=UNIT, **kw):
        return self.eng.propose(action_id="restart-unit", params={"target_host": host, "unit": unit}, reason="the unit stopped",
                                source=kw.pop("source", "diagnosis"), incident_id=kw.pop("incident_id", 1), **kw)

    def run_auto(self, host="canary-1", diag=None):
        p = self.propose(host)
        out = self.eng.consider_auto(p["id"], diag or DIAG)
        settle()
        return p["id"], out, self.eng.get(p["id"])

    # -- the switches ----------------------------------------------------------------------------------------
    def test_off_by_default_and_the_proposal_waits_for_a_human(self):
        self.build(on=False)
        pid, out, p = self.run_auto()
        self.assertEqual(out, {"auto": False, "reason": "autonomy-off"})
        self.assertEqual(p["state"], "pending")
        self.assertEqual(self.sem.started, [])

    def test_kill_switch_and_maintenance_win(self):
        for flag, reason in (("kill_switch", "kill-switch"), ("maintenance", "maintenance")):
            self.build()
            self.eng.set_flag(flag, True, by=OP, reason="test")
            pid, out, p = self.run_auto()
            self.assertEqual((out["auto"], out["reason"], p["state"]), (False, reason, "pending"))
            self.assertEqual(self.sem.started, [])

    def test_only_an_operator_flips_the_master_switch_and_only_the_system_trips_the_breaker(self):
        self.build(on=False)
        with self.assertRaises(actions.Refused) as cm:
            self.eng.set_flag("autonomy", True, by="222222222222222222")
        self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(actions.Refused) as cm:
            self.eng.set_flag("autonomy_breaker", True, by=OP)  # an operator can reset it, never trip it
        self.assertEqual(cm.exception.status, 403)

    # -- the happy path and the precheck -----------------------------------------------------------------------
    def test_a_really_failed_unit_is_restarted_by_policy_and_verified(self):
        self.build({"aiops-service-status": [status("failed"), status("active")], "aiops-restart-unit": [restart_ok()]})
        pid, out, p = self.run_auto()
        self.assertEqual(out, {"auto": True, "policy": "restart-failed-unit"})
        self.assertEqual((p["state"], p["decided_by"]), ("succeeded", "auto:restart-failed-unit"))
        self.assertEqual([s[0] for s in self.sem.started], ["aiops-service-status", "aiops-restart-unit", "aiops-service-status"])
        self.assertEqual([s["step"] for s in p["result"]["steps"]], ["precheck:service-status", "action", "verify:service-status"])
        self.assertTrue(any(a["event"] == "proposal_auto_approved" for a in self.audit))

    def test_a_unit_that_healed_itself_is_skipped_not_restarted(self):
        self.build({"aiops-service-status": [status("active")]})
        pid, out, p = self.run_auto()
        self.assertEqual(p["state"], "skipped")
        self.assertEqual([s[0] for s in self.sem.started], ["aiops-service-status"])  # nothing was restarted

    def test_an_inconclusive_precheck_stops_rather_than_acts(self):
        self.build({"aiops-service-status": [status("activating")]})
        pid, out, p = self.run_auto()
        self.assertEqual(p["state"], "cancelled")
        self.assertEqual([s[0] for s in self.sem.started], ["aiops-service-status"])

    def test_a_precheck_that_cannot_read_stops(self):
        self.build({"aiops-service-status": [("error", ["TASK failed"])]})
        pid, out, p = self.run_auto()
        self.assertEqual(p["state"], "cancelled")
        self.assertEqual([s[0] for s in self.sem.started], ["aiops-service-status"])

    def test_a_human_approval_never_runs_the_precheck(self):
        self.build({"aiops-restart-unit": [restart_ok()], "aiops-service-status": [status("active")]})
        p = self.propose()
        self.eng.decide(p["id"], "approve", by=OP, ref="x", params_hash_seen=p["params_hash"], run=False)
        out = self.eng.execute(p["id"])
        self.assertEqual(out["state"], "succeeded")
        self.assertEqual([s[0] for s in self.sem.started], ["aiops-restart-unit", "aiops-service-status"])

    # -- scope -------------------------------------------------------------------------------------------------
    def test_scope_and_diagnosis_gates_leave_the_proposal_for_a_human(self):
        cases = [("mimir", "AdGuardHome.service", DIAG, "host-out-of-scope"),
                 ("canary-1", UNIT, {**DIAG, "confidence": "low"}, "confidence"),
                 ("canary-1", UNIT, {**DIAG, "layer": "network"}, "layer"),
                 ("canary-1", UNIT, {**DIAG, "runbook_id": "RB-DISK-FULL"}, "runbook-mismatch")]
        for host, unit, diag, reason in cases:
            self.build()
            p = self.propose(host, unit)
            out = self.eng.consider_auto(p["id"], diag)
            self.assertEqual(out, {"auto": False, "reason": reason})
            self.assertEqual(self.eng.get(p["id"])["state"], "pending")
            self.assertEqual(self.sem.started, [])

    def test_a_replay_proposal_never_runs_itself(self):
        self.build()
        p = self.propose(replay=True)
        self.assertEqual(self.eng.consider_auto(p["id"], DIAG), {"auto": False, "reason": "replay"})

    def test_a_chat_proposal_never_runs_itself(self):
        self.build()
        p = self.propose(source="chat", conversation_id=3, incident_id=None)
        self.assertEqual(self.eng.consider_auto(p["id"], DIAG), {"auto": False, "reason": "not-a-diagnosis"})

    def test_an_action_without_an_enabled_policy_never_runs_itself(self):
        self.build()
        p = self.eng.propose(action_id="flux-reconcile", params={"hr_name": "vmagent", "hr_namespace": "monitoring"}, reason="stalled", source="diagnosis", incident_id=1)
        self.assertEqual(self.eng.consider_auto(p["id"], DIAG), {"auto": False, "reason": "no-policy"})

    # -- limits and the breaker ----------------------------------------------------------------------------------
    def test_per_target_rate_limit(self):
        self.build({"aiops-service-status": [status("failed"), status("active")] * 3, "aiops-restart-unit": [restart_ok()] * 3})
        for _ in range(2):
            _, out, p = self.run_auto()
            self.assertEqual(p["state"], "succeeded")
        _, out, p = self.run_auto()
        self.assertEqual(out, {"auto": False, "reason": "target-rate-limit"})
        self.assertEqual(p["state"], "pending")
        self.clock.t += 3601  # the window rolls on
        _, out, p = self.run_auto()
        self.assertTrue(out["auto"])

    def test_per_policy_daily_limit(self):
        n = self.build().reg.autonomy.limits.per_policy_per_day
        self.build({"aiops-service-status": [status("failed"), status("active")] * (n + 1), "aiops-restart-unit": [restart_ok()] * (n + 1)})
        hosts = ["canary-1", "canary-2", "canary-3"]
        for i in range(n):
            self.clock.t += 3601  # stay under the per-target hourly limit
            _, out, p = self.run_auto(hosts[i % 3])
            self.assertTrue(out["auto"], i)
        self.clock.t += 3601
        _, out, _ = self.run_auto("canary-1")
        self.assertEqual(out, {"auto": False, "reason": "policy-daily-limit"})

    def test_repeated_failures_trip_the_breaker_and_only_an_operator_rearms_it(self):
        script = {"aiops-service-status": [status("failed")] * 3, "aiops-restart-unit": [("error", ["TASK failed"])] * 3}
        self.build(script)
        for host in ("canary-1", "canary-2", "canary-3"):
            _, out, p = self.run_auto(host)
            self.assertTrue(out["auto"])
            self.assertEqual(p["state"], "failed")
        self.assertTrue(self.eng.flag("autonomy_breaker"))
        self.assertTrue(any(a["event"] == "breaker_tripped" for a in self.audit))
        self.assertIn("breaker_tripped", [e["kind"] for e in self.eng.feed()["events"]])
        self.clock.t += 3601
        _, out, p = self.run_auto("canary-1")
        self.assertEqual(out, {"auto": False, "reason": "breaker-open"})
        self.assertEqual(p["state"], "pending")  # a human can still approve it
        self.eng.set_flag("autonomy_breaker", False, by=OP, reason="reset after looking")
        self.assertFalse(self.eng.flag("autonomy_breaker"))

    def test_failures_outside_the_window_do_not_count(self):
        self.build({"aiops-service-status": [status("failed")] * 3, "aiops-restart-unit": [("error", ["TASK failed"])] * 3})
        for host in ("canary-1", "canary-2", "canary-3"):
            self.run_auto(host)
            self.clock.t += 3601
        self.assertFalse(self.eng.flag("autonomy_breaker"))  # three failures, but never three inside one window

    def test_a_verify_failure_counts_toward_the_breaker(self):
        self.build({"aiops-service-status": [status("failed"), status("failed")], "aiops-restart-unit": [restart_ok()]})
        _, _, p = self.run_auto()
        self.assertEqual(p["state"], "verify_failed")
        self.assertEqual(self.eng.report(1)["by_policy"], {"restart-failed-unit": {"verify_failed": 1}})

    # -- the report ------------------------------------------------------------------------------------------------
    def test_report_says_what_ran_and_why_things_did_not(self):
        self.build({"aiops-service-status": [status("failed"), status("active")], "aiops-restart-unit": [restart_ok()]})
        self.run_auto("canary-1")
        p = self.propose("mimir", "AdGuardHome.service")
        self.eng.consider_auto(p["id"], DIAG)
        r = self.eng.report(14)
        self.assertEqual(r["autonomous_runs"], 1)
        self.assertEqual(r["by_policy"], {"restart-failed-unit": {"succeeded": 1}})
        self.assertEqual(r["by_target"], {"canary-1": 1})
        self.assertEqual(r["skipped_reasons"], {"host-out-of-scope": 1})
        self.assertEqual(r["breaker_trips"], 0)
        self.assertTrue(r["flags"]["autonomy"])


if __name__ == "__main__":
    unittest.main()
