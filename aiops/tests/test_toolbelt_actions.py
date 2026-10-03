"""Phase 10e: the Toolbelt wiring - two roles, proposals, decisions, chat. Driven over a real loopback socket."""
from __future__ import annotations

import copy
import ipaddress
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import actions  # noqa: E402
import core  # noqa: E402
import normalize  # noqa: E402
import server  # noqa: E402
from test_actions import FakeSemaphore, result_line  # noqa: E402

CASES = REPO / "aiops" / "fixtures" / "zabbix-native"
ROUTES = normalize.load_routes()
KNOWN = {r["id"] for r in yaml.safe_load((REPO / "aiops" / "runbooks.yml").read_text())["runbooks"]}
REGISTRY = actions.Registry.from_file(REPO / "aiops" / "actions.yml")
AGENT_TOKEN, APPROVER_TOKEN = "a" * 40, "p" * 40
OP = "111111111111111111"
THREAD = "222222222222222222"
RESTART = {"target_host": "canary-1", "unit": "vlagent.service"}
LOOP = [ipaddress.ip_network("127.0.0.0/8")]


def zevent(host="canary-1", trigger="Agent unreachable", severity="High", event_id="1"):
    e = copy.deepcopy(json.loads((CASES / "native-high-problem-routed.json").read_text())["event"])
    e.update(host=host, trigger_name=trigger, severity=severity, event_id=event_id, status="PROBLEM")
    return e


def good_script():
    return {"aiops-restart-unit": [("success", [result_line(action="restart-unit", ok=True, active_state="active")])],
            "aiops-service-status": [("success", [result_line(action="service-status", ok=True, active_state="active")])]}


class Rig:
    """A Toolbelt with the action engine, behind a real socket with both roles."""

    def __init__(self, sem=None, approver_allow=None, **cfg):
        self.audit: list[dict] = []
        c = core.Config(live=core.tools.LiveConfig(root=REPO), replay_dir=REPO / "aiops" / "replays", **cfg)
        c.actions = actions.ActionConfig(operators=frozenset({OP}), semaphore=sem if sem is not None else FakeSemaphore(good_script()),
                                         poll_seconds=0, sleep=lambda s: None)
        self.tb = core.Toolbelt(c, ROUTES, KNOWN, audit=self.audit.append, registry=REGISTRY)
        handler = server.make_handler(self.tb, AGENT_TOKEN, LOOP, APPROVER_TOKEN, approver_allow if approver_allow is not None else LOOP)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tb.db.close()

    def call(self, method, path, body=None, token=AGENT_TOKEN, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        if token:
            h["Authorization"] = "Bearer " + token
        req = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def agent(self, method, path, body=None, **kw):
        return self.call(method, path, body, AGENT_TOKEN, **kw)

    def appr(self, method, path, body=None, **kw):
        return self.call(method, path, body, APPROVER_TOKEN, **kw)

    def wait_state(self, pid, want, secs=5):
        end = time.time() + secs
        while time.time() < end:
            st, p = self.appr("GET", f"/proposals/{pid}")
            if st == 200 and p["state"] == want:
                return p
            time.sleep(0.02)
        return self.appr("GET", f"/proposals/{pid}")[1]

    def chat_proposal(self, params=RESTART, thread=THREAD):
        st, t = self.agent("POST", "/chat/turn", {"thread_id": thread, "author": OP, "content": "restart vlagent on canary-1 please"})
        assert st == 200, t
        st, p = self.agent("POST", "/proposals", {"conversation_id": t["conversation_id"], "action_id": "restart-unit", "params": params,
                                                  "reason": "vlagent is stopped on canary-1"})
        assert st == 200, p
        return t, p


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.r = Rig()
        self.t, self.p = self.r.chat_proposal()

    def tearDown(self):
        self.r.close()

    def test_healthz_is_open_everything_else_needs_a_token(self):
        self.assertEqual(self.r.call("GET", "/healthz", token=None)[0], 200)
        self.assertEqual(self.r.call("GET", "/status", token=None)[0], 403)
        self.assertEqual(self.r.call("GET", "/status", token="x" * 40)[0], 403)

    def test_the_agent_cannot_decide_flip_flags_or_read_the_feed(self):
        pid = self.p["id"]
        for method, path, body in (("POST", f"/proposals/{pid}/decision", {"decision": "approve", "by": OP}),
                                   ("POST", f"/proposals/{pid}/message", {"message_ref": "1"}),
                                   ("POST", "/flags/kill_switch", {"value": True, "by": OP}),
                                   ("GET", "/flags", None), ("GET", "/proposals/feed", None), ("GET", "/proposals", None), ("GET", "/status", None)):
            self.assertEqual(self.r.agent(method, path, body)[0], 403, (method, path))
        self.assertEqual(self.r.appr("GET", f"/proposals/{pid}")[1]["state"], "pending")
        self.assertTrue(any(a.get("reason") == "role" for a in self.r.audit))

    def test_the_approver_cannot_use_the_agent_routes(self):
        for method, path, body in (("POST", "/ingest/zabbix", zevent()), ("POST", "/tool/registry.actions", {"args": {}, "incident_id": 1}),
                                   ("POST", "/diagnosis/1", {}), ("POST", "/chat/turn", {"thread_id": THREAD, "author": OP, "content": "hi"}),
                                   ("POST", "/proposals", {"action_id": "restart-unit"}), ("GET", "/group/1", None), ("GET", "/watchdog", None)):
            self.assertEqual(self.r.appr(method, path, body)[0], 403, (method, path))

    def test_each_role_may_read_a_proposal_and_stats(self):
        pid = self.p["id"]
        self.assertEqual(self.r.agent("GET", f"/proposals/{pid}")[0], 200)
        self.assertEqual(self.r.appr("GET", f"/proposals/{pid}")[0], 200)
        self.assertEqual(self.r.agent("GET", "/stats")[0], 200)
        self.assertEqual(self.r.appr("GET", "/stats")[0], 200)

    def test_unknown_routes_are_404_after_auth(self):
        self.assertEqual(self.r.agent("DELETE", "/proposals/1")[0], 404)
        self.assertEqual(self.r.appr("GET", "/nope")[0], 404)


class SourceAddressTests(unittest.TestCase):
    def test_the_approver_token_only_works_from_the_approver_network(self):
        r = Rig(approver_allow=[ipaddress.ip_network("127.0.0.2/32")])  # the test client is 127.0.0.1
        try:
            self.assertEqual(r.call("GET", "/status", token=APPROVER_TOKEN)[0], 403)
            self.assertTrue(any(a.get("reason") == "token" for a in r.audit))
        finally:
            r.close()


class DecisionFlowTests(unittest.TestCase):
    def setUp(self):
        self.r = Rig()
        self.t, self.p = self.r.chat_proposal()

    def tearDown(self):
        self.r.close()

    def decide(self, who=OP, decision="approve", h=None, pid=None):
        return self.r.appr("POST", f"/proposals/{pid or self.p['id']}/decision",
                           {"decision": decision, "by": who, "ref": "interaction-1", "params_hash": h or self.p["params_hash"]})

    def test_approve_runs_the_registry_template_and_verifies(self):
        st, body = self.decide()
        self.assertEqual((st, body["state"]), (200, "approved"))
        done = self.r.wait_state(self.p["id"], "succeeded")
        self.assertEqual(done["state"], "succeeded")
        self.assertEqual(done["decided_by"], OP)
        names = [s[0] for s in self.r.tb.engine.cfg.semaphore.started]
        self.assertEqual(names, ["aiops-restart-unit", "aiops-service-status"])

    def test_a_stranger_cannot_approve_even_with_the_approver_token(self):
        st, body = self.decide(who="333333333333333333")
        self.assertEqual(st, 403)
        self.assertEqual(self.r.appr("GET", f"/proposals/{self.p['id']}")[1]["state"], "pending")
        self.assertEqual(self.r.tb.engine.cfg.semaphore.started, [])

    def test_a_forged_params_hash_is_refused(self):
        self.assertEqual(self.decide(h="0" * 16)[0], 409)

    def test_reject_and_double_decision(self):
        self.assertEqual(self.decide(decision="reject")[1]["state"], "rejected")
        self.assertEqual(self.decide()[0], 409)

    def test_kill_switch_over_http(self):
        self.assertEqual(self.r.appr("POST", "/flags/kill_switch", {"value": True, "by": "333333333333333333"})[0], 403)
        st, f = self.r.appr("POST", "/flags/kill_switch", {"value": True, "by": OP, "reason": "drill"})
        self.assertEqual((st, f["value"]), (200, True))
        self.assertEqual(self.decide()[0], 409)
        self.assertTrue(self.r.appr("GET", "/status")[1]["actions"]["flags"]["kill_switch"])
        self.assertEqual(self.r.appr("POST", "/flags/kill_switch", {"value": False, "by": OP})[1]["value"], False)
        self.assertEqual(self.decide()[1]["state"], "approved")

    def test_feed_and_message_ref(self):
        st, feed = self.r.appr("GET", "/proposals/feed")
        self.assertEqual([e["kind"] for e in feed["events"]], ["created"])
        self.assertEqual(feed["events"][0]["proposal"]["thread_id"], THREAD)
        self.r.appr("POST", f"/proposals/{self.p['id']}/message", {"message_ref": "444444444444444444"})
        feed2 = self.r.appr("GET", f"/proposals/feed?after={feed['next']}")[1]
        self.assertEqual([e["kind"] for e in feed2["events"]], ["message_set"])
        self.assertEqual(self.r.appr("POST", f"/proposals/{self.p['id']}/message", {})[0], 400)

    def test_listing_open_proposals(self):
        st, body = self.r.appr("GET", "/proposals")
        self.assertEqual([p["id"] for p in body["proposals"]], [self.p["id"]])


class ProposalValidationTests(unittest.TestCase):
    def setUp(self):
        self.r = Rig()

    def tearDown(self):
        self.r.close()

    def test_bad_proposals_are_refused_with_the_problems(self):
        st, t = self.r.agent("POST", "/chat/turn", {"thread_id": THREAD, "author": OP, "content": "x"})
        for body, want in (({"conversation_id": t["conversation_id"], "action_id": "restart-unit", "params": {"target_host": "gondul", "unit": "k3s.service"}, "reason": "restart k3s"}, 422),
                           ({"conversation_id": t["conversation_id"], "action_id": "rm-rf", "params": {}, "reason": "no such action"}, 404),
                           ({"conversation_id": t["conversation_id"], "action_id": "restart-unit", "params": RESTART, "reason": "x", "extra": 1}, 400),
                           ({"conversation_id": 999, "action_id": "restart-unit", "params": RESTART, "reason": "no such conversation"}, 404),
                           ({"conversation_id": True, "action_id": "restart-unit", "params": RESTART, "reason": "bool is not an int"}, 400)):
            st, resp = self.r.agent("POST", "/proposals", body)
            self.assertEqual(st, want, resp)
        self.assertIn("problems", self.r.agent("POST", "/proposals", {"conversation_id": t["conversation_id"], "action_id": "restart-unit",
                                                                       "params": {"target_host": "gondul", "unit": "k3s.service"}, "reason": "restart k3s"})[1])


class DiagnosisProposalTests(unittest.TestCase):
    def setUp(self):
        self.r = Rig()

    def tearDown(self):
        self.r.close()

    def incident(self, host="canary-1", replay=None):
        headers = {"X-AIOPS-Replay": replay} if replay else None
        st, out = self.r.agent("POST", "/ingest/zabbix", zevent(host=host), headers=headers)
        self.assertEqual(st, 200, out)
        return out["incident_id"]

    def diag(self, inc, actions_):
        return {"diagnosis": {"schema_version": "aiops.diagnosis/v1", "incident_id": inc, "layer": "unknown", "confidence": "low",
                              "summary": "Not enough to say which layer; the agent service looks down.", "evidence": [], "needs_human": True,
                              "proposed_actions": actions_}, "model": "test"}

    def test_proposed_actions_become_proposals_bound_to_the_thread_once_it_exists(self):
        inc = self.incident()
        st, out = self.r.agent("POST", f"/diagnosis/{inc}", self.diag(inc, [{"action_id": "restart-unit", "params": RESTART, "reason": "vlagent stopped"}]))
        self.assertEqual(st, 200, out)
        self.assertEqual(len(out["proposals"]), 1)
        self.assertIn(f"(proposal #{out['proposals'][0]})", out["content"])
        self.assertIn("approves each one", out["content"])
        pid = out["proposals"][0]
        self.assertIsNone(self.r.appr("GET", f"/proposals/{pid}")[1]["thread_id"])
        self.r.agent("POST", f"/group/{inc}/state", {"state": "running"})
        self.r.agent("POST", f"/group/{inc}/state", {"state": "posted", "thread_id": THREAD})
        kinds = [e["kind"] for e in self.r.appr("GET", "/proposals/feed")[1]["events"]]
        self.assertEqual(kinds, ["created", "thread_bound"])
        self.assertEqual(self.r.appr("GET", f"/proposals/{pid}")[1]["thread_id"], THREAD)

    def test_a_proposal_that_fails_the_guard_is_reported_not_dropped(self):
        inc = self.incident()
        st, out = self.r.agent("POST", f"/diagnosis/{inc}", self.diag(inc, [{"action_id": "restart-unit", "params": {"target_host": "gondul", "unit": "k3s.service"}, "reason": "restart k3s"}]))
        self.assertEqual(st, 200)
        self.assertEqual(out["proposals"], [])
        self.assertEqual(out["proposals_refused"][0]["action_id"], "restart-unit")
        self.assertIn("was NOT proposed", out["content"])

    def test_a_diagnosis_without_params_is_valid_but_cannot_become_a_proposal(self):
        inc = self.incident()
        st, out = self.r.agent("POST", f"/diagnosis/{inc}", self.diag(inc, [{"action_id": "restart-unit", "reason": "vlagent stopped"}]))
        self.assertEqual(st, 200)
        self.assertEqual(out["proposals"], [])
        self.assertTrue(out["proposals_refused"])

    def auto_diag(self, inc, params=RESTART):
        self.r.tb.call_tool("registry.runbook", {"id": "RB-UNIT-STOPPED-T1"}, inc)  # evidence must match a call the Toolbelt served
        d = self.diag(inc, [{"action_id": "restart-unit", "params": params, "reason": "the unit is stopped"}])
        d["diagnosis"].update(layer="workload", confidence="high", needs_human=False, runbook_id="RB-UNIT-STOPPED-T1",
                              evidence=[{"tool": "registry.runbook", "args": {"id": "RB-UNIT-STOPPED-T1"}, "finding": "the stopped-unit runbook applies"}])
        return d

    def test_autonomy_end_to_end_through_the_real_server(self):
        self.r.close()
        sem = FakeSemaphore({"aiops-service-status": [("success", [result_line(action="service-status", ok=True, active_state="failed")]),
                                                      ("success", [result_line(action="service-status", ok=True, active_state="active")])],
                             "aiops-restart-unit": [("success", [result_line(action="restart-unit", ok=True, active_state="active")])]})
        self.r = Rig(sem)
        inc = self.incident()
        st, out = self.r.agent("POST", f"/diagnosis/{inc}", self.auto_diag(inc))  # master switch is off by default
        self.assertEqual((st, out["auto"]), (200, [{"auto": False, "reason": "autonomy-off"}]), out)
        self.assertEqual(sem.started, [])
        self.assertEqual(self.r.agent("POST", "/flags/autonomy", {"value": True, "by": OP})[0], 403)  # the agent role cannot flip it
        self.assertEqual(self.r.appr("POST", "/flags/autonomy", {"value": True, "by": OP, "reason": "test"})[0], 200)
        inc2 = self.incident(host="canary-2")
        st, out = self.r.agent("POST", f"/diagnosis/{inc2}", self.auto_diag(inc2, {"target_host": "canary-2", "unit": "vlagent.service"}))
        self.assertEqual((st, out["auto"][0]["auto"]), (200, True), out)
        self.assertIn("running automatically by policy `restart-failed-unit`", out["content"])
        done = self.r.wait_state(out["proposals"][0], "succeeded")
        self.assertEqual((done["state"], done["decided_by"]), ("succeeded", "auto:restart-failed-unit"))
        st, rep = self.r.appr("GET", "/report?days=7")
        self.assertEqual((st, rep["autonomous_runs"], rep["skipped_reasons"]), (200, 1, {"autonomy-off": 1}))
        self.assertEqual(self.r.agent("GET", "/report")[0], 403)  # the agent role cannot read it either
        self.assertEqual(self.r.appr("POST", "/flags/autonomy_breaker", {"value": True, "by": OP})[0], 403)  # only the system trips it

    def test_replay_incidents_produce_replay_proposals_that_can_never_be_decided(self):
        inc = self.incident(replay="canary-agent-down")
        st, out = self.r.agent("POST", f"/diagnosis/{inc}", self.diag(inc, [{"action_id": "restart-unit", "params": RESTART, "reason": "vlagent stopped"}]))
        self.assertEqual(st, 200, out)
        pid = out["proposals"][0]
        self.assertTrue(self.r.appr("GET", f"/proposals/{pid}")[1]["replay"])
        st, body = self.r.appr("POST", f"/proposals/{pid}/decision", {"decision": "approve", "by": OP})
        self.assertEqual(st, 409)
        self.assertEqual(self.r.tb.engine.cfg.semaphore.started, [])


class IncidentDraftRouteTests(unittest.TestCase):
    def setUp(self):
        self.r = Rig()

    def tearDown(self):
        self.r.close()

    def test_only_the_approver_reads_a_draft_and_it_is_scrubbed_markdown(self):
        st, out = self.r.agent("POST", "/ingest/zabbix", zevent(host="canary-1"))
        self.assertEqual(st, 200, out)
        inc = out["incident_id"]
        self.assertIn(self.r.agent("GET", f"/incident/{inc}/draft")[0], (401, 403))  # the agent role cannot read it
        st, d = self.r.appr("GET", f"/incident/{inc}/draft")
        self.assertEqual(st, 200, d)
        self.assertTrue(d["filename"].endswith(".md") and d["slug"][:4].isdigit())
        self.assertIn("DRAFT: generated from the Toolbelt's records", d["markdown"])
        self.assertIn(f"# Incident #{inc}: canary-1", d["markdown"])
        self.assertEqual(self.r.appr("GET", "/incident/9999/draft")[0], 404)


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.r = Rig(chat_conversation_turn_cap=3, chat_author_hourly_cap=4, chat_daily_turn_cap=5, chat_tool_calls_per_turn=2)

    def tearDown(self):
        self.r.close()

    def turn(self, content="what is the state of canary-1?", thread=THREAD, author=OP):
        return self.r.agent("POST", "/chat/turn", {"thread_id": thread, "author": author, "content": content})

    def test_turn_validation(self):
        for body in ({"thread_id": "abc", "author": OP, "content": "x"}, {"thread_id": THREAD, "author": "bob", "content": "x"},
                     {"thread_id": THREAD, "author": OP, "content": "  "}, {"thread_id": THREAD, "author": OP, "content": "y" * 2001}):
            self.assertEqual(self.r.agent("POST", "/chat/turn", body)[0], 400, body)

    def test_history_and_reply_scrubbing(self):
        st, t1 = self.turn("first question")
        self.assertEqual((st, t1["kind"], t1["history"]), (200, "chat", []))
        st, rep = self.r.agent("POST", "/chat/reply", {"conversation_id": t1["conversation_id"], "turn_id": t1["turn_id"],
                                                       "content": "ping @everyone and @here; key sk-ant-abcdefghijklmnop; https://discord.com/api/webhooks/123456789012345678/abcDEF-ghi"})
        self.assertEqual(st, 200)
        self.assertNotIn("sk-ant-abcdefghijklmnop", rep["content"])
        self.assertNotIn("webhooks/1234", rep["content"])
        self.assertNotIn("@everyone", rep["content"])
        self.assertNotIn("@here", rep["content"])
        self.assertGreaterEqual(rep["redacted"], 2)
        st, t2 = self.turn("second question")
        self.assertEqual([h["role"] for h in t2["history"]], ["user", "assistant"])
        self.assertEqual(t2["conversation_id"], t1["conversation_id"])
        self.assertEqual(self.r.agent("POST", "/chat/reply", {"conversation_id": t2["conversation_id"], "turn_id": 999, "content": "x"})[0], 404)

    def test_long_replies_are_truncated_to_the_discord_limit(self):
        st, t = self.turn()
        st, rep = self.r.agent("POST", "/chat/reply", {"conversation_id": t["conversation_id"], "turn_id": t["turn_id"], "content": "z" * 5000})
        self.assertLessEqual(len(rep["content"]), 1900)

    def test_caps(self):
        for _ in range(3):
            self.assertEqual(self.turn()[0], 200)
        st, body = self.turn()
        self.assertEqual((st, body["error"]), (429, "conversation-cap"))
        self.assertEqual(self.turn(thread="555555555555555555")[0], 200)  # a fresh thread still works
        st, body = self.turn(thread="555555555555555555")
        self.assertEqual((st, body["error"]), (429, "author-rate"))  # 4 turns this hour by this author
        self.assertEqual(self.turn(thread="666666666666666666", author="777777777777777777")[0], 200)  # another user is unaffected (5th turn today)
        st, body = self.turn(thread="888888888888888888", author="999999999999999999")  # the daily budget of 5 is spent
        self.assertEqual((st, body["error"]), (429, "daily-chat-cap"))

    def test_chat_tool_calls_are_scoped_to_the_turn_and_capped(self):
        st, t = self.turn()
        body = {"args": {}, "conversation_id": t["conversation_id"], "turn_id": t["turn_id"]}
        self.assertEqual(self.r.agent("POST", "/tool/registry.actions", body)[0], 200)
        self.assertEqual(self.r.agent("POST", "/tool/registry.actions", body)[0], 200)
        st, resp = self.r.agent("POST", "/tool/registry.actions", body)
        self.assertEqual((st, resp["error"]), (429, "per-turn tool-call cap reached"))
        self.assertEqual(self.r.agent("POST", "/tool/registry.actions", {"args": {}, "conversation_id": t["conversation_id"], "turn_id": 12345})[0], 404)
        self.assertEqual(self.r.agent("POST", "/tool/registry.actions", {"args": {}, "conversation_id": "1", "turn_id": 1})[0], 400)
        self.assertEqual(self.r.agent("POST", "/tool/zabbix.nope", {"args": {}, "conversation_id": t["conversation_id"], "turn_id": t["turn_id"]})[0], 404)

    def test_an_incident_thread_gets_the_incident_and_diagnosis_as_context(self):
        st, out = self.r.agent("POST", "/ingest/zabbix", zevent())
        inc = out["incident_id"]
        self.r.agent("POST", f"/group/{inc}/state", {"state": "running"})
        self.r.agent("POST", f"/group/{inc}/state", {"state": "posted", "thread_id": THREAD})
        st, t = self.turn("why did you say that?")
        self.assertEqual((t["kind"], t["incident_id"]), ("incident", inc))
        self.assertEqual(t["context"]["incident"]["incident_id"], inc)
        self.assertIn("proposals", t["context"])


class ServerMainTests(unittest.TestCase):
    """server.main() argument handling for the 10e options (it is never started: bad arguments exit before the socket)."""

    def setUp(self):
        import tempfile

        self.d = Path(tempfile.mkdtemp())
        (self.d / "agent").write_text("a" * 40)
        (self.d / "appr").write_text("p" * 40)
        (self.d / "same").write_text("a" * 40)
        (self.d / "ops").write_text("111111111111111111,222222222222222222\n")
        (self.d / "noops").write_text("not-an-id\n")
        self.base = ["--listen", "127.0.0.1:0", "--db", ":memory:", "--token-file", str(self.d / "agent"), "--allow", "127.0.0.0/8"]

    def test_actions_need_an_operator_file_and_an_approver_token(self):
        self.assertEqual(server.main(self.base + ["--actions"]), 2)
        self.assertEqual(server.main(self.base + ["--actions", "--approver-token-file", str(self.d / "appr")]), 2)

    def test_the_approver_token_must_differ_from_the_agent_token(self):
        self.assertEqual(server.main(self.base + ["--approver-token-file", str(self.d / "same")]), 2)

    def test_an_operators_file_without_a_discord_id_is_refused(self):
        self.assertEqual(server.main(self.base + ["--actions", "--approver-token-file", str(self.d / "appr"), "--operators-file", str(self.d / "noops")]), 2)

    def test_a_comma_separated_operator_list_is_parsed(self):
        import re

        parsed = frozenset(x for x in re.split(r"[,\s]+", (self.d / "ops").read_text()) if x.isdigit())
        self.assertEqual(parsed, {"111111111111111111", "222222222222222222"})


if __name__ == "__main__":
    unittest.main()
