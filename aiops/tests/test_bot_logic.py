"""Phase 10e: Ratatoskr's logic (aiops/bot/logic.py), without Discord, plus the bot's Toolbelt client against the real server."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("bot", "toolbelt", "tools", "tests"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import logic  # noqa: E402
from test_toolbelt_actions import AGENT_TOKEN, APPROVER_TOKEN, OP, RESTART, THREAD, Rig  # noqa: E402

PROPOSAL = {
    "id": 12, "state": "pending", "action_id": "restart-unit", "tier": "T1", "target": "canary-1",
    "params": {"target_host": "canary-1", "unit": "vlagent.service"}, "params_hash": "0123456789abcdef",
    "reason": "vlagent stopped @everyone <@123456789012345678>", "incident_id": 5, "conversation_id": None,
    "thread_id": THREAD, "source": "diagnosis", "replay": False, "created_at": 1, "expires_at": 4102444800,
    "decided_by": None, "message_ref": None, "description": "Restart one systemd unit on one T1 host.",
    "rollback": "A restart has no inverse.", "verify": {"ok": True, "active_state": "active"}, "requires_prior": None, "result": None,
}


OP_ID = "111111111111111111"


def p(**kw):
    return {**PROPOSAL, **kw}


def ev(proposal, kind="created", i=1):
    return {"id": i, "kind": kind, "ts": 0, "data": {}, "proposal": proposal}


class HelperTests(unittest.TestCase):
    def test_custom_id_roundtrip_and_rejects(self):
        cid = logic.custom_id("approve", 12, "0123456789abcdef")
        self.assertEqual(logic.parse_custom_id(cid), ("approve", 12, "0123456789abcdef"))
        for bad in ("", "aiops:approve:x:0123456789abcdef", "aiops:delete:1:0123456789abcdef", "aiops:approve:1:xyz", "other:approve:1:0123456789abcdef"):
            self.assertIsNone(logic.parse_custom_id(bad), bad)
        self.assertLessEqual(len(cid), 100)  # Discord's custom_id limit

    def test_sanitize_neutralises_pings_and_bounds_text(self):
        t = logic.sanitize("hello @everyone and @here <@123456789012345678> <@&99> end\n\nline2")
        self.assertNotIn("<@", t)
        self.assertNotIn("@everyone", t)
        self.assertNotIn("\n", t)
        self.assertLessEqual(len(logic.sanitize("x" * 1000)), 300)

    def test_strip_mention_and_split(self):
        self.assertEqual(logic.strip_mention("<@555> why is it down? <@!555>", 555), "why is it down?")
        self.assertEqual(logic.strip_mention("<@999> hello", 555), "<@999> hello")
        chunks = logic.split_message("a" * 4000 + "\nshort\n" + "b" * 100, limit=1900)
        self.assertTrue(all(len(c) <= 1900 for c in chunks))
        self.assertEqual("".join(chunks).replace("\n", ""), "a" * 4000 + "short" + "b" * 100)
        self.assertEqual(logic.split_message(""), [""])

    def test_friendly_chat_errors(self):
        self.assertIn("this hour", logic.friendly_chat_error(429, {"error": "author-rate"}))
        self.assertIn("turn limit", logic.friendly_chat_error(429, {"error": "conversation-cap"}))
        self.assertIn("budget", logic.friendly_chat_error(429, {"error": "daily-chat-cap"}))
        self.assertIn("could not reach", logic.friendly_chat_error(0, {}))
        self.assertIn("credential", logic.friendly_chat_error(403, {}))
        self.assertIn("500", logic.friendly_chat_error(500, {}))

    def test_operator_check_is_by_id_string(self):
        cfg = mk_cfg()
        self.assertTrue(cfg.is_operator(111111111111111111))
        self.assertTrue(cfg.is_operator("111111111111111111"))
        self.assertFalse(cfg.is_operator("222222222222222222"))
        self.assertFalse(cfg.is_operator(None))
        self.assertFalse(cfg.is_operator(""))


def mk_cfg(**kw):
    base = dict(toolbelt_url="http://127.0.0.1:1", approver_token=APPROVER_TOKEN, n8n_chat_url="http://127.0.0.1:1/chat", n8n_chat_token="c" * 40,
                operator_ids=frozenset({OP}), guild_id=1, diagnoses_channel_id=2, chat_channel_id=3, state_dir=Path(tempfile.mkdtemp()))
    base.update(kw)
    return logic.Config(**base)


class ConfigTests(unittest.TestCase):
    def test_load_config(self):
        d = Path(tempfile.mkdtemp())
        (d / "static.json").write_text(json.dumps({"toolbelt_url": "http://10.0.11.30:8090/", "n8n_chat_url": "http://10.0.11.221:8081/webhook/aiops-chat",
                                                    "state_dir": str(d / "state")}))
        (d / "secrets.json").write_text(json.dumps({"token": "tok", "operator_user_id": OP, "guild_id": "1", "diagnoses_channel_id": "2", "chat_channel_id": "3"}))
        (d / "appr").write_text("a" * 40 + "\n")
        (d / "chat").write_text("c" * 40 + "\n")
        cfg, token = logic.load_config(str(d / "static.json"), str(d / "secrets.json"), str(d / "appr"), str(d / "chat"))
        self.assertEqual((token, cfg.toolbelt_url, cfg.approver_token, cfg.n8n_chat_token), ("tok", "http://10.0.11.30:8090", "a" * 40, "c" * 40))
        self.assertEqual((cfg.operator_ids, cfg.chat_channel_id), (frozenset({OP}), 3))
        (d / "secrets.json").write_text(json.dumps({"token": "tok", "operator_user_id": "not-an-id", "guild_id": "1", "diagnoses_channel_id": "2", "chat_channel_id": "3"}))
        with self.assertRaises(SystemExit):
            logic.load_config(str(d / "static.json"), str(d / "secrets.json"), str(d / "appr"), str(d / "chat"))


class CardTests(unittest.TestCase):
    def test_the_title_shows_the_conversation_number_not_the_primary_key(self):
        c = logic.card({**PROPOSAL, "number": 1})
        self.assertEqual(c["title"], "Proposal #1: restart-unit")
        self.assertIn("id 12 |", c["footer"])
        self.assertIn("Proposal #1 ", logic.result_summary({**PROPOSAL, "number": 1, "state": "rejected"}))

    def test_pending_card_is_built_from_toolbelt_data_and_never_pings(self):
        c = logic.card(PROPOSAL)
        self.assertTrue(c["buttons"])
        self.assertEqual(c["title"], "Proposal #12: restart-unit")
        self.assertNotIn("@everyone", c["description"])
        self.assertNotIn("<@123456789012345678>", c["description"])
        self.assertIn("params 0123456789abcdef", c["footer"])
        self.assertIn("id 12 |", c["footer"])
        self.assertIn("expires <t:4102444800:R>", c["footer"])
        names = [f[0] for f in c["fields"]]
        self.assertEqual(names, ["Action", "Target", "Parameters", "What it does", "Then it verifies", "If it goes wrong"])
        self.assertIn("unit = vlagent.service", dict((f[0], f[1]) for f in c["fields"])["Parameters"])

    def test_requires_prior_is_shown(self):
        c = logic.card(p(action_id="replay-role", requires_prior="replay-role-check"))
        self.assertIn("Runs first", [f[0] for f in c["fields"]])

    def test_decided_cards_lose_their_buttons_and_name_the_operator(self):
        for state in ("approved", "running", "succeeded", "failed", "verify_failed", "rejected"):
            c = logic.card(p(state=state, decided_by=OP))
            self.assertFalse(c["buttons"], state)
            self.assertIn(f"<@{OP}>", c["description"], state)
            self.assertNotIn("expires", c["footer"])
        for state in ("expired", "cancelled"):
            c = logic.card(p(state=state))
            self.assertFalse(c["buttons"])
            self.assertNotIn("<@", c["description"])

    def test_result_summaries(self):
        ok = logic.result_summary(p(state="succeeded", result={"steps": [{"step": "action", "status": "success"}, {"step": "verify:service-status", "status": "success"}]}))
        self.assertIn("succeeded and verified", ok)
        self.assertIn("- action: success", ok)
        bad = logic.result_summary(p(state="verify_failed", result={"why": "the post-condition did not hold: active_state: expected 'active', got 'failed'",
                                                                 "rollback": "A restart has no inverse."}))
        self.assertIn("did NOT hold", bad)
        self.assertIn("If it goes wrong: A restart has no inverse.", bad)
        self.assertIn("expired", logic.result_summary(p(state="expired")))
        self.assertLessEqual(len(logic.result_summary(p(state="failed", result={"why": "x" * 5000}))), 1900)


class ResultSentenceTests(unittest.TestCase):
    """A succeeded read-only proposal also relays what the probe found, from the stored fields."""

    def t0(self, action_id, res, **kw):
        steps = [{"step": "action", "status": "success", "result": res}, {"step": f"verify:{action_id}", "status": "success", "result": {"ok": True}}]
        return p(state="succeeded", tier="T0", action_id=action_id, result={"steps": steps}, **kw)

    def sentence(self, action_id, res):
        return logic.result_sentence(self.t0(action_id, res))

    def test_patroni_healthy_lag_and_no_leader(self):
        base = {"ok": True, "leader_present": True, "running_members": 3, "total_members": 3,
                "members": ["Fulla:leader:running", "Vör:replica:streaming", "Idunn:replica:streaming"]}
        self.assertEqual(self.sentence("patroni-status", {**base, "max_lag_bytes": 0}),
                         "Checked Patroni: leader is Fulla, 3 of 3 members running, no lag.")
        self.assertIn("worst replica lag 4096 bytes", self.sentence("patroni-status", {**base, "max_lag_bytes": 4096}))
        self.assertTrue(self.sentence("patroni-status", base).endswith("members running."))   # lag not reported: say nothing about it
        nolead = self.sentence("patroni-status", {"ok": True, "running_members": 1, "total_members": 3,
                                                  "members": ["Fulla:replica:stopped", "Vör:replica:running", "Idunn:replica:crashed"]})
        self.assertIn("NO leader", nolead)
        self.assertIn("not running: Fulla (stopped), Idunn (crashed)", nolead)
        self.assertIn("no PG node answered", self.sentence("patroni-status", {"ok": "False"}))

    def test_service_status_active_and_failed(self):
        act = self.sentence("service-status", {"target_host": "canary-1", "unit": "vlagent.service", "load_state": "loaded",
                                               "active_state": "active", "sub_state": "running", "n_restarts": "0"})
        self.assertEqual(act, "Checked vlagent.service on canary-1: active (running).")
        bad = self.sentence("service-status", {"target_host": "canary-1", "unit": "vlagent.service", "load_state": "loaded",
                                               "active_state": "failed", "sub_state": "failed", "n_restarts": "3", "active_since": "Fri 2026-10-02"})
        self.assertEqual(bad, "Checked vlagent.service on canary-1: failed (failed), restarted 3 times, since Fri 2026-10-02.")
        self.assertIn("not loaded", self.sentence("service-status", {"target_host": "h", "unit": "x.service", "load_state": "not-found"}))

    def test_vault_sealed_and_unsealed(self):
        self.assertEqual(self.sentence("vault-status", {"ok": True, "sealed": False, "initialized": True, "ha_enabled": True, "version": "1.18.2", "storage_type": "raft"}),
                         "Checked Vault: unsealed, initialized, HA enabled, version 1.18.2, storage raft.")
        self.assertTrue(self.sentence("vault-status", {"ok": "True", "sealed": "true"}).startswith("Checked Vault: SEALED"))
        self.assertIn("no status document", self.sentence("vault-status", {"ok": False}))

    def test_replay_role_check_changed_and_clean(self):
        self.assertEqual(self.sentence("replay-role-check", {"ok": True, "changed": 0, "target_host": "canary-1"}),
                         "Dry run on canary-1: nothing would change, the host is in sync.")
        self.assertIn("would change 4 tasks", self.sentence("replay-role-check", {"ok": True, "changed": 4, "target_host": "canary-1"}))
        self.assertIn("1 task.", self.sentence("replay-role-check", {"ok": True, "changed": 1, "target_host": "canary-1"}))
        self.assertIn("did not finish cleanly", self.sentence("replay-role-check", {"ok": False, "failed": 1, "unreachable": 0, "target_host": "c"}))

    def test_unknown_action_gets_a_bounded_scalar_line(self):
        s = self.sentence("brand-new-probe", {"action": "brand-new-probe", "ok": True, "a": 1, "b": "x", "nested": {"k": "v"}, "c": 3, "d": 4, "e": 5, "f": 6})
        self.assertEqual(s, "Checked brand-new-probe: ok=True, a=1, b=x, c=3, d=4.")
        self.assertNotIn("{", s)

    def test_hostile_text_is_sanitised_and_bounded(self):
        s = self.sentence("service-status", {"target_host": "h", "unit": "@everyone <@123456789012345678> " + "y" * 500, "load_state": "loaded",
                                             "active_state": "@here", "sub_state": "x"})
        self.assertNotIn("@everyone", s)
        self.assertNotIn("<@", s)
        self.assertNotIn("@here", s)
        self.assertLessEqual(len(s), 400)
        out = logic.result_summary(self.t0("service-status", {"unit": "@everyone", "target_host": "h"}))
        self.assertNotIn("@everyone", out)
        self.assertLessEqual(len(out), 1900)

    def test_a_formatter_error_falls_back_to_the_generic_line(self):
        res = {"members": 5, "running_members": 1}     # members is not a list: the patroni formatter's own input is odd
        orig = logic.RESULT_SENTENCES["patroni-status"]
        logic.RESULT_SENTENCES["patroni-status"] = lambda r: 1 / 0
        try:
            s = self.sentence("patroni-status", res)
        finally:
            logic.RESULT_SENTENCES["patroni-status"] = orig
        self.assertEqual(s, "Checked patroni-status: members=5, running_members=1.")

    def test_only_succeeded_t0_proposals_with_an_action_result_relay(self):
        res = {"ok": True, "sealed": False}
        self.assertEqual(logic.result_sentence({**self.t0("vault-status", res), "tier": "T1"}), "")           # mutating tier keeps today's summary
        self.assertEqual(logic.result_sentence({**self.t0("vault-status", res), "state": "failed"}), "")
        self.assertEqual(logic.result_sentence(p(state="succeeded", tier="T0", action_id="vault-status", result={"steps": [{"step": "verify:vault-status", "result": res}]})), "")
        self.assertEqual(logic.result_sentence(p(state="succeeded", tier="T0", action_id="vault-status", result=None)), "")
        self.assertEqual(logic.result_sentence(p(state="succeeded", tier="T0", action_id="vault-status", result={"steps": "garbage"})), "")
        self.assertIn("Checked Vault: unsealed", logic.result_summary(self.t0("vault-status", res)))
        self.assertNotIn("Checked", logic.result_summary(p(state="succeeded", tier="T1", result={"steps": [{"step": "action", "status": "success", "result": res}]})))


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.state = logic.State(Path(tempfile.mkdtemp()) / "state.json")

    def plan(self, *events):
        return [(a.kind, a.proposal["id"]) for a in logic.plan_feed(list(events), self.state)]

    def test_a_new_pending_proposal_with_a_thread_gets_a_card(self):
        self.assertEqual(self.plan(ev(PROPOSAL)), [("post_card", 12)])

    def test_nothing_is_posted_without_a_thread_or_for_replays_or_when_a_card_exists(self):
        self.assertEqual(self.plan(ev(p(thread_id=None))), [])
        self.assertEqual(self.plan(ev(p(replay=True))), [])
        self.assertEqual(self.plan(ev(p(message_ref="9"))), [])

    def test_a_proposal_the_toolbelt_ran_itself_still_gets_a_buttonless_card_and_an_announcement(self):
        auto = p(state="succeeded", decided_by="auto:restart-failed-unit")
        self.assertEqual(self.plan(ev(auto)), [("post_card", 12), ("announce", 12)])
        self.assertEqual(self.plan(ev(p(state="skipped", decided_by="auto:restart-failed-unit"))), [("post_card", 12), ("announce", 12)])
        self.assertEqual(self.plan(ev(p(state="succeeded", decided_by=OP_ID))), [])  # a human decision always had a card already
        self.assertEqual(self.plan(ev(p(state="succeeded", decided_by="auto:x", thread_id=None))), [])
        self.assertEqual(self.plan(ev(p(state="succeeded", decided_by="auto:x", replay=True))), [])

    def test_an_autonomy_card_says_policy_and_has_no_buttons(self):
        c = logic.card(p(state="running", decided_by="auto:restart-failed-unit"))
        self.assertFalse(c["buttons"])
        self.assertIn("Auto-approved by policy `restart-failed-unit`", c["description"])
        self.assertNotIn("<@", c["description"].split("**")[1] if "**" in c["description"] else "")
        self.assertIn("already healed", logic.card(p(state="skipped", decided_by="auto:x"))["description"])
        self.assertIn("skipped", logic.result_summary(p(state="skipped", decided_by="auto:x")))

    def test_the_breaker_notice_goes_to_the_thread_of_the_run_that_tripped_it(self):
        e = ev(p(state="failed", decided_by="auto:x"), "breaker_tripped")
        e["data"] = {"failures": 3, "window_seconds": 3600}
        plan = logic.plan_feed([e], self.state)
        self.assertEqual([a.kind for a in plan], ["breaker", "post_card", "announce"])
        text = logic.breaker_notice(plan[0].proposal)
        self.assertIn("TRIPPED", text)
        self.assertIn("3 autonomous runs", text)
        self.assertIn("60 minutes", text)
        self.assertIn("reset-breaker", text)

    def test_the_latest_state_in_the_batch_wins(self):
        self.assertEqual(self.plan(ev(p(state="pending", message_ref="9"), i=1), ev(p(state="approved", message_ref="9"), "approved", 2)), [("edit_card", 12)])

    def test_terminal_proposals_are_edited_and_announced_exactly_once(self):
        done = p(state="succeeded", message_ref="9")
        self.assertEqual(self.plan(ev(done, "succeeded")), [("edit_card", 12), ("announce", 12)])
        self.state.announced.add(12)
        self.assertEqual(self.plan(ev(done, "succeeded")), [("edit_card", 12)])

    def test_a_terminal_proposal_that_never_had_a_card_is_ignored(self):
        self.assertEqual(self.plan(ev(p(state="expired", message_ref=None), "expired")), [])

    def test_running_edits_but_does_not_announce(self):
        self.assertEqual(self.plan(ev(p(state="running", message_ref="9"), "step_started")), [("edit_card", 12)])

    def test_state_survives_a_restart_and_a_corrupt_file_is_harmless(self):
        self.state.cursor = 42
        self.state.announced = {1, 2}
        self.state.save()
        again = logic.State.load(self.state.path.parent)
        self.assertEqual((again.cursor, again.announced), (42, {1, 2}))
        self.state.path.write_text("{not json")
        self.assertEqual(logic.State.load(self.state.path.parent).cursor, 0)

    def test_format_status(self):
        s = logic.format_status({"open_incidents": 1, "runs_today": 3, "daily_run_cap": 40, "chat_turns_today": 2,
                                 "actions": {"flags": {"kill_switch": True, "maintenance": False}, "proposals_today": 1, "daily_proposal_cap": 30,
                                             "proposals": {"pending": 1}}, "open_proposals": [PROPOSAL]})
        self.assertIn("ENGAGED", s)
        self.assertIn("#12", s)


    def test_status_shows_autonomy_and_a_tripped_breaker(self):
        a = {"flags": {"kill_switch": False, "maintenance": False, "autonomy": True, "autonomy_breaker": True}, "proposals_today": 0,
             "daily_proposal_cap": 30, "proposals": {}, "autonomy": {"hosts": ["canary-1"], "policies": {"restart-failed-unit": True, "replay-drifted-baseline": False}}}
        s = logic.format_status({"actions": a, "open_proposals": []})
        self.assertIn("Autonomy: ON", s)
        self.assertIn("BREAKER TRIPPED", s)
        self.assertIn("policies on: restart-failed-unit", s)
        self.assertNotIn("replay-drifted-baseline", s)
        off = logic.format_status({"actions": {**a, "flags": {"autonomy": False}, "autonomy": None}, "open_proposals": []})
        self.assertIn("Autonomy: off", off)

    def test_format_report(self):
        r = {"days": 14, "autonomous_runs": 3, "by_policy": {"restart-failed-unit": {"succeeded": 2, "failed": 1}}, "by_target": {"canary-1": 3},
             "skipped_reasons": {"autonomy-off": 4}, "breaker_trips": 1, "flapping_targets": ["canary-1"], "flags": {"autonomy": True}}
        t = logic.format_report(r)
        for needle in ("3 autonomous run", "breaker trips 1", "master switch ON", "succeeded 2", "`canary-1` 3", "autonomy-off 4", "Flapping"):
            self.assertIn(needle, t)
        self.assertIn("0 autonomous run", logic.format_report({"days": 1}))


class FakeBrain(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        return

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeBrain.seen.append((self.headers.get("X-AIOPS-Token"), body))
        code, out = (200, {"reply": "canary-1 vlagent is stopped"}) if self.headers.get("X-AIOPS-Token") == "c" * 40 else (403, {"error": "no"})
        raw = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ClientTests(unittest.TestCase):
    def test_brain_client_sends_the_token_and_who_asked(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeBrain)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/chat"
            st, body = logic.Brain(mk_cfg(n8n_chat_url=url)).chat(THREAD, OP, "why?")
            self.assertEqual((st, body["reply"]), (200, "canary-1 vlagent is stopped"))
            self.assertEqual(FakeBrain.seen[-1], ("c" * 40, {"thread_id": THREAD, "author": OP, "content": "why?"}))
            self.assertEqual(logic.Brain(mk_cfg(n8n_chat_url=url, n8n_chat_token="w" * 40)).chat(THREAD, OP, "x")[0], 403)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(logic.Brain(mk_cfg(n8n_chat_url="http://127.0.0.1:1/x")).chat(THREAD, OP, "x")[0], 0)  # unreachable -> status 0


class EndToEndTests(unittest.TestCase):
    """The bot's Toolbelt client and planner against the REAL server: propose -> card -> decide -> run -> announce."""

    def setUp(self):
        self.rig = Rig()
        self.cfg = mk_cfg(toolbelt_url=self.rig.base)
        self.tb = logic.Toolbelt(self.cfg)
        self.state = logic.State(Path(tempfile.mkdtemp()) / "state.json")

    def tearDown(self):
        self.rig.close()

    def test_full_lifecycle(self):
        _, prop = self.rig.chat_proposal()
        st, feed = self.tb.feed(self.state.cursor)
        self.assertEqual(st, 200)
        plan = logic.plan_feed(feed["events"], self.state)
        self.assertEqual([(a.kind, a.proposal["id"]) for a in plan], [("post_card", prop["id"])])
        self.state.cursor = feed["next"]
        self.assertEqual(self.tb.set_message(prop["id"], "444444444444444444")[0], 200)
        # a stranger's press is refused by the Toolbelt even if the bot were fooled
        self.assertEqual(self.tb.decide(prop["id"], "approve", "333333333333333333", "i-0", prop["params_hash"])[0], 403)
        st, body = self.tb.decide(prop["id"], "approve", OP, "interaction-1", prop["params_hash"])
        self.assertEqual((st, body["state"]), (200, "approved"))
        done = self.rig.wait_state(prop["id"], "succeeded")
        self.assertEqual(done["state"], "succeeded")
        st, feed = self.tb.feed(self.state.cursor)
        kinds = [(a.kind, a.proposal["state"]) for a in logic.plan_feed(feed["events"], self.state)]
        self.assertEqual(kinds, [("edit_card", "succeeded"), ("announce", "succeeded")])
        self.assertIn("succeeded and verified", logic.result_summary(done))

    def test_kill_switch_and_status_through_the_client(self):
        self.assertEqual(self.tb.set_flag("kill_switch", True, "333333333333333333", "x")[0], 403)
        st, f = self.tb.set_flag("kill_switch", True, OP, "drill")
        self.assertEqual((st, f["value"]), (200, True))
        st, s = self.tb.status()
        self.assertTrue(logic.format_status(s).startswith("**Kill switch:** ENGAGED"))
        self.assertEqual(self.tb.set_flag("kill_switch", False, OP)[1]["value"], False)

    def test_replay_proposals_never_get_a_card(self):
        st, out = self.rig.agent("POST", "/ingest/zabbix", __import__("test_toolbelt_actions").zevent(), headers={"X-AIOPS-Replay": "canary-agent-down"})
        inc = out["incident_id"]
        diag = {"diagnosis": {"schema_version": "aiops.diagnosis/v1", "incident_id": inc, "layer": "unknown", "confidence": "low",
                              "summary": "Not enough to say which layer; the agent looks down.", "evidence": [], "needs_human": True,
                              "proposed_actions": [{"action_id": "restart-unit", "params": RESTART, "reason": "vlagent stopped"}]}, "model": "t"}
        self.assertEqual(self.rig.agent("POST", f"/diagnosis/{inc}", diag)[0], 200)
        self.rig.agent("POST", f"/group/{inc}/state", {"state": "running"})
        self.rig.agent("POST", f"/group/{inc}/state", {"state": "posted", "thread_id": THREAD})
        st, feed = self.tb.feed(0)
        self.assertTrue(feed["events"])
        self.assertEqual(logic.plan_feed(feed["events"], self.state), [])


if __name__ == "__main__":
    unittest.main()
