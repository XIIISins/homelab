"""Phase 10e1: proposals, operator approval and the executor (aiops/toolbelt/actions.py).

Pure logic against in-memory SQLite, a fake clock and a scripted fake Semaphore: every rule runs without a socket.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))

import actions  # noqa: E402

REGISTRY = actions.Registry.from_file(REPO / "aiops" / "actions.yml")
OP = "111111111111111111"


class Clock:
    def __init__(self, t=1_790_000_000):
        self.t = t

    def __call__(self):
        return self.t


def result_line(**d) -> str:
    """How ansible's debug callback prints it inside a task output: the JSON is escaped inside a JSON string."""
    return '    "msg": "AIOPS_RESULT ' + json.dumps(d).replace('"', '\\"') + '"'


class FakeSemaphore:
    """Scripted by template name: {template: [(status, output lines), ...]} consumed per started task."""

    def __init__(self, script=None):
        self.script = script or {}
        self.tasks: dict[int, dict] = {}
        self.started: list[tuple] = []
        self.polls_until_done = 1

    def template_id(self, name):
        if name not in self.script:
            raise actions.Refused(404, f"no template {name}")
        return abs(hash(name)) % 1000

    def start(self, template_id, environment, fields):
        name = next(n for n in self.script if abs(hash(n)) % 1000 == template_id)
        status, lines = self.script[name].pop(0)
        tid = len(self.tasks) + 1
        self.tasks[tid] = {"status": status, "lines": lines, "polls": 0, "template": name}
        self.started.append((name, dict(environment), dict(fields)))
        return tid

    def status(self, task_id):
        t = self.tasks[task_id]
        t["polls"] += 1
        return "running" if t["polls"] <= self.polls_until_done and t["status"] != "hang" else (t["status"] if t["status"] != "hang" else "running")

    def output(self, task_id):
        return self.tasks[task_id]["lines"]


def make(sem=None, clock=None, **cfg):
    clock = clock or Clock()
    db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE counters (name TEXT NOT NULL, day TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (name, day))")
    audit: list[dict] = []

    def bump(name):
        db.execute("INSERT INTO counters(name, day, n) VALUES (?, 'd', 1) ON CONFLICT(name, day) DO UPDATE SET n=n+1", (name,))
        return counter(name)

    def counter(name):
        r = db.execute("SELECT n FROM counters WHERE name=?", (name,)).fetchone()
        return r["n"] if r else 0

    ac = actions.ActionConfig(operators=frozenset({OP}), semaphore=sem, sleep=lambda s: setattr(clock, "t", clock.t + s), **cfg)
    eng = actions.Engine(db, threading.RLock(), clock, lambda event, **kw: audit.append({"event": event, **kw}), REGISTRY, ac, bump, counter)
    return eng, clock, audit, db


RESTART = {"target_host": "canary-1", "unit": "vlagent.service"}


def approve(eng, pid, **kw):
    p = eng._view(pid)
    return eng.decide(pid, "approve", by=OP, ref="x", params_hash_seen=p["params_hash"], run=False, **kw)


class RegistryTests(unittest.TestCase):
    def test_declared_params_only_and_patterns_are_full_matched(self):
        clean, probs = REGISTRY.validate_params("restart-unit", RESTART)
        self.assertEqual((clean, probs), (RESTART, []))
        _, probs = REGISTRY.validate_params("restart-unit", {**RESTART, "extra": "x"})
        self.assertTrue(any("not a declared parameter" in p for p in probs))
        _, probs = REGISTRY.validate_params("restart-unit", {"target_host": "canary-1"})
        self.assertTrue(any("missing required" in p for p in probs))
        _, probs = REGISTRY.validate_params("restart-unit", {"target_host": "canary-1;rm -rf /", "unit": "vlagent.service"})
        self.assertTrue(any("does not match" in p for p in probs))
        _, probs = REGISTRY.validate_params("restart-unit", {"target_host": "canary-1\n", "unit": "vlagent.service"})
        self.assertTrue(any("plain string" in p for p in probs))
        _, probs = REGISTRY.validate_params("replay-role", {"target_host": "canary-1", "role_tag": "k3s"})
        self.assertTrue(any("must be one of" in p for p in probs))
        _, probs = REGISTRY.validate_params("restart-unit", ["not", "a", "dict"])
        self.assertEqual(probs, ["params must be an object"])

    def test_guards(self):
        g = REGISTRY.guard
        self.assertEqual(g("restart-unit", RESTART), [])
        self.assertTrue(g("restart-unit", {"target_host": "gondul", "unit": "k3s.service"}))  # not T1
        self.assertTrue(g("restart-unit", {"target_host": "canary-1", "unit": "sshd.service"}))  # unit not allow-listed on that host
        self.assertTrue(g("restart-unit", {"target_host": "mimir", "unit": "vlagent.service"}))  # allow-listed on canaries, not mimir
        self.assertTrue(g("flux-reconcile", {"hr_name": "vault", "hr_namespace": "vault"}))  # deny-listed
        self.assertEqual(g("flux-reconcile", {"hr_name": "vmagent", "hr_namespace": "monitoring"}), [])
        self.assertTrue(g("flux-reconcile-reset", {"hr_name": "traefik", "hr_namespace": "traefik"}))  # not on the reset allow-list
        self.assertEqual(g("flux-reconcile-reset", {"hr_name": "vmagent", "hr_namespace": "monitoring"}), [])

    def test_an_unapplied_template_cannot_be_proposed(self):
        reg = actions.Registry({"host_tiers": {"T1": ["canary-1"]}, "actions": {
            "x": {"tier": "T1", "max_autonomy": "approval", "semaphore": {"template": "t", "playbook": "p", "applied": False},
                  "extra_vars": {}, "guard": {"summary": "s", "target_policy": "n/a"}, "verify": {"self": True, "expect": {"ok": True}},
                  "rollback": "none", "description": "d" * 12, "idempotent": True}}})
        self.assertTrue(any("not applied" in p for p in reg.guard("x", {})))

    def test_task_building_resolves_placeholders_and_fixed_vars(self):
        tpl, env, fields = REGISTRY.semaphore_task("replay-role-check", {"target_host": "canary-1", "role_tag": "vlagent"})
        self.assertEqual(tpl, "aiops-replay-role-check")
        self.assertEqual(fields, {"limit": "canary-1", "arguments": ["--check", "--diff", "--tags", "vlagent"]})
        _, env, _ = REGISTRY.semaphore_task("flux-reconcile-reset", {"hr_name": "vmagent", "hr_namespace": "monitoring"})
        self.assertIs(env["reset"], True)


class ParseTests(unittest.TestCase):
    def test_aiops_result_in_every_shape_ansible_prints_it(self):
        escaped = result_line(action="restart-unit", ok=True, active_state="active")
        self.assertEqual(actions.parse_output([escaped], "canary-1")["active_state"], "active")
        plain = 'AIOPS_RESULT {"action": "service-status", "ok": true, "active_state": "active"}'
        self.assertTrue(actions.parse_output([plain], None)["ok"])
        coloured = "\x1b[0;32m" + escaped  # Semaphore strips ANSI, but the parser must also cope with leftovers
        self.assertTrue(actions.parse_output([actions._ANSI.sub("", coloured)], None)["ok"])
        self.assertEqual(actions.parse_output(["nothing here"], None), {})

    def test_recap_actions_take_ok_and_changed_from_the_target_hosts_recap(self):
        lines = [result_line(action="replay-role-check", phase="complete", ok=True, check_mode=True),
                 "PLAY RECAP *********", "canary-1                   : ok=12   changed=3    unreachable=0    failed=0    skipped=1",
                 "canary-2                   : ok=1    changed=0    unreachable=0    failed=1"]
        r = actions.parse_output(lines, "canary-1", "recap")
        self.assertEqual((r["ok"], r["changed"], r["check_mode"]), (True, 3, True))
        bad = actions.parse_output(["canary-1 : ok=1 changed=0 unreachable=1 failed=0"], "canary-1", "recap")
        self.assertFalse(bad["ok"])
        self.assertTrue(actions.parse_output(["no recap"], "canary-1", "recap")["recap_missing"])

    def test_evaluate(self):
        self.assertEqual(actions.evaluate({"ok": True, "ready": "True"}, {"ok": True, "ready": True}), [])
        self.assertTrue(actions.evaluate({"ok": True}, {"ok": False}))
        self.assertTrue(actions.evaluate({"active_state": "active"}, {}))

    def test_redaction(self):
        self.assertNotIn("sk-ant-abcdefghijklmnop", actions.redact("key sk-ant-abcdefghijklmnop leaked"))


class ProposeTests(unittest.TestCase):
    def test_a_valid_proposal_is_stored_pending_and_bound_to_a_hash(self):
        eng, clock, audit, _ = make()
        p = eng.propose(action_id="restart-unit", params=RESTART, reason="vlagent stopped on canary-1", source="diagnosis", incident_id=7, thread_id="t1")
        self.assertEqual((p["state"], p["tier"], p["target"], p["incident_id"]), ("pending", "T1", "canary-1", 7))
        self.assertEqual(p["params_hash"], actions.params_hash("restart-unit", RESTART))
        self.assertEqual(p["expires_at"], clock.t + 1800)
        self.assertTrue(any(a["event"] == "proposal_created" for a in audit))

    def test_invalid_proposals_are_refused_with_the_problems(self):
        eng, *_ = make()
        for action, params, reason in (("nope", {}, "x" * 5), ("restart-unit", {"target_host": "gondul", "unit": "k3s.service"}, "abc"),
                                       ("restart-unit", RESTART, "x"), ("restart-unit", RESTART, "token sk-ant-abcdefghijklmnop here")):
            with self.assertRaises(actions.Refused) as cm:
                eng.propose(action_id=action, params=params, reason=reason, source="chat")
            self.assertIn(cm.exception.status, (400, 404, 422))

    def test_identical_open_proposals_are_deduplicated(self):
        eng, *_ = make()
        a = eng.propose(action_id="restart-unit", params=RESTART, reason="first reason", source="diagnosis", incident_id=1)
        b = eng.propose(action_id="restart-unit", params=RESTART, reason="second reason", source="chat", incident_id=1)
        self.assertEqual(a["id"], b["id"])
        self.assertTrue(b["duplicate"])

    def test_caps(self):
        eng, *_ = make(max_pending_per_incident=2, max_proposals_per_day=3)
        for host in ("canary-1", "canary-2"):
            eng.propose(action_id="restart-unit", params={"target_host": host, "unit": "vlagent.service"}, reason="stopped", source="diagnosis", incident_id=1)
        with self.assertRaises(actions.Refused) as cm:
            eng.propose(action_id="restart-unit", params={"target_host": "canary-3", "unit": "vlagent.service"}, reason="stopped", source="diagnosis", incident_id=1)
        self.assertEqual(cm.exception.status, 429)
        eng.propose(action_id="restart-unit", params={"target_host": "canary-3", "unit": "vlagent.service"}, reason="stopped", source="diagnosis", incident_id=2)
        with self.assertRaises(actions.Refused) as cm:
            eng.propose(action_id="restart-unit", params={"target_host": "mimir", "unit": "AdGuardHome.service"}, reason="stopped", source="diagnosis", incident_id=3)
        self.assertEqual(cm.exception.status, 429)

    def test_ttl_expires_a_pending_proposal(self):
        eng, clock, *_ = make(proposal_ttl=60)
        p = eng.propose(action_id="restart-unit", params=RESTART, reason="stopped", source="diagnosis")
        clock.t += 61
        self.assertEqual(eng.get(p["id"])["state"], "expired")
        with self.assertRaises(actions.Refused) as cm:
            eng.decide(p["id"], "approve", by=OP, run=False)
        self.assertEqual(cm.exception.status, 409)


class UnitNameTests(unittest.TestCase):
    def test_a_bare_unit_name_is_completed_but_the_allow_list_still_decides(self):
        eng, *_ = make()
        p = eng.propose(action_id="restart-unit", params={"target_host": "canary-1", "unit": "vlagent"}, reason="agent stopped", source="diagnosis", incident_id=1)
        self.assertEqual(p["params"]["unit"], "vlagent.service")
        with self.assertRaises(actions.Refused):  # sshd is not on the canary allow-list, with or without the suffix
            eng.propose(action_id="restart-unit", params={"target_host": "canary-1", "unit": "sshd"}, reason="agent stopped", source="diagnosis", incident_id=1)
        for bad in ("vlagent;reboot", "vl agent", "../x", "vlagent.timer"):
            with self.assertRaises(actions.Refused):
                eng.propose(action_id="restart-unit", params={"target_host": "canary-1", "unit": bad}, reason="agent stopped", source="diagnosis", incident_id=1)

    def test_other_actions_and_non_dict_params_are_left_alone(self):
        eng, *_ = make()
        self.assertEqual(eng._tidy_params("flux-reconcile", {"hr_name": "x", "hr_namespace": "y"}), {"hr_name": "x", "hr_namespace": "y"})
        self.assertEqual(eng._tidy_params("restart-unit", ["not", "a", "dict"]), ["not", "a", "dict"])
        self.assertEqual(eng._tidy_params("no-such-action", {"unit": "x"}), {"unit": "x"})


class NumberingTests(unittest.TestCase):
    def test_humans_read_1_2_3_within_a_conversation_while_ids_keep_counting(self):
        eng, *_ = make()
        eng.propose(action_id="restart-unit", params={"target_host": "canary-1", "unit": "vlagent.service"}, reason="earlier", source="chat", conversation_id=None, incident_id=7)
        got = [eng.propose(action_id="restart-unit", params={"target_host": h, "unit": "zabbix-agent2.service"}, reason="agent stopped", source="chat", conversation_id=5)
               for h in ("canary-1", "canary-2", "canary-3")]
        self.assertEqual([p["number"] for p in got], [1, 2, 3])
        self.assertEqual([p["id"] for p in got], [2, 3, 4])
        self.assertEqual(eng.get(got[2]["id"])["number"], 3)

    def test_without_a_conversation_or_incident_the_number_is_the_id(self):
        eng, *_ = make()
        p = eng.propose(action_id="restart-unit", params=RESTART, reason="agent stopped", source="diagnosis")
        self.assertEqual(p["number"], p["id"])


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.eng, self.clock, self.audit, _ = make()
        self.p = self.eng.propose(action_id="restart-unit", params=RESTART, reason="stopped", source="diagnosis", incident_id=1)

    def test_only_an_operator_may_decide(self):
        for who in ("222222222222222222", "", None, 111111111111111111):
            with self.assertRaises(actions.Refused) as cm:
                self.eng.decide(self.p["id"], "approve", by=who, run=False)
            self.assertEqual(cm.exception.status, 403)
        self.assertEqual(self.eng.get(self.p["id"])["state"], "pending")

    def test_approve_records_who_and_moves_to_approved(self):
        r = self.eng.decide(self.p["id"], "approve", by=OP, ref="interaction-9", params_hash_seen=self.p["params_hash"], run=False)
        self.assertEqual((r["state"], r["decided_by"]), ("approved", OP))
        with self.assertRaises(actions.Refused) as cm:  # one decision only
            self.eng.decide(self.p["id"], "reject", by=OP, run=False)
        self.assertEqual(cm.exception.status, 409)

    def test_the_hash_the_human_saw_must_match(self):
        with self.assertRaises(actions.Refused) as cm:
            self.eng.decide(self.p["id"], "approve", by=OP, params_hash_seen="0" * 16, run=False)
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.eng.get(self.p["id"])["state"], "pending")

    def test_reject(self):
        self.assertEqual(self.eng.decide(self.p["id"], "reject", by=OP, run=False)["state"], "rejected")

    def test_replay_proposals_can_never_be_decided(self):
        r = self.eng.propose(action_id="restart-unit", params={"target_host": "canary-2", "unit": "vlagent.service"}, reason="stopped", source="diagnosis", replay=True)
        with self.assertRaises(actions.Refused) as cm:
            self.eng.decide(r["id"], "approve", by=OP, run=False)
        self.assertEqual(cm.exception.status, 409)

    def test_kill_switch_blocks_approval_and_cancels_what_is_approved(self):
        approve(self.eng, self.p["id"])
        other = self.eng.propose(action_id="restart-unit", params={"target_host": "canary-2", "unit": "vlagent.service"}, reason="stopped", source="diagnosis")
        self.eng.set_flag("kill_switch", True, by=OP, reason="testing")
        self.assertEqual(self.eng.get(self.p["id"])["state"], "cancelled")
        with self.assertRaises(actions.Refused) as cm:
            self.eng.decide(other["id"], "approve", by=OP, run=False)
        self.assertEqual(cm.exception.status, 409)
        self.eng.set_flag("kill_switch", False, by=OP)
        self.assertEqual(approve(self.eng, other["id"])["state"], "approved")

    def test_flags_need_an_operator_and_a_known_name(self):
        with self.assertRaises(actions.Refused):
            self.eng.set_flag("kill_switch", True, by="999")
        with self.assertRaises(actions.Refused):
            self.eng.set_flag("self_destruct", True, by=OP)
        self.assertTrue(self.eng.set_flag("maintenance", True, by=OP)["value"])


class ExecuteTests(unittest.TestCase):
    def run_restart(self, script, **cfg):
        sem = FakeSemaphore(script)
        eng, clock, audit, _ = make(sem, **cfg)
        p = eng.propose(action_id="restart-unit", params=RESTART, reason="stopped", source="diagnosis", incident_id=1)
        approve(eng, p["id"])
        return eng.execute(p["id"]), sem, eng, audit

    def test_success_runs_the_template_with_declared_vars_then_verifies(self):
        out, sem, eng, audit = self.run_restart({
            "aiops-restart-unit": [("success", [result_line(action="restart-unit", ok=True, active_state="active")])],
            "aiops-service-status": [("success", [result_line(action="service-status", ok=True, active_state="active")])]})
        self.assertEqual(out["state"], "succeeded")
        self.assertEqual([s[0] for s in sem.started], ["aiops-restart-unit", "aiops-service-status"])
        self.assertEqual(sem.started[0][1], RESTART)  # exactly the declared vars, nothing else
        self.assertEqual(sem.started[1][1], RESTART)  # the verify action got {target_host}/{unit} substituted
        kinds = [e["kind"] for e in eng.feed()["events"]]
        self.assertEqual(kinds[:3], ["created", "approved", "started"])
        self.assertEqual(kinds[-1], "succeeded")
        self.assertTrue(any(a["event"] == "proposal_succeeded" for a in audit))

    def test_a_failed_task_fails_the_proposal_and_skips_verify(self):
        out, sem, *_ = self.run_restart({"aiops-restart-unit": [("error", ["TASK failed"])], "aiops-service-status": []})
        self.assertEqual(out["state"], "failed")
        self.assertEqual(len(sem.started), 1)

    def test_verify_failure_is_its_own_state_and_names_the_rollback_note(self):
        out, *_ = self.run_restart({
            "aiops-restart-unit": [("success", [result_line(action="restart-unit", ok=True, active_state="active")])],
            "aiops-service-status": [("success", [result_line(action="service-status", ok=True, active_state="failed")])]})
        self.assertEqual(out["state"], "verify_failed")
        self.assertIn("active_state", json.dumps(out["result"]))
        self.assertIn("rollback", out["result"])

    def test_a_task_that_never_finishes_times_out(self):
        out, *_ = self.run_restart({"aiops-restart-unit": [("hang", [])]}, task_timeout=30)
        self.assertEqual(out["state"], "failed")
        self.assertIn("timeout", json.dumps(out["result"]))

    def test_replay_role_runs_the_check_first_and_stops_if_it_fails(self):
        sem = FakeSemaphore({
            "aiops-replay-role-check": [("success", ["canary-1 : ok=5 changed=2 unreachable=0 failed=1"])],
            "aiops-replay-role": [("success", [])]})
        eng, *_ = make(sem)
        p = eng.propose(action_id="replay-role", params={"target_host": "canary-1", "role_tag": "vlagent"}, reason="drifted", source="diagnosis")
        approve(eng, p["id"])
        out = eng.execute(p["id"])
        self.assertEqual(out["state"], "failed")
        self.assertEqual([s[0] for s in sem.started], ["aiops-replay-role-check"])  # the converge never started
        self.assertEqual(sem.started[0][2]["arguments"], ["--check", "--diff", "--tags", "vlagent"])

    def test_replay_role_success_path_verifies_with_a_clean_check(self):
        sem = FakeSemaphore({
            "aiops-replay-role-check": [("success", ["canary-1 : ok=5 changed=2 unreachable=0 failed=0"]),
                                        ("success", ["canary-1 : ok=5 changed=0 unreachable=0 failed=0"])],
            "aiops-replay-role": [("success", ["canary-1 : ok=9 changed=2 unreachable=0 failed=0"])]})
        eng, *_ = make(sem)
        p = eng.propose(action_id="replay-role", params={"target_host": "canary-1", "role_tag": "vlagent"}, reason="drifted", source="diagnosis")
        approve(eng, p["id"])
        out = eng.execute(p["id"])
        self.assertEqual(out["state"], "succeeded")
        self.assertEqual([s[0] for s in sem.started], ["aiops-replay-role-check", "aiops-replay-role", "aiops-replay-role-check"])

    def test_replay_role_verify_fails_if_the_second_check_still_shows_changes(self):
        sem = FakeSemaphore({
            "aiops-replay-role-check": [("success", ["canary-1 : ok=5 changed=2 unreachable=0 failed=0"]),
                                        ("success", ["canary-1 : ok=5 changed=1 unreachable=0 failed=0"])],
            "aiops-replay-role": [("success", ["canary-1 : ok=9 changed=2 unreachable=0 failed=0"])]})
        eng, *_ = make(sem)
        p = eng.propose(action_id="replay-role", params={"target_host": "canary-1", "role_tag": "vlagent"}, reason="drifted", source="diagnosis")
        approve(eng, p["id"])
        self.assertEqual(eng.execute(p["id"])["state"], "verify_failed")

    def test_the_kill_switch_stops_an_approved_proposal_before_it_starts(self):
        sem = FakeSemaphore({"aiops-restart-unit": []})
        eng, *_ = make(sem)
        p = eng.propose(action_id="restart-unit", params=RESTART, reason="stopped", source="diagnosis")
        approve(eng, p["id"])
        with eng.lock:  # engage it between approval and execution without the cancel sweep (simulates the race)
            eng.db.execute("INSERT INTO flags(name, value) VALUES ('kill_switch', 1)")
        self.assertEqual(eng.execute(p["id"])["state"], "cancelled")
        self.assertEqual(sem.started, [])

    def test_one_run_per_target_at_a_time(self):
        sem = FakeSemaphore({"aiops-restart-unit": []})
        eng, *_ = make(sem)
        a = eng.propose(action_id="restart-unit", params=RESTART, reason="first", source="diagnosis")
        b = eng.propose(action_id="restart-unit", params={"target_host": "canary-1", "unit": "zabbix-agent2.service"}, reason="second", source="diagnosis")
        approve(eng, a["id"])
        approve(eng, b["id"])
        with eng.lock:
            eng._move(a["id"], "running", "started")  # a is mid-run on canary-1
        self.assertEqual(eng.execute(b["id"])["state"], "cancelled")

    def test_no_semaphore_credential_means_the_executor_refuses(self):
        eng, *_ = make(None)
        p = eng.propose(action_id="restart-unit", params=RESTART, reason="stopped", source="diagnosis")
        approve(eng, p["id"])
        out = eng.execute(p["id"])
        self.assertEqual(out["state"], "failed")
        self.assertIn("not configured", json.dumps(out["result"]))

    def test_restart_recovery(self):
        eng, clock, audit, db = make(FakeSemaphore())
        a = eng.propose(action_id="restart-unit", params=RESTART, reason="stopped", source="diagnosis")
        b = eng.propose(action_id="restart-unit", params={"target_host": "canary-2", "unit": "vlagent.service"}, reason="stopped", source="diagnosis")
        approve(eng, a["id"])
        approve(eng, b["id"])
        with eng.lock:
            eng._move(a["id"], "running", "started")
        clock.t += 1000  # the old approval of b is stale by the time the Toolbelt comes back
        eng2 = actions.Engine(db, threading.RLock(), clock, lambda *a, **k: None, REGISTRY, eng.cfg, eng._bump, eng._counter)
        self.assertEqual(eng2._view(a["id"])["state"], "failed")
        self.assertEqual(eng2._view(b["id"])["state"], "cancelled")


class LateLogTests(unittest.TestCase):
    """A task that is `success` but whose log is not readable yet must be re-read, not judged empty (10f live finding)."""

    class LateSemaphore(FakeSemaphore):
        def __init__(self, script, empty_reads):
            super().__init__(script)
            self.empty_reads, self.reads = empty_reads, {}

        def output(self, task_id):
            n = self.reads[task_id] = self.reads.get(task_id, 0) + 1
            return [] if n <= self.empty_reads else super().output(task_id)

    def run_with(self, empty_reads):
        sem = self.LateSemaphore({
            "aiops-restart-unit": [("success", [result_line(action="restart-unit", ok=True, active_state="active")])],
            "aiops-service-status": [("success", [result_line(action="service-status", ok=True, active_state="active")])]}, empty_reads)
        eng, clock, audit, _ = make(sem)
        p = eng.propose(action_id="restart-unit", params=RESTART, reason="stopped", source="diagnosis", incident_id=1)
        approve(eng, p["id"])
        return eng.execute(p["id"]), clock

    def test_a_log_that_appears_a_moment_late_still_verifies(self):
        out, clock = self.run_with(empty_reads=2)
        self.assertEqual(out["state"], "succeeded")

    def test_a_log_that_never_appears_still_fails_rather_than_passing(self):
        out, clock = self.run_with(empty_reads=99)
        self.assertEqual(out["state"], "verify_failed")  # an empty result is never read as success


class FeedTests(unittest.TestCase):
    def test_feed_cursor_and_message_ref(self):
        eng, *_ = make()
        p = eng.propose(action_id="restart-unit", params=RESTART, reason="stopped", source="diagnosis", incident_id=1, thread_id="42")
        f = eng.feed()
        self.assertEqual([e["kind"] for e in f["events"]], ["created"])
        self.assertEqual(f["events"][0]["proposal"]["thread_id"], "42")
        eng.set_message(p["id"], "9001")
        f2 = eng.feed(after=f["next"])
        self.assertEqual([e["kind"] for e in f2["events"]], ["message_set"])
        self.assertEqual(f2["events"][0]["proposal"]["message_ref"], "9001")
        self.assertEqual(eng.feed(after=f2["next"])["events"], [])

    def test_summary_and_list(self):
        eng, *_ = make()
        eng.propose(action_id="restart-unit", params=RESTART, reason="stopped", source="diagnosis")
        s = eng.summary()
        self.assertEqual((s["proposals"], s["flags"]["kill_switch"]), ({"pending": 1}, False))
        self.assertEqual(len(eng.list()), 1)


if __name__ == "__main__":
    unittest.main()
