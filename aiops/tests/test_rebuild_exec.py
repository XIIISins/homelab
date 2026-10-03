"""Phase 10g2: the rebuild loop's engine support (aiops/toolbelt/rebuild_exec.py + the hooks in actions.Engine) and its bot text.

Everything runs with FAKES: a FakeRunner (the runner protocol as a transport function), a FakeFacts provider (the
Toolbelt's own read of reality), a FakeVerify provider and a scripted Semaphore. Nothing touches a live system. The
shipped registry keeps every rebuild action `applied: false`; the tests open the gates in a COPY so the whole flow runs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import socket
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "bot", "tools", "tests"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import actions  # noqa: E402
import logic  # noqa: E402
import rebuild  # noqa: E402
import rebuild_exec  # noqa: E402
from test_actions import OP, Clock, FakeSemaphore, approve, result_line  # noqa: E402

DATA = yaml.safe_load((REPO / "aiops" / "actions.yml").read_text())
SHIPPED = actions.Registry(copy.deepcopy(DATA))
REBUILD_ACTIONS = ("rebuild-plan", "start-guest", "rebuild-guest", "rebuild-worker", "rebuild-verify")
DIAG_DEAD = {"layer": "host", "confidence": "high", "runbook_id": "RB-GUEST-DEAD"}
DIAG_BROKEN = {"layer": "workload", "confidence": "high", "runbook_id": "RB-GUEST-BROKEN"}
CONVERGE = ("success", [result_line(action="rebuild-guest", ok=True)])
START_OK = ("success", [result_line(action="start-guest", ok=True, running=True)])


def live_data(policies=(), force_policies=False) -> dict:
    """The registry with the five rebuild actions' operator gates opened (a copy: the shipped file stays applied: false)."""
    d = copy.deepcopy(DATA)
    for n in REBUILD_ACTIONS:
        d["actions"][n]["semaphore"]["applied"] = True
        d["actions"][n]["semaphore"].pop("planned", None)
    for pn in policies:
        d["rebuild"]["policies"][pn]["enabled"] = True
    if force_policies:  # every class gets an ENABLED rebuild-guest policy: only the stage rules may stop an unattended rebuild
        for cn in d["rebuild"]["classes"]:
            d["rebuild"]["policies"][f"force-{cn}"] = {"enabled": True, "action": "rebuild-guest", "class": cn, "runbook": "RB-GUEST-DEAD",
                                                       "layers": ["host", "workload"], "min_confidence": "medium", "precheck": "guest-dead"}
    return d


class FakeRunner:
    """The runner protocol (v1) as a transport: transport(request, timeout) -> response. Scriptable failures."""

    def __init__(self, clock, data):
        self.clock, self.requests, self.n, self.applied, self.last = clock, [], 0, [], {}
        self.hosts = {h: (c, info) for c in data["rebuild"]["classes"].values() for h, info in c["hosts"].items()}
        self.action = "replace"
        self.plan_error = None       # {"problems": [...]} -> ok:false
        self.vmid_override = None
        self.apply_error = None      # "terraform-failed: boom"
        self.apply_hook = None       # called inside apply (e.g. to engage the kill switch)
        self.apply_crash = False     # the Toolbelt dies before the apply returns, the apply did NOT happen
        self.apply_crash_after = False  # the apply happened, then the Toolbelt died before it recorded it
        self.busy_polls = 0
        self.expires_in = 900

    def ops(self):
        return [r["op"] for r in self.requests]

    def __call__(self, req, timeout):
        self.requests.append(req)
        rid = req.get("request_id")
        if req["op"] == "plan":
            if self.plan_error:
                return {"v": 1, "request_id": rid, "ok": False, **self.plan_error}
            self.n += 1
            t = req["target"]
            cls, info = self.hosts[t]
            return {"v": 1, "request_id": rid, "ok": True, "plan_id": hashlib.sha256(f"{t}{self.n}".encode()).hexdigest(),
                    "address": f'proxmox_virtual_environment_container.lxc["{t}"]',
                    "summary": {"action": self.action, "changes": 1,
                                "identity": {"name": t, "vmid": self.vmid_override or info["vmid"], "node": info["node"], "ip": "10.0.11.191"}},
                    "origin_main": "a" * 40, "expires_at": int(self.clock()) + self.expires_in, "problems": []}
        if req["op"] == "apply":
            if self.apply_crash:
                raise KeyboardInterrupt("the Toolbelt died")
            if self.apply_hook:
                self.apply_hook()
            if self.apply_error:
                return {"v": 1, "request_id": rid, "ok": False, "error": self.apply_error}
            self.applied.append(req["plan_id"])
            self.last = {"plan_id": req["plan_id"], "ok": True, "seconds": 42}
            if self.apply_crash_after:
                raise KeyboardInterrupt("the Toolbelt died after the apply")
            return {"v": 1, "request_id": rid, "ok": True, "result": {"applied": True, "resources": 1}, "seconds": 42, "origin_main": "a" * 40}
        if req["op"] == "status":
            if self.busy_polls > 0:
                self.busy_polls -= 1
                return {"v": 1, "ok": True, "busy": True, "last": {}}
            return {"v": 1, "ok": True, "busy": False, "last": dict(self.last)}
        raise AssertionError(req)


class FakeFacts:
    def __init__(self, **over):
        self.f = {"guest_state": "missing", "node_online": True, "node_guests_ok": True, "probe_ok": None, "agent_silent": None,
                  "last_backup_age_hours": 2.0, "peers_healthy": True, "is_leader": False, "drift_changed": None, "replay_converges": None}
        self.f.update(over)
        self.manifest = {"node": "x", "blocks": False, "reasons": [], "summary": "Data on einherjar-urd if rebuilt:\n- no local-path volumes\nManifest clean."}

    def facts(self, target, cls):
        return dict(self.f)

    def worker_manifest(self, target):
        return self.manifest


class FakeVerify:
    def __init__(self):
        self.calls, self.fail, self.status, self.crash = [], [], "success", False

    def check(self, pid, target, class_name, conditions):
        if self.crash:
            raise KeyboardInterrupt("the Toolbelt died in verify")
        self.calls.append((target, class_name, tuple(conditions)))
        return {"status": self.status, "checks": {c: (c not in self.fail) for c in conditions}}


class CrashSemaphore(FakeSemaphore):
    def __init__(self, script=None):
        super().__init__(script)
        self.crash = False

    def status(self, task_id):
        if self.crash:
            raise KeyboardInterrupt("the Toolbelt died while converging")
        return super().status(task_id)


class Rig:
    def __init__(self, *, policies=(), force_policies=False, data=None, script=None, facts=None, prior=None, sem=None, **cfg):
        self.data = data or live_data(policies, force_policies)
        self.reg = actions.Registry(self.data)
        self.clock = prior.clock if prior else Clock()
        self.runner = prior.runner if prior else FakeRunner(self.clock, self.data)
        self.facts = prior.facts if prior else (facts or FakeFacts())
        self.verify = prior.verify if prior else FakeVerify()
        self.sem = prior.sem if prior else (sem or CrashSemaphore(script if script is not None else {"aiops-rebuild-converge": [CONVERGE]}))
        self.audit: list = [] if not prior else prior.audit
        if prior:
            self.db = prior.db
        else:
            self.db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            self.db.execute("CREATE TABLE counters (name TEXT NOT NULL, day TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (name, day))")

        def counter(name):
            r = self.db.execute("SELECT n FROM counters WHERE name=?", (name,)).fetchone()
            return r["n"] if r else 0

        def bump(name):
            self.db.execute("INSERT INTO counters(name, day, n) VALUES (?, 'd', 1) ON CONFLICT(name, day) DO UPDATE SET n=n+1", (name,))
            return counter(name)

        ac = actions.ActionConfig(operators=frozenset({OP}), semaphore=self.sem, runner=rebuild_exec.RunnerClient(transport=self.runner),
                                  facts=self.facts, verifier=self.verify, auto_resume=False, max_proposals_per_day=500,
                                  sleep=lambda s: setattr(self.clock, "t", self.clock.t + s), **cfg)
        self.eng = actions.Engine(self.db, threading.RLock(), self.clock, lambda event, **kw: self.audit.append({"event": event, **kw}), self.reg, ac, bump, counter)

    def propose(self, target="canary-2", action="rebuild-guest", params=None, source="diagnosis", **kw):
        return self.eng.propose(action_id=action, params=params or {"target": target}, reason=f"{target} is dead", source=source,
                                incident_id=kw.pop("incident_id", 1), thread_id="t1", **kw)

    def run(self, target="canary-2", **kw):
        """propose -> approve -> execute; returns the final view."""
        p = self.propose(target, **kw)
        approve(self.eng, p["id"])
        return self.eng.execute(p["id"])

    def flag(self, name, value=True, by=OP):
        return self.eng.set_flag(name, value, by=by, reason="test")

    def run_row(self, pid):
        return self.db.execute("SELECT * FROM rebuild_runs WHERE proposal_id=?", (pid,)).fetchone()


def settle():
    for t in threading.enumerate():
        if t.name.startswith(("auto-", "exec-")):
            t.join(10)


class HappyPath(unittest.TestCase):
    def test_a_canary_rebuild_runs_plan_apply_converge_verify_with_timings(self):
        r = Rig()
        p = r.propose()
        self.assertEqual(p["state"], "pending")
        self.assertEqual(p["params"]["plan_hash"], p["rebuild"]["plan_id"])  # the proposal is bound to the runner's plan
        self.assertEqual(p["rebuild"]["plan_action"], "replace")
        self.assertIn("DESTROYS the existing canary-2", p["rebuild"]["destroys"])
        self.assertEqual(r.runner.ops(), ["plan"])
        self.assertLessEqual(p["expires_at"], r.clock() + 900)  # a proposal never outlives its plan
        approve(r.eng, p["id"])
        final = r.eng.execute(p["id"])
        self.assertEqual(final["state"], "succeeded", final["result"])
        self.assertEqual(r.runner.ops(), ["plan", "apply"])
        self.assertEqual(r.runner.requests[1]["plan_id"], p["rebuild"]["plan_id"])
        name, env, fields = r.sem.started[0]
        self.assertEqual(name, "aiops-rebuild-converge")
        self.assertEqual((env["target"], env["class"], env["converge"]), ("canary-2", "canary", "asgard-canary"))
        self.assertEqual(fields, {"limit": "canary-2"})
        self.assertEqual(r.verify.calls[0][:2], ("canary-2", "canary"))
        self.assertIn("vlagent-active", r.verify.calls[0][2])  # the class post-conditions from the registry
        res = final["result"]
        self.assertEqual([s["step"] for s in res["steps"]], ["apply", "converge", "verify"])
        self.assertEqual(res["timings"]["apply"], 42)
        self.assertIn("total", res["timings"])
        row = r.run_row(p["id"])
        self.assertEqual((row["stage"], row["outcome"]), ("done", "succeeded"))
        self.assertFalse(r.eng.flag("autonomy_rebuild_breaker"))
        self.assertEqual(rebuild_exec.active(r.eng), [])  # nothing is rebuilding any more

    def test_a_missing_guest_plans_a_create(self):
        r = Rig()
        r.runner.action = "create"
        p = r.propose()
        self.assertIn("Nothing is destroyed", p["rebuild"]["destroys"])

    def test_rebuilding_is_visible_in_status_while_it_runs(self):
        r = Rig()
        seen = []
        r.verify.check = lambda pid, t, c, conds: (seen.append((rebuild_exec.active(r.eng), r.eng.summary()["rebuild"]["rebuilding"], r.eng._view(pid))),
                                                   {"status": "success", "checks": {x: True for x in conds}})[1]
        r.run()
        active, summary, view = seen[0]
        self.assertEqual((active[0]["target"], active[0]["stage"]), ("canary-2", "verify"))
        self.assertEqual(summary[0]["stage"], "verify")
        self.assertEqual(view["state"], "running")
        self.assertEqual(view["rebuild"]["stage"], "verify")

    def test_a_rebuild_plan_proposal_returns_the_runner_summary_and_a_later_rebuild_uses_it(self):
        r = Rig()
        pl = r.eng.propose(action_id="rebuild-plan", params={"target": "canary-2"}, reason="plan only please", source="chat", conversation_id=None, incident_id=1)
        approve(r.eng, pl["id"])
        done = r.eng.execute(pl["id"])
        self.assertEqual(done["state"], "succeeded")
        res = done["result"]["steps"][0]["result"]
        self.assertTrue(res["plan_ok"])
        self.assertEqual(res["summary"]["identity"]["vmid"], 1191)
        self.assertEqual(r.runner.ops(), ["plan"])
        self.assertIn("Plan ok: replace of canary-2", logic.plan_sentence(done))
        # the operator then proposes the rebuild bound to THAT plan: the runner is not asked for another
        p = r.propose(params={"target": "canary-2", "plan_hash": res["plan_id"]})
        self.assertEqual(r.runner.ops(), ["plan"])
        self.assertEqual(p["params"]["plan_hash"], res["plan_id"])
        # a plan_hash nobody planned is refused
        with self.assertRaises(actions.Refused) as cm:
            r.propose("canary-1", params={"target": "canary-1", "plan_hash": "0" * 64})
        self.assertEqual(cm.exception.status, 422)


class Refusals(unittest.TestCase):
    def refused(self, r, status=None, **kw):
        with self.assertRaises(actions.Refused) as cm:
            r.propose(**kw)
        if status:
            self.assertEqual(cm.exception.status, status)
        return cm.exception

    def test_the_shipped_registry_refuses_only_what_is_not_applied(self):
        """Since 2026-10-03 the canary stage (rebuild-plan, start-guest, rebuild-guest, rebuild-verify) is applied but
        approval-gated; rebuild-worker (stage C) has no playbook and stays refused by the applied gate."""
        r = Rig(data=copy.deepcopy(DATA))
        e = self.refused(r, 422, action="rebuild-worker", target="einherjar-urd", params={"target": "einherjar-urd", "plan_hash": "0" * 64})
        self.assertTrue(any("not applied" in p for p in e.detail["problems"]))
        self.assertEqual(r.runner.requests, [])
        # the same registry with the canary stage applied accepts a canary rebuild proposal, still pending a human
        ok = Rig(data=copy.deepcopy(DATA)).propose(params={"target": "canary-2"})
        self.assertEqual(ok["state"], "pending")
        self.assertFalse(DATA["rebuild"]["policies"]["rebuild-dead-canary"]["enabled"])  # nothing runs by itself

    def test_not_eligible_is_refused_before_anything_is_planned(self):
        for over, reason in ((dict(guest_state="running", probe_ok=True), "not-dead-or-broken"),
                             (dict(node_online=False), "node-unhealthy"),
                             (dict(guest_state="stopped"), "ladder-start-first"),
                             (dict(guest_state="unknown"), "state-unknown")):
            r = Rig(facts=FakeFacts(**over))
            e = self.refused(r)
            self.assertIn(reason, e.message, over)
            self.assertEqual(r.runner.requests, [])  # nothing was planned

    def test_a_running_guest_with_a_live_agent_is_not_dead(self):
        r = Rig(facts=FakeFacts(guest_state="running", probe_ok=False, agent_silent=False))
        for _ in range(4):
            r.clock.t += 400
            self.refused(r)
        self.assertEqual(r.eng.db.execute("SELECT COUNT(*) FROM rebuild_probes").fetchone()[0], 4)  # each read was recorded

    def test_dead_means_three_failed_probes_over_ten_minutes_and_a_silent_agent(self):
        r = Rig(facts=FakeFacts(guest_state="running", probe_ok=False, agent_silent=True))
        self.refused(r)  # one probe proves nothing
        r.clock.t += 400
        self.refused(r)
        r.clock.t += 400
        p = r.propose()  # the third probe, 800 s after the first
        self.assertEqual(p["state"], "pending")

    def test_the_ladder_start_then_rebuild(self):
        r = Rig(facts=FakeFacts(guest_state="stopped"), script={"aiops-start-guest": [("error", [])], "aiops-rebuild-converge": [CONVERGE]})
        self.refused(r, 409)  # start first
        s = r.eng.propose(action_id="start-guest", params={"target": "canary-2"}, reason="start canary-2", source="chat", incident_id=1)
        approve(r.eng, s["id"])
        self.assertEqual(r.eng.execute(s["id"])["state"], "failed")  # the start failed: now the rebuild is the next rung
        self.assertEqual(r.propose()["state"], "pending")

    def test_the_deny_list_and_unknown_targets_can_never_be_proposed(self):
        r = Rig()
        for name in DATA["rebuild"]["deny"]["names"] + ["canary-9", "mimir-x", "pbs", "gondul", "do1-x"]:
            for action in ("rebuild-plan", "rebuild-guest", "rebuild-worker", "start-guest", "rebuild-verify"):
                params = {"target": name}
                with self.assertRaises(actions.Refused, msg=(name, action)):
                    r.eng.propose(action_id=action, params=params, reason="try the deny list", source="chat", incident_id=1)
        self.assertEqual(r.runner.requests, [])

    def test_the_action_kind_must_match_the_class(self):
        r = Rig()
        for action, target in (("rebuild-guest", "einherjar-urd"), ("rebuild-worker", "canary-1"), ("start-guest", "do1")):
            with self.assertRaises(actions.Refused, msg=(action, target)):
                r.eng.propose(action_id=action, params={"target": target}, reason="wrong kind of guest", source="chat", incident_id=1)

    def test_the_runner_rejecting_the_plan_ends_the_proposal_with_nothing_stored(self):
        r = Rig()
        r.runner.plan_error = {"error": "", "problems": ["the plan destroys another resource: x"]}
        e = self.refused(r, 422)
        self.assertIn("did not produce a plan", e.message)
        self.assertEqual(r.eng.db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0], 0)

    def test_a_plan_that_fails_the_toolbelts_own_check_is_refused(self):
        r = Rig()
        r.runner.vmid_override = 1190  # a plan for canary-1's VMID presented as canary-2's
        e = self.refused(r, 422)
        self.assertTrue(any("vmid" in p for p in e.detail["problems"]))

    def test_the_runner_unreachable_is_a_502_not_a_guess(self):
        r = Rig()

        def dead(req, timeout):
            raise rebuild_exec.RunnerError("runner-unreachable", "FileNotFoundError")

        r.eng.cfg.runner = rebuild_exec.RunnerClient(transport=dead)
        self.refused(r, 502)

    def test_a_plan_is_used_once(self):
        r = Rig()
        p = r.propose()
        with self.assertRaises(sqlite3.IntegrityError):  # the table itself refuses a second run on the same plan
            r.eng.db.execute("INSERT INTO rebuild_runs(proposal_id, target, guest_class, mode, plan_id, plan_created_at, plan_expires_at, stage) "
                             "VALUES (99, 'canary-1', 'canary', 'approval', ?, 0, 0, 'planned')", (p["rebuild"]["plan_id"],))

    def test_the_apply_refuses_a_plan_that_aged_out(self):
        r = Rig()
        p = r.propose()
        approve(r.eng, p["id"])
        r.clock.t += 901
        final = r.eng.execute(p["id"])
        self.assertEqual(final["state"], "failed")
        self.assertIn("plan-expired", final["result"]["why"])
        self.assertEqual(r.runner.ops(), ["plan"])  # no apply was ever sent
        self.assertTrue(final["result"]["nothing_changed"])
        self.assertFalse(r.eng.flag("autonomy_rebuild_breaker"))  # nothing was changed: no budget spent
        self.assertIsNone(r.run_row(p["id"])["apply_started_at"])

    def test_the_apply_refuses_a_different_plan_hash(self):
        r = Rig()
        p = r.propose()
        approve(r.eng, p["id"])
        r.db.execute("UPDATE rebuild_runs SET plan_id=? WHERE proposal_id=?", ("f" * 64, p["id"]))
        final = r.eng.execute(p["id"])
        self.assertEqual(final["state"], "failed")
        self.assertIn("plan hash differs", final["result"]["why"])
        self.assertEqual(r.runner.ops(), ["plan"])

    def test_a_runner_refusal_before_the_apply_changes_nothing_and_costs_nothing(self):
        for code in rebuild_exec.PRE_APPLY_CODES:
            r = Rig()
            r.runner.apply_error = f"{code}: nope"
            final = r.run()
            self.assertEqual(final["state"], "failed", code)
            self.assertIn(code, final["result"]["why"])
            self.assertTrue(final["result"]["nothing_changed"])
            self.assertFalse(r.eng.flag("autonomy_rebuild_breaker"), code)
            self.assertEqual(r.sem.started, [])  # no converge after a refused apply

    def test_a_terraform_failure_trips_the_breaker_and_an_operator_must_re_arm_it(self):
        r = Rig()
        r.runner.apply_error = "terraform-failed: pve said no"
        final = r.run()
        self.assertEqual(final["state"], "failed")
        self.assertFalse(final["result"]["nothing_changed"])
        self.assertTrue(r.eng.flag("autonomy_rebuild_breaker"))
        ev = [e for e in r.eng.feed()["events"] if e["kind"] == "breaker_tripped"]
        self.assertEqual(ev[0]["data"]["kind"], "rebuild")
        # while it is open NOTHING is eligible, attended or not
        e = self.refused(r, 422, target="canary-1")
        self.assertIn("breaker-open", e.message)
        # only an operator re-arms it
        with self.assertRaises(actions.Refused):
            r.eng.set_flag("autonomy_rebuild_breaker", False, by="222222222222222222")
        with self.assertRaises(actions.Refused):
            r.eng.set_flag("autonomy_rebuild_breaker", False, by="system", system=True)
        r.flag("autonomy_rebuild_breaker", False)
        self.assertEqual(r.propose("canary-1")["state"], "pending")

    def test_the_runner_vanishing_during_the_apply_trips_the_breaker(self):
        r = Rig()
        orig = r.runner.__call__

        def flaky(req, timeout):
            if req["op"] == "apply":
                raise rebuild_exec.RunnerError("runner-unreachable", "ConnectionResetError")
            return orig(req, timeout)

        r.eng.cfg.runner = rebuild_exec.RunnerClient(transport=flaky)
        p = r.propose()
        approve(r.eng, p["id"])
        final = r.eng.execute(p["id"])
        self.assertEqual(final["state"], "failed")
        self.assertTrue(r.eng.flag("autonomy_rebuild_breaker"))  # we cannot say that nothing changed

    def test_a_failed_converge_trips_the_breaker(self):
        r = Rig(script={"aiops-rebuild-converge": [("error", [result_line(ok=False)])]})
        final = r.run()
        self.assertEqual(final["state"], "failed")
        self.assertIn("converge", final["result"]["why"])
        self.assertTrue(r.eng.flag("autonomy_rebuild_breaker"))
        self.assertEqual(r.verify.calls, [])  # no verify after a failed converge

    def test_a_converge_that_reports_not_ok_trips_the_breaker(self):
        r = Rig(script={"aiops-rebuild-converge": [("success", [result_line(ok=False)])]})
        self.assertEqual(r.run()["state"], "failed")
        self.assertTrue(r.eng.flag("autonomy_rebuild_breaker"))

    def test_a_failed_verify_is_verify_failed_and_trips_the_breaker(self):
        r = Rig()
        r.verify.fail = ["zabbix-agent2-active"]
        final = r.run()
        self.assertEqual(final["state"], "verify_failed")
        self.assertIn("zabbix-agent2-active", final["result"]["why"])
        self.assertTrue(r.eng.flag("autonomy_rebuild_breaker"))
        self.assertEqual(r.run_row(final["id"])["outcome"], "verify_failed")

    def test_an_unreadable_verify_is_a_failure_never_a_pass(self):
        r = Rig()
        r.verify.status = "error"
        self.assertEqual(r.run()["state"], "verify_failed")

    def test_the_kill_switch_blocks_starting_but_a_started_apply_finishes_its_converge(self):
        r = Rig()
        p = r.propose()
        approve(r.eng, p["id"])
        r.flag("kill_switch")
        self.assertEqual(r.eng.execute(p["id"])["state"], "cancelled")
        self.assertEqual(r.runner.ops(), ["plan"])
        r = Rig()
        r.runner.apply_hook = lambda: r.flag("kill_switch")  # engaged while the apply runs
        final = r.run()
        self.assertEqual(final["state"], "succeeded")  # a half-built guest is worse than a finished one
        self.assertEqual(len(r.sem.started), 1)

    def test_maintenance_and_one_at_a_time(self):
        r = Rig()
        r.flag("maintenance")
        self.assertEqual(self.refused(r, 409).detail["eligibility"]["reason"], "maintenance")
        r = Rig()
        a = r.propose("canary-1")
        approve(r.eng, a["id"])  # approved, not yet running: still in flight
        e = self.refused(r, 409, target="canary-2")
        self.assertEqual(e.detail["eligibility"]["reason"], "rebuild-in-flight")
        with r.eng.lock:  # and if two ever raced to start, the second is cancelled at the door
            r.db.execute("UPDATE proposals SET state='running' WHERE id=?", (a["id"],))
            r.db.execute("INSERT INTO proposals(id, source, action_id, params_json, params_hash, tier, target, reason, state, created_at, expires_at, decided_at, decided_by) "
                         "VALUES (50, 'chat', 'rebuild-guest', '{}', 'x', 'T1', 'canary-3', 'second', 'approved', 1, 9999999999, 1, ?)", (OP,))
            r.db.execute("INSERT INTO rebuild_runs(proposal_id, target, guest_class, mode, plan_id, plan_created_at, plan_expires_at, stage) "
                         "VALUES (50, 'canary-3', 'canary', 'approval', ?, 0, 9999999999, 'planned')", ("e" * 64,))
        self.assertEqual(r.eng.execute(50)["state"], "cancelled")

    def test_a_second_rebuild_of_the_same_guest_within_a_day_is_escalated(self):
        r = Rig()
        self.assertEqual(r.run()["state"], "succeeded")
        e = self.refused(r, 422)
        self.assertEqual(e.detail["eligibility"]["reason"], "rate-target-day")

    def test_the_fleet_cap(self):
        r = Rig(script={"aiops-rebuild-converge": [CONVERGE, CONVERGE]})
        for t in ("canary-1", "canary-2"):
            self.assertEqual(r.run(t)["state"], "succeeded")
        e = self.refused(r, 422, target="canary-3")
        self.assertEqual(e.detail["eligibility"]["reason"], "rate-class-day")

    def test_a_guest_that_healed_before_the_approval_is_skipped_not_rebuilt(self):
        r = Rig()
        p = r.propose()
        approve(r.eng, p["id"])
        r.facts.f.update(guest_state="running", probe_ok=True)  # it came back by itself
        final = r.eng.execute(p["id"])
        self.assertEqual(final["state"], "skipped")
        self.assertEqual(r.runner.ops(), ["plan"])

    def test_a_worker_rebuild_needs_the_manifest_and_runs_drain_before_the_apply(self):
        r = Rig(script={"aiops-rebuild-worker": [("success", [result_line(ok=True)]), ("success", [result_line(ok=True)])]})
        r.facts.f["guest_state"] = "missing"
        r.facts.manifest = {"blocks": True, "reasons": ["single-instance data and no PBS backup"], "summary": "x"}
        with self.assertRaises(actions.Refused) as cm:
            r.propose("einherjar-urd", action="rebuild-worker")
        self.assertIn("manifest blocks", cm.exception.message)
        r.facts.manifest = {"blocks": False, "reasons": [], "summary": "Data on einherjar-urd if rebuilt:\n- no local-path volumes\nManifest clean."}
        p = r.propose("einherjar-urd", action="rebuild-worker")
        self.assertIn("Manifest clean", p["rebuild"]["manifest"])
        approve(r.eng, p["id"])
        final = r.eng.execute(p["id"])
        self.assertEqual(final["state"], "succeeded", final["result"])
        self.assertEqual([s[1].get("phase") for s in r.sem.started], ["drain", "converge"])
        order = [s["step"] for s in final["result"]["steps"]]
        self.assertEqual(order, ["drain", "apply", "converge", "verify"])
        self.assertEqual(r.runner.ops(), ["plan", "apply"])  # drain ran BEFORE the apply (one semaphore task, then the runner)

    def test_a_worker_whose_manifest_cannot_be_read_is_never_proposed(self):
        r = Rig()
        r.facts.manifest = None
        with self.assertRaises(actions.Refused):
            r.propose("einherjar-verd", action="rebuild-worker")


class Recovery(unittest.TestCase):
    def crashed(self, rig, pid):
        with self.assertRaises(KeyboardInterrupt):
            rig.eng.execute(pid)
        return pid

    def test_restart_after_the_apply_resumes_at_the_converge_without_a_second_apply(self):
        r = Rig()
        p = r.propose()
        approve(r.eng, p["id"])
        r.sem.crash = True
        self.crashed(r, p["id"])
        self.assertEqual(r.run_row(p["id"])["stage"], "converge")
        r2 = Rig(prior=r)
        self.assertEqual(r2.eng._resumable, [p["id"]])
        self.assertEqual(r2.eng.get(p["id"])["state"], "running")  # NOT failed by the generic restart rule
        r.sem.crash = False
        out = r2.eng.resume_rebuilds()
        self.assertEqual(out[0]["state"], "succeeded", out[0]["result"])
        self.assertEqual(r.runner.ops(), ["plan", "apply"])  # never replayed
        self.assertEqual(len(r.sem.started), 1)  # re-attached to the task it had started, did not start another
        self.assertEqual(len(r.verify.calls), 1)

    def test_restart_inside_the_verify_re_runs_only_the_verify(self):
        r = Rig()
        p = r.propose()
        approve(r.eng, p["id"])
        r.verify.crash = True
        self.crashed(r, p["id"])
        r.verify.crash = False
        r2 = Rig(prior=r)
        out = r2.eng.resume_rebuilds()
        self.assertEqual(out[0]["state"], "succeeded")
        self.assertEqual(r.runner.ops(), ["plan", "apply"])
        self.assertEqual(len(r.sem.started), 1)

    def test_restart_during_the_apply_asks_the_runner_and_continues_if_it_finished(self):
        r = Rig()
        p = r.propose()
        approve(r.eng, p["id"])
        r.runner.apply_crash_after = True
        self.crashed(r, p["id"])
        r.runner.apply_crash_after = False
        r.runner.busy_polls = 2  # the runner reports busy for a moment first
        r2 = Rig(prior=r)
        out = r2.eng.resume_rebuilds()
        self.assertEqual(out[0]["state"], "succeeded", out[0]["result"])
        self.assertEqual(r.runner.ops().count("apply"), 1)  # asked (status), never re-sent
        self.assertIn("status", r.runner.ops())

    def test_restart_during_an_apply_the_runner_cannot_confirm_stops_for_a_human(self):
        r = Rig()
        p = r.propose()
        approve(r.eng, p["id"])
        r.runner.apply_crash = True
        self.crashed(r, p["id"])
        r.runner.apply_crash = False
        r2 = Rig(prior=r)
        out = r2.eng.resume_rebuilds()
        self.assertEqual(out[0]["state"], "failed")
        self.assertIn("a human must look", out[0]["result"]["why"])
        self.assertEqual(r.runner.ops().count("apply"), 1)  # only the original attempt: no blind replay after the restart
        self.assertTrue(r.eng.flag("autonomy_rebuild_breaker"))
        self.assertEqual(r.sem.started, [])

    def test_restart_before_the_apply_changed_nothing_and_just_ends_the_proposal(self):
        r = Rig()
        p = r.propose()
        approve(r.eng, p["id"])
        r.db.execute("UPDATE proposals SET state='running' WHERE id=?", (p["id"],))
        r.db.execute("UPDATE rebuild_runs SET stage='planned' WHERE proposal_id=?", (p["id"],))
        r2 = Rig(prior=r)
        self.assertEqual(r2.eng._resumable, [])
        v = r2.eng.get(p["id"])
        self.assertEqual(v["state"], "failed")
        self.assertTrue(v["result"]["nothing_changed"])
        self.assertFalse(r.eng.flag("autonomy_rebuild_breaker"))

    def test_the_kill_switch_does_not_stop_a_resume(self):
        r = Rig()
        p = r.propose()
        approve(r.eng, p["id"])
        r.sem.crash = True
        self.crashed(r, p["id"])
        r.sem.crash = False
        r.flag("kill_switch")
        r2 = Rig(prior=r)
        self.assertEqual(r2.eng.resume_rebuilds()[0]["state"], "succeeded")


class Autonomy(unittest.TestCase):
    def propose_auto(self, r, target="canary-2", diag=DIAG_DEAD, **kw):
        p = r.propose(target, source=kw.pop("source", "diagnosis"), **kw)
        out = r.eng.consider_auto(p["id"], diag)
        settle()
        return p["id"], out

    def test_every_flag_defaults_off(self):
        r = Rig()
        for f in ("autonomy_rebuild", "autonomy_rebuild_breaker", "autonomy", "kill_switch", "maintenance"):
            self.assertFalse(r.eng.flags()[f]["value"], f)
        self.assertFalse(SHIPPED.rebuild.autonomy_rebuild_default)
        self.assertFalse(any(p.enabled for p in SHIPPED.rebuild.policies.values()))
        for n in REBUILD_ACTIONS:  # rebuild-worker (stage C) stays planned; the canary-stage four (rebuild-plan is runner-only) are applied
            sem = DATA["actions"][n]["semaphore"]
            want = (False, True) if n == "rebuild-worker" else (True, None)
            self.assertEqual((sem["applied"], sem.get("planned")), want, n)


    def test_the_master_switch_is_operator_only_and_the_system_never_turns_it_on(self):
        r = Rig()
        for who, system in (("222222222222222222", False), ("system", True)):
            with self.assertRaises(actions.Refused) as cm:
                r.eng.set_flag("autonomy_rebuild", True, by=who, system=system)
            self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(actions.Refused):
            r.eng.set_flag("autonomy_rebuild_breaker", True, by=OP)  # an operator can re-arm it, never trip it
        r.flag("autonomy_rebuild")
        self.assertTrue(r.eng.flag("autonomy_rebuild"))
        self.assertFalse(r.eng.flag("autonomy"))  # 10f's switch is separate

    def test_10fs_switch_does_not_enable_rebuilds(self):
        r = Rig(policies=("rebuild-dead-canary",))
        r.flag("autonomy")
        pid, out = self.propose_auto(r)
        self.assertEqual(out, {"auto": False, "reason": "autonomy-rebuild-off"})
        self.assertEqual(r.eng.get(pid)["state"], "pending")
        self.assertEqual(r.runner.ops(), ["plan"])

    def test_a_dead_canary_is_rebuilt_unattended_when_the_switch_is_on(self):
        r = Rig(policies=("rebuild-dead-canary",))
        r.flag("autonomy_rebuild")
        pid, out = self.propose_auto(r)
        self.assertEqual(out, {"auto": True, "policy": "rebuild-dead-canary"})
        v = r.eng.get(pid)
        self.assertEqual((v["state"], v["decided_by"]), ("succeeded", "auto:rebuild-dead-canary"), v["result"])
        self.assertEqual(r.runner.ops(), ["plan", "apply"])
        self.assertEqual(r.run_row(pid)["mode"], "auto")
        self.assertIn("proposal_auto_approved", [a["event"] for a in r.audit])

    def test_the_gates_in_order(self):
        cases = (("kill-switch", lambda r: r.flag("kill_switch")), ("maintenance", lambda r: r.flag("maintenance")))
        for reason, setup in cases:
            r = Rig(policies=("rebuild-dead-canary",))
            r.flag("autonomy_rebuild")
            r.propose()  # eligibility at propose time (approval mode) ignores the switches except the hard ones
            r.eng.db.execute("DELETE FROM rebuild_runs")
            r.eng.db.execute("DELETE FROM proposals")
            p = r.propose()
            setup(r)
            out = r.eng.consider_auto(p["id"], DIAG_DEAD)
            self.assertFalse(out["auto"], reason)
            self.assertEqual(out["reason"], reason)
        r = Rig(policies=("rebuild-dead-canary",))
        r.flag("autonomy_rebuild")
        for diag, reason in (({**DIAG_DEAD, "layer": "network"}, "layer"), ({**DIAG_DEAD, "confidence": "low"}, "confidence"),
                             ({**DIAG_DEAD, "runbook_id": "RB-OTHER"}, "runbook-mismatch")):
            r.eng.db.execute("DELETE FROM rebuild_runs")
            r.eng.db.execute("DELETE FROM proposals")
            p = r.propose()
            self.assertEqual(r.eng.consider_auto(p["id"], diag), {"auto": False, "reason": reason})
        r.eng.db.execute("DELETE FROM rebuild_runs")
        r.eng.db.execute("DELETE FROM proposals")
        p = r.propose(source="chat")
        self.assertEqual(r.eng.consider_auto(p["id"], DIAG_DEAD)["reason"], "not-a-diagnosis")

    def test_a_replay_proposal_can_never_qualify_or_be_decided(self):
        r = Rig(policies=("rebuild-dead-canary",))
        r.flag("autonomy_rebuild")
        p = r.propose(replay=True)
        self.assertEqual(r.eng.consider_auto(p["id"], DIAG_DEAD)["reason"], "replay")
        self.assertEqual(r.runner.requests, [])  # a replay never reaches the runner
        with self.assertRaises(actions.Refused):
            r.eng.decide(p["id"], "approve", by=OP, run=False)

    def test_the_guest_dead_and_guest_broken_prechecks_read_reality(self):
        r = Rig(policies=("rebuild-broken-canary",))
        r.flag("autonomy_rebuild")
        pid, out = self.propose_auto(r, diag=DIAG_BROKEN)  # the guest is MISSING (dead), the policy is for broken
        self.assertEqual(out, {"auto": False, "reason": "precheck-mismatch"})
        r = Rig(policies=("rebuild-dead-canary",))
        r.flag("autonomy_rebuild")
        pid, out = self.propose_auto(r)  # a dead guest and a dead policy
        self.assertTrue(out["auto"])
        # a broken guest: running and reachable, the restart breaker tripped for it within a day
        r = Rig(policies=("rebuild-broken-canary", "rebuild-dead-canary"), facts=FakeFacts(guest_state="running", probe_ok=True))
        r.flag("autonomy_rebuild")
        with self.assertRaises(actions.Refused):
            r.propose()  # running and healthy: not eligible at all
        r.db.execute("INSERT INTO proposals(id, source, action_id, params_json, params_hash, tier, target, reason, state, created_at, expires_at) "
                     "VALUES (70, 'x', 'restart-unit', '{}', 'h', 'T1', 'canary-2', 'r', 'failed', 1, 2)")
        r.db.execute("INSERT INTO proposal_events(proposal_id, ts, kind) VALUES (70, ?, 'breaker_tripped')", (int(r.clock()),))
        pid, out = self.propose_auto(r, diag=DIAG_BROKEN)
        self.assertEqual(out, {"auto": True, "policy": "rebuild-broken-canary"})

    def test_an_open_breaker_stops_the_unattended_path(self):
        r = Rig(policies=("rebuild-dead-canary",))
        r.flag("autonomy_rebuild")
        r.runner.apply_error = "terraform-failed: boom"
        pid, out = self.propose_auto(r)
        self.assertTrue(out["auto"])
        self.assertEqual(r.eng.get(pid)["state"], "failed")
        self.assertTrue(r.eng.flag("autonomy_rebuild_breaker"))
        with self.assertRaises(actions.Refused) as cm:  # even proposing is refused now
            r.propose("canary-1")
        self.assertIn("breaker-open", cm.exception.message)

    def test_never_a_non_canary_unattended_even_with_every_policy_forced_on(self):
        for host in [h for c in DATA["rebuild"]["classes"].values() for h in c["hosts"] if not h.startswith("canary-")]:
            r = Rig(force_policies=True)
            r.flag("autonomy_rebuild")
            r.facts.manifest = {"blocks": False, "reasons": [], "summary": "Manifest clean."}
            cls = SHIPPED.rebuild.class_of(host)
            action = "rebuild-worker" if cls.kind == "k8s-worker" else "rebuild-guest"
            try:
                p = r.propose(host, action=action)
            except actions.Refused:
                continue  # not even proposable: stronger still
            out = r.eng.consider_auto(p["id"], DIAG_DEAD)
            settle()
            self.assertFalse(out["auto"], (host, out))
            self.assertEqual(r.eng.get(p["id"])["state"], "pending", host)
            self.assertEqual(r.runner.ops().count("apply"), 0, host)

    def test_a_replica_needs_two_approved_clean_rebuilds_before_it_may_run_unattended(self):
        d = live_data(("rebuild-dead-replica",))
        r = Rig(data=d)
        r.flag("autonomy_rebuild")
        pid, out = self.propose_auto(r, "mimir")
        self.assertEqual((out["auto"], out["reason"]), (False, "approvals-first"))

    def test_the_start_guest_rung_is_gated_by_the_guest_dead_precheck(self):
        r = Rig(policies=("start-dead-canary",), facts=FakeFacts(guest_state="stopped"),
                script={"aiops-start-guest": [START_OK]})
        r.flag("autonomy_rebuild")
        p = r.eng.propose(action_id="start-guest", params={"target": "canary-2"}, reason="canary-2 is stopped", source="diagnosis", incident_id=1)
        out = r.eng.consider_auto(p["id"], DIAG_DEAD)
        settle()
        self.assertTrue(out["auto"], out)
        self.assertEqual(r.eng.get(p["id"])["state"], "succeeded")
        # a guest that is already running is never "started" by policy
        r = Rig(policies=("start-dead-canary",), facts=FakeFacts(guest_state="running"), script={"aiops-start-guest": [START_OK]})
        r.flag("autonomy_rebuild")
        p = r.eng.propose(action_id="start-guest", params={"target": "canary-2"}, reason="canary-2 looked stopped", source="diagnosis", incident_id=1)
        self.assertEqual(r.eng.consider_auto(p["id"], DIAG_DEAD), {"auto": False, "reason": "precheck-skip"})
        self.assertEqual(r.sem.started, [])


class StatusAndReport(unittest.TestCase):
    def test_summary_and_report_carry_the_rebuild_state(self):
        r = Rig()
        self.assertEqual(r.run()["state"], "succeeded")
        s = r.eng.summary()
        self.assertEqual((s["rebuild"]["rebuilding"], s["rebuild"]["autonomy_rebuild"], s["rebuild"]["breaker"]), ([], False, False))
        rep = r.eng.report(7)
        self.assertEqual(rep["rebuild"]["runs"], 1)
        self.assertEqual(rep["rebuild"]["by_outcome"], {"succeeded": 1})
        self.assertEqual(rep["rebuild"]["by_target"], {"canary-2": 1})
        self.assertEqual(rep["rebuild"]["unattended"], 0)
        self.assertFalse(rep["flags"]["autonomy_rebuild"])
        r2 = Rig()
        r2.runner.apply_error = "terraform-failed: x"
        r2.run()
        self.assertEqual(r2.eng.report(7)["rebuild"]["breaker_trips"], 1)


class RunnerClientTests(unittest.TestCase):
    def test_request_shapes_are_exactly_the_protocol(self):
        seen = []

        def t(req, timeout):
            seen.append(req)
            return {"v": 1, "request_id": req.get("request_id"), "ok": True, "busy": False, "last": {}}

        c = rebuild_exec.RunnerClient(transport=t)
        c.plan("canary", "canary-2")
        c.apply("a" * 64)
        c.status()
        self.assertEqual(set(seen[0]), {"v", "request_id", "op", "class", "target"})
        self.assertEqual((seen[0]["v"], seen[0]["op"], seen[0]["class"], seen[0]["target"]), (1, "plan", "canary", "canary-2"))
        self.assertEqual(set(seen[1]), {"v", "request_id", "op", "plan_id"})
        self.assertEqual(seen[2], {"v": 1, "op": "status"})
        self.assertNotEqual(seen[0]["request_id"], seen[1]["request_id"])

    def test_errors_map_to_codes(self):
        def mk(resp):
            return rebuild_exec.RunnerClient(transport=lambda req, timeout: resp)

        with self.assertRaises(rebuild_exec.RunnerError) as cm:
            mk({"ok": False, "error": "origin-moved: main advanced"}).apply("a" * 64)
        self.assertEqual((cm.exception.code, cm.exception.message), ("origin-moved", "main advanced"))
        with self.assertRaises(rebuild_exec.RunnerError) as cm:
            mk({"ok": False, "problems": ["two changes"]}).plan("canary", "canary-2")
        self.assertEqual((cm.exception.code, cm.exception.problems), ("plan-rejected", ["two changes"]))
        for bad in ({"nope": 1}, "not a dict", {"ok": "yes"}):
            with self.assertRaises(rebuild_exec.RunnerError) as cm:
                mk(bad).status()
            self.assertEqual(cm.exception.code, "bad-response")
        with self.assertRaises(rebuild_exec.RunnerError) as cm:  # an answer to some other request
            mk({"ok": True, "request_id": "someone-else"}).plan("canary", "canary-2")
        self.assertEqual(cm.exception.code, "bad-response")

    def test_the_unix_socket_transport_round_trips_one_json_line(self):
        d = tempfile.mkdtemp(prefix="rb")
        path = str(Path(d) / "r.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        got = []

        def serve():
            conn, _ = srv.accept()
            buf = b""
            while b"\n" not in buf:
                buf += conn.recv(4096)
            req = json.loads(buf)
            got.append(req)
            conn.sendall((json.dumps({"v": 1, "request_id": req["request_id"], "ok": True, "busy": False, "last": {}}) + "\n").encode())
            conn.close()

        th = threading.Thread(target=serve, daemon=True)
        th.start()
        try:
            resp = rebuild_exec.RunnerClient(socket_path=path).plan("canary", "canary-2")
            th.join(5)
            self.assertTrue(resp["ok"])
            self.assertEqual(got[0]["op"], "plan")
        finally:
            srv.close()
        with self.assertRaises(rebuild_exec.RunnerError) as cm:  # nobody listening
            rebuild_exec.RunnerClient(socket_path=path).status()
        self.assertEqual(cm.exception.code, "runner-unreachable")


class ChecklistAndFacts(unittest.TestCase):
    def test_the_checklist_is_strict(self):
        conds = ("a", "b", "c")
        self.assertTrue(rebuild_exec.run_checklist(conds, {"a": True, "b": "true", "c": True})["ok"])
        r = rebuild_exec.run_checklist(conds, {"a": True, "b": False})
        self.assertEqual((r["ok"], r["failed"], r["missing"]), (False, ["b", "c"], ["c"]))
        self.assertFalse(rebuild_exec.run_checklist(conds, {})["ok"])
        self.assertFalse(rebuild_exec.run_checklist((), {"a": True})["ok"])  # nothing checked is not a pass
        self.assertFalse(rebuild_exec.run_checklist(("a",), {"a": "yes"})["ok"])
        self.assertFalse(rebuild_exec.run_checklist(("a",), None)["ok"])

    def test_reader_facts_are_conservative(self):
        cls = SHIPPED.rebuild.classes["canary"]
        online = {"cluster_view": {"status": "online"}}

        def reader(table):
            def f(name, args):
                v = table[name]
                if isinstance(v, Exception):
                    raise v
                return v
            return f

        guests = {"guests": [{"vmid": 1191, "status": "running"}, {"vmid": 1190, "status": "running"}]}
        zbx_ok = {"host": {"interfaces": [{"type": 1, "available": "1"}]}}
        ok = rebuild_exec.ReaderFacts(reader({"pve.node_status": online, "pve.guests": guests, "reach.tcp": {"open": True}, "zabbix.host": zbx_ok}))
        f = ok.facts("canary-2", cls)
        self.assertEqual((f["guest_state"], f["node_online"], f["node_guests_ok"], f["probe_ok"], f["agent_silent"]), ("running", True, True, True, False))
        down = rebuild_exec.ReaderFacts(reader({"pve.node_status": {"cluster_view": {"status": "offline"}}, "pve.guests": guests}))
        f = down.facts("canary-2", cls)
        self.assertEqual((f["node_online"], f["guest_state"]), (False, "unknown"))  # a dead node never reads as "guest missing"
        unreadable = rebuild_exec.ReaderFacts(reader({"pve.node_status": RuntimeError("x")}))
        self.assertEqual(unreadable.facts("canary-2", cls)["node_online"], False)
        gone = rebuild_exec.ReaderFacts(reader({"pve.node_status": online, "pve.guests": {"guests": [{"vmid": 1190, "status": "running"}]},
                                                "reach.tcp": {"open": False}, "zabbix.host": zbx_ok}))
        self.assertEqual(gone.facts("canary-2", cls)["guest_state"], "missing")
        silent = rebuild_exec.ReaderFacts(reader({"pve.node_status": online, "pve.guests": guests, "reach.tcp": {"open": False},
                                                  "zabbix.host": {"host": {"interfaces": [{"type": 1, "available": "2"}]}}}))
        self.assertTrue(silent.facts("canary-2", cls)["agent_silent"])
        # an AdGuard replica: unknown master is treated as the master; peers read on port 53
        rep = SHIPPED.rebuild.classes["adguard-replica"]
        g2 = {"guests": [{"vmid": 1111, "status": "running"}]}
        peers = rebuild_exec.ReaderFacts(reader({"pve.node_status": online, "pve.guests": g2, "reach.tcp": {"open": True}, "zabbix.host": zbx_ok}))
        f = peers.facts("mimir", rep)
        self.assertEqual((f["is_leader"], f["peers_healthy"]), (True, True))
        self.assertIsNone(peers.worker_manifest("einherjar-urd"))  # no PV read tool yet: a worker rebuild stays blocked


class BotText(unittest.TestCase):
    def proposal(self, **over):
        r = Rig()
        p = r.propose()
        return {**p, **over}

    def test_the_card_shows_plan_destruction_manifest_and_backup_age(self):
        p = self.proposal()
        p["rebuild"] = {**p["rebuild"], "manifest": "Data on x if rebuilt:\n- vault/data 10Gi: replicated\nManifest clean.", "backup_age_hours": 5.0}
        c = logic.card(p)
        fields = {f[0]: f[1] for f in c["fields"]}
        self.assertIn("replace", fields["Plan"])
        self.assertIn("canary-2", fields["Plan"])
        self.assertIn("DESTROYS", fields["What gets destroyed"])
        self.assertIn("```", fields["Data-loss manifest"])
        self.assertIn("vault/data", fields["Data-loss manifest"])
        self.assertEqual(fields["Last backup"], "5 h ago")
        self.assertTrue(c["buttons"])

    def test_hostile_text_in_the_plan_cannot_ping_anyone(self):
        p = self.proposal()
        p["rebuild"] = {**p["rebuild"], "destroys": "@everyone <@123456789012345678> destroys it", "manifest": "@here\n@everyone"}
        c = logic.card(p)
        blob = json.dumps(c["fields"])
        self.assertNotIn("@everyone", blob.replace("@​", ""))
        self.assertNotIn("<@123456789012345678>", blob)

    def test_a_running_rebuild_card_says_which_step(self):
        p = self.proposal(state="running")
        p["rebuild"] = {**p["rebuild"], "stage": "converge", "done": ["apply"]}
        self.assertIn("Rebuilding: step `converge`", logic.card(p)["description"])

    def test_the_announcement_has_timings_and_the_failure_wording(self):
        r = Rig()
        done = r.run()
        text = logic.result_summary(done)
        self.assertIn("succeeded and verified", text)
        self.assertIn("apply: success (42 s)", text)
        self.assertIn("Total", text)
        r2 = Rig()
        r2.runner.apply_error = "terraform-failed: x"
        bad = logic.result_summary(r2.run())
        self.assertIn("FAILED", bad)
        self.assertIn("half-built", bad)
        self.assertIn("/aiops rebuild reset-breaker", bad)
        r3 = Rig()
        r3.runner.apply_error = "origin-moved: x"
        self.assertIn("Nothing was changed", logic.result_summary(r3.run()))

    def test_the_breaker_notice_for_a_rebuild(self):
        r = Rig()
        r.runner.apply_error = "terraform-failed: x"
        r.run()
        events = r.eng.feed()["events"]
        plan = logic.plan_feed(events, logic.State(Path("/nonexistent/state.json")))
        notice = [a for a in plan if a.kind == "breaker"]
        self.assertEqual(len(notice), 1)
        text = logic.breaker_notice(notice[0].proposal)
        self.assertIn("Rebuild circuit breaker TRIPPED", text)
        self.assertIn("/aiops rebuild reset-breaker", text)

    def test_status_and_report_lines(self):
        r = Rig()
        r.flag("autonomy_rebuild")
        seen = []
        r.verify.check = lambda pid, t, c, conds: (seen.append(logic.format_status({"actions": r.eng.summary(), "open_proposals": r.eng.list()})),
                                                   {"status": "success", "checks": {x: True for x in conds}})[1]
        r.run()
        self.assertIn("Rebuild autonomy: ON", seen[0])
        self.assertIn("REBUILDING", seen[0])
        self.assertIn("`canary-2`", seen[0])
        rep = logic.format_report(r.eng.report(7))
        self.assertIn("Rebuilds, same period:** 1 run(s)", rep)
        self.assertIn("succeeded 1", rep)
        r.runner.apply_error = "terraform-failed: x"
        r.db.execute("DELETE FROM rebuild_runs")
        r.run("canary-1")
        st = logic.format_status({"actions": r.eng.summary(), "open_proposals": []})
        self.assertIn("REBUILD BREAKER TRIPPED", st)


class VerifyMapping(unittest.TestCase):
    """The verify PLAYBOOK emits flat fields (ssh_as_ansible, ...); the registry names conditions with hyphens. Found at
    integration time: without this mapping every real verify would have been read as 'missing' and failed."""
    CONDS = tuple(DATA["rebuild"]["classes"]["canary"]["post_conditions"])
    GOOD = {"ssh_as_ansible": True, "vlagent_active": True, "zabbix_agent2_active": "True", "zabbix_group_canary": True,
            "canary_template_linked": True, "alert_cleared": True}

    def test_the_playbooks_flat_fields_satisfy_every_registry_condition(self):
        checks = rebuild_exec.checks_from_result(self.GOOD, self.CONDS)
        verdict = rebuild_exec.run_checklist(self.CONDS, checks)
        self.assertTrue(verdict["ok"], verdict)
        self.assertEqual(verdict["checked"], len(self.CONDS))

    def test_one_false_field_never_passes(self):
        for field in self.GOOD:
            checks = rebuild_exec.checks_from_result({**self.GOOD, field: False}, self.CONDS)
            self.assertFalse(rebuild_exec.run_checklist(self.CONDS, checks)["ok"], field)

    def test_a_field_the_playbook_did_not_report_is_missing_not_passed(self):
        gone = {k: v for k, v in self.GOOD.items() if k != "alert_cleared"}
        verdict = rebuild_exec.run_checklist(self.CONDS, rebuild_exec.checks_from_result(gone, self.CONDS))
        self.assertFalse(verdict["ok"])
        self.assertIn("alert-cleared", verdict["missing"])
        half = {k: v for k, v in self.GOOD.items() if k != "zabbix_agent2_active"}
        self.assertFalse(rebuild_exec.run_checklist(self.CONDS, rebuild_exec.checks_from_result(half, self.CONDS))["ok"])

    def test_an_explicit_checks_dict_wins(self):
        self.assertEqual(rebuild_exec.checks_from_result({"checks": {"x": True}}, self.CONDS), {"x": True})


if __name__ == "__main__":
    unittest.main()
