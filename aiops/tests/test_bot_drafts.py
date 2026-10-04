"""Phase 10h2: Ratatoskr's change-request cards and feed plan (aiops/bot/drafts.py), without Discord, plus the client against the
real Toolbelt server."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("bot", "toolbelt", "tools", "tests", "author"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import drafts  # noqa: E402
import logic  # noqa: E402
import test_change_requests as tcr  # noqa: E402

CR = {"id": 4, "state": "pending", "class": "docs", "title": "Draft the NVMe note @everyone", "body": "write it <@123456789012345678>", "source": "operator",
      "created_by": "1111", "allowed_paths": ["docs/incidents/**"], "pr_url": None, "error": None, "summary": None, "message_ref": None, "decided_by": None}


class Cards(unittest.TestCase):
    def test_custom_ids_round_trip_and_reject_garbage(self):
        self.assertEqual(drafts.parse_custom_id(drafts.custom_id("approve", 12)), ("approve", 12))
        for bad in ("aiops:approve:12:0123456789abcdef", "aiops:cr-delete:1", "aiops:cr-approve:x", ""):
            self.assertIsNone(drafts.parse_custom_id(bad))

    def test_buttons_follow_the_state(self):
        self.assertEqual(drafts.buttons(CR), ["approve", "reject"])
        self.assertEqual(drafts.buttons({**CR, "state": "running"}), ["cancel"])
        self.assertEqual(drafts.buttons({**CR, "state": "pr-open"}), [])

    def test_free_text_cannot_ping(self):
        c = drafts.card(CR)
        text = c["title"] + c["description"]
        self.assertNotIn("@everyone", text)
        self.assertNotIn("<@1234", text)

    def test_the_pr_link_and_error_show_when_present(self):
        c = drafts.card({**CR, "state": "pr-open", "pr_url": "https://github.com/XIIISins/homelab/pull/9", "summary": "did it"})
        self.assertIn("pull/9", " ".join(v for _, v, _ in c["fields"]))
        c = drafts.card({**CR, "state": "failed", "error": "refused before pushing: x"})
        self.assertIn("refused before pushing", " ".join(v for _, v, _ in c["fields"]))

    def test_announcements(self):
        self.assertIn("pull/9", drafts.announcement({**CR, "state": "pr-open", "pr_url": "https://github.com/XIIISins/homelab/pull/9"}))
        self.assertIn("did not become", drafts.announcement({**CR, "state": "failed", "error": "boom"}))


class Plan(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.state = drafts.State.load(Path(self.t.name))

    def tearDown(self):
        self.t.cleanup()

    def ev(self, **kw):
        return {"id": 1, "kind": "x", "change_request": {**CR, **kw}}

    def test_a_new_pending_request_gets_a_card_once(self):
        self.assertEqual([a.kind for a in drafts.plan([self.ev()], self.state)], ["post_card"])
        self.assertEqual(drafts.plan([self.ev(message_ref="55")], self.state)[0].kind, "edit_card")

    def test_results_are_announced_once_per_state(self):
        a = drafts.plan([self.ev(message_ref="55", state="pr-open", pr_url="https://github.com/XIIISins/homelab/pull/9")], self.state)
        self.assertEqual([x.kind for x in a], ["edit_card", "announce"])
        self.state.announced.add("4:pr-open")
        self.assertEqual([x.kind for x in drafts.plan([self.ev(message_ref="55", state="pr-open")], self.state)], ["edit_card"])
        self.assertEqual([x.kind for x in drafts.plan([self.ev(message_ref="55", state="merged")], self.state)], ["edit_card", "announce"])

    def test_decisions_by_the_operator_are_not_announced_and_cardless_non_pending_is_skipped(self):
        self.assertEqual([x.kind for x in drafts.plan([self.ev(message_ref="55", state="rejected")], self.state)], ["edit_card"])
        self.assertEqual(drafts.plan([self.ev(state="approved")], self.state), [])

    def test_state_survives_a_restart(self):
        self.state.cursor = 9
        self.state.announced.add("4:merged")
        self.state.save()
        again = drafts.State.load(Path(self.t.name))
        self.assertEqual((again.cursor, again.announced), (9, {"4:merged"}))


class Blocked(unittest.TestCase):
    CR_WAIT = {**CR, "id": 7, "state": "approved", "message_ref": "555",
               "blocked": {"why": "daily-budget", "detail": "the daily budget is used up (6 of 6 drafts started today; it resets at 00:00 UTC)", "since": 1}}

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.state = drafts.State.load(Path(self.t.name))

    def ev(self, kind="blocked", cr=None):
        return {"id": 3, "kind": kind, "ts": 1, "data": {"why": "daily-budget"}, "change_request": cr or self.CR_WAIT}

    def test_a_blocked_event_posts_one_notice_beside_the_card_and_not_again(self):
        acts = drafts.plan([self.ev()], self.state)
        self.assertEqual([a.kind for a in acts], ["edit_card", "notice"])
        self.assertEqual(acts[1].text, "7:blocked:daily-budget")
        self.state.announced.add(acts[1].text)  # what the bot does once it has posted it
        self.assertEqual([a.kind for a in drafts.plan([self.ev()], self.state)], ["edit_card"])

    def test_only_an_approved_request_with_a_card_gets_a_notice(self):
        self.assertEqual([a.kind for a in drafts.plan([self.ev(cr={**self.CR_WAIT, "state": "running"})], self.state)], ["edit_card"])
        self.assertEqual(drafts.plan([self.ev(cr={**self.CR_WAIT, "message_ref": None})], self.state), [])

    def test_the_card_shows_what_it_is_waiting_for(self):
        fields = dict((n, v) for n, v, _ in drafts.card(self.CR_WAIT)["fields"])
        self.assertIn("6 of 6 drafts", fields["Waiting because"])
        self.assertNotIn("Waiting because", dict((n, v) for n, v, _ in drafts.card({**self.CR_WAIT, "blocked": None})["fields"]))

    def test_the_text_cannot_ping(self):
        crm = {**self.CR_WAIT, "blocked": {"why": "x", "detail": "@everyone <@123456789012345678>"}}
        text = str(drafts.card(crm)["fields"])
        self.assertNotIn("@everyone", text)
        self.assertNotIn("<@1234", text)


class IncidentRequest(unittest.TestCase):
    def test_the_request_only_names_the_incident_and_fits_the_body_limit(self):
        title, body = drafts.incident_request(41)
        self.assertEqual(title, "Incident write-up for incident #41")
        self.assertIn('{"incident_id": 41}', body)
        self.assertIn("incident.draft", body)
        self.assertLess(len(body), 4000)
        self.assertLess(len(title), 120)
        for forbidden in ("decisions.md", "CLAUDE.md"):
            self.assertIn(forbidden, body)  # named only as files the session must NOT edit
        self.assertEqual(drafts.incident_request("7")[0], "Incident write-up for incident #7")  # always an int in the text

    def test_the_client_files_it_with_its_source_and_reference(self):
        rig = tcr.Rig()
        try:
            cfg = logic.Config(toolbelt_url=rig.base, approver_token=tcr.T_APPR, n8n_chat_url="", n8n_chat_token="", operator_ids=frozenset({tcr.OP}),
                               guild_id=1, diagnoses_channel_id=2, chat_channel_id=3)
            title, body = drafts.incident_request(41)
            st, cr = drafts.Client(cfg).create("docs", title, body, tcr.OP, "incident", "incident-41")
            self.assertEqual(st, 200, cr)
            got = rig.call(tcr.T_APPR, "GET", f"/change-requests/{cr['id']}")[1]
            self.assertEqual((got["source"], got["source_ref"], got["class"]), ("incident", "incident-41", "docs"))
        finally:
            rig.close()


class AgainstTheRealToolbelt(unittest.TestCase):
    def test_file_decide_and_feed_through_the_client(self):
        rig = tcr.Rig()
        try:
            cfg = logic.Config(toolbelt_url=rig.base, approver_token=tcr.T_APPR, n8n_chat_url="", n8n_chat_token="", operator_ids=frozenset({tcr.OP}),
                               guild_id=1, diagnoses_channel_id=2, chat_channel_id=3)
            c = drafts.Client(cfg)
            st, cr = c.create("docs", "Draft the NVMe note", "write it from the incident", tcr.OP)
            self.assertEqual((st, cr["state"]), (200, "pending"))
            st, feed = c.feed(0)
            self.assertEqual([e["kind"] for e in feed["events"]], ["created"])
            self.assertEqual(c.set_message(cr["id"], "555")[1]["message_ref"], "555")
            self.assertEqual(c.decide(cr["id"], "approve", tcr.OP, "interaction-1")[1]["state"], "approved")
            self.assertEqual(c.decide(cr["id"], "approve", "999", "x")[0] >= 400, True)  # not an operator
            self.assertEqual(c.list()[1]["change_requests"][0]["id"], cr["id"])
        finally:
            rig.close()


if __name__ == "__main__":
    unittest.main()
