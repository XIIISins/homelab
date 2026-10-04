"""Phase 10h1: Ratatoskr's forecast cards, event plan and client (aiops/bot/fcast.py), without Discord, plus the client against the real
Toolbelt server."""
from __future__ import annotations

import json
import py_compile
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("bot", "toolbelt", "tools", "tests"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import fcast  # noqa: E402
import forecast_store as fst  # noqa: E402
import logic  # noqa: E402
import test_change_requests as tcr  # noqa: E402
import test_forecast_store as tfs  # noqa: E402

NOW = 1_800_000_000


def fc(**kw):
    base = {"id": 3, "fingerprint": "forecast:slow-fill:m:t", "kind": "slow-fill", "metric": "vfs.fs.dependent.size[*,pused]", "target": "hugin:/",
            "state": "open", "confidence": "high", "days_to_full": 9.6, "ratio": None, "first_seen": NOW - 86400, "last_seen": NOW,
            "eta_at": int(NOW + 9.6 * 86400), "label": None, "message_ref": None,
            "evidence": {"evidence": {"slope_per_day": 0.0123, "current": 0.8, "capacity": 0.9, "r2": 0.97, "points": 15}, "ratio": None}}
    base.update(kw)
    return base


class Cards(unittest.TestCase):
    def test_custom_ids_round_trip_and_reject_garbage(self):
        self.assertEqual(fcast.parse_custom_id(fcast.custom_id("useful", 12)), ("useful", 12))
        for bad in ("aiops:fc-delete:1", "aiops:fc-noise:x", "aiops:cr-approve:1", "", "aiops:fc-noise:1:2"):
            self.assertIsNone(fcast.parse_custom_id(bad))

    def test_buttons_only_while_open_and_unlabelled(self):
        self.assertEqual(fcast.buttons(fc()), ["useful", "noise", "draft"])               # a filesystem fill has a fix in the repository
        self.assertEqual(fcast.buttons(fc(label="noise")), ["draft"])
        self.assertEqual(fcast.buttons(fc(state="resolved")), [])
        self.assertEqual(fcast.buttons(fc(metric="proxmox.node.disk/maxdisk")), ["useful", "noise"])   # the PBS datastore / NAS: no fix in the repo

    def test_a_slow_fill_card_says_it_is_a_heads_up_and_gives_the_numbers(self):
        c = fcast.card(fc())
        self.assertTrue(c["description"].startswith("**A heads-up, not an alert.**"))
        for needle in ("90%", "10 day(s)", "1.2 points a day"):
            self.assertIn(needle, c["description"])
        fields = dict((n, v) for n, v, _ in c["fields"])
        self.assertEqual(fields["Signal"], "Filesystem fill")
        self.assertEqual(fields["Target"], "`hugin:/`")
        self.assertIn("80% of a 90% limit", fields["Now"])
        self.assertIn("around", fields["Estimated"])
        self.assertIn("forecast 3", c["footer"])

    def test_urgency_colours_the_card_and_a_resolved_one_goes_quiet(self):
        self.assertEqual(fcast.card(fc(days_to_full=1.5))["colour"], fcast.COLOUR["hot"])
        self.assertEqual(fcast.card(fc(days_to_full=5))["colour"], fcast.COLOUR["soon"])
        self.assertEqual(fcast.card(fc(days_to_full=12))["colour"], fcast.COLOUR["later"])
        q = fcast.card(fc(state="resolved"))
        self.assertEqual(q["colour"], fcast.COLOUR["quiet"])
        self.assertIn("no longer forecast", q["footer"])

    def test_a_fast_rise_has_no_date_and_says_how_fast(self):
        f = fc(kind="fast-rise", days_to_full=None, eta_at=None, ratio=6.0, metric="vl_data_size_bytes",
               evidence={"evidence": {"recent_rate_per_hour": 2.5e9}, "ratio": 6.0})
        c = fcast.card(f)
        self.assertIn("about 6x", c["description"])
        self.assertIn("2.5e+09", c["description"])
        self.assertIn("no date", dict((n, v) for n, v, _ in c["fields"])["Estimated"])

    def test_under_a_day_is_in_hours(self):
        self.assertIn("12 hour(s)", fcast.eta_text(fc(days_to_full=0.5)))

    def test_a_label_shows_and_free_text_cannot_ping(self):
        self.assertIn("`noise`", str(fcast.card(fc(label="noise"))["fields"]))
        c = fcast.card(fc(target="@everyone <@123456789012345678>"))
        text = c["title"] + str(c["fields"])
        self.assertNotIn("@everyone", text)
        self.assertNotIn("<@1234", text)

    def test_notices_say_what_changed(self):
        self.assertIn("now about 5 day(s)", fcast.notice("escalated", fc(), {"days_to_full": 4.6, "from_days": 10}))
        self.assertIn("was 10", fcast.notice("escalated", fc(), {"days_to_full": 4.6, "from_days": 10}))
        self.assertIn("Still open", fcast.notice("reposted", fc(), {}))
        self.assertIn("no longer forecast", fcast.notice("resolved", fc(), {}))
        self.assertEqual(fcast.notice("created", fc(), {}), "")

    def test_the_bot_module_at_least_compiles(self):
        py_compile.compile(str(REPO / "aiops/bot/bot.py"), doraise=True)
        py_compile.compile(str(REPO / "aiops/bot/fcast.py"), doraise=True)


class Plan(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.state = fcast.State.load(Path(self.t.name))

    def ev(self, kind, eid, **f):
        return {"id": eid, "kind": kind, "ts": 1, "data": {"days_to_full": 4, "from_days": 9}, "forecast": fc(**f)}

    def test_created_posts_a_card_once_and_a_restart_does_not_repeat_it(self):
        acts = fcast.plan([self.ev("created", 1)], self.state)
        self.assertEqual([(a.kind, a.key) for a in acts], [("post_card", "e1")])
        self.state.done.add("e1")
        self.state.cursor = 1
        self.state.save()
        again = fcast.State.load(Path(self.t.name))
        self.assertEqual((again.cursor, again.done), (1, {"e1"}))
        self.assertEqual(fcast.plan([self.ev("created", 1)], again), [])

    def test_escalated_reposted_and_resolved_edit_the_card_and_add_a_notice(self):
        for kind in ("escalated", "reposted", "resolved"):
            acts = fcast.plan([self.ev(kind, 5, message_ref="555")], self.state)
            self.assertEqual([a.kind for a in acts], ["edit_card", "notice"], kind)
            self.assertTrue(acts[1].text)
        self.assertEqual([a.kind for a in fcast.plan([self.ev("escalated", 6)], self.state)], ["notice"])  # no card to edit yet

    def test_a_label_refreshes_the_card_and_nothing_else(self):
        self.assertEqual([a.kind for a in fcast.plan([self.ev("labeled", 7, message_ref="555", label="useful")], self.state)], ["edit_card"])
        self.assertEqual(fcast.plan([self.ev("labeled", 8)], self.state), [])


class AgainstTheRealToolbelt(unittest.TestCase):
    def test_list_label_get_message_and_feed_through_the_client(self):
        r = tcr.Rig()
        try:
            tb = r.tb
            tb.fc = fst.Forecasts(tb.db, tb._lock, tb.clock, tb.audit)
            tb.fc.sync({"ts": r.clock.t, "findings": [tfs.finding(days=4)], "stats": {}})
            cfg = logic.Config(toolbelt_url=r.base, approver_token=tcr.T_APPR, n8n_chat_url="", n8n_chat_token="", operator_ids=frozenset({tcr.OP}),
                               guild_id=1, diagnoses_channel_id=2, chat_channel_id=3)
            c = fcast.Client(cfg)
            st, feed = c.feed(0)
            self.assertEqual((st, [e["kind"] for e in feed["events"]]), (200, ["created"]))
            self.assertEqual(fcast.plan(feed["events"], fcast.State(Path("/tmp/none.json")))[0].kind, "post_card")
            self.assertEqual(c.set_message(1, "555", "777")[1]["message_ref"], "555")
            st, listed = c.list()
            self.assertEqual((st, listed["forecasts"][0]["id"]), (200, 1))
            st, labelled = c.label(1, "useful", tcr.OP)
            self.assertEqual((st, labelled["label"]), (200, "useful"))
            self.assertEqual(c.label(1, "noise", "999")[0], 403)
            self.assertEqual(c.get(99)[0], 404)
            self.assertEqual(fcast.buttons(c.get(1)[1]), ["draft"] if c.get(1)[1]["metric"] in fcast.REMEDY_METRICS else [])
        finally:
            r.close()


class Config(unittest.TestCase):
    def test_the_forecasts_channel_is_optional(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            (p / "static.json").write_text(json.dumps({"toolbelt_url": "http://x:8090", "n8n_chat_url": "http://y"}))
            (p / "sec.json").write_text(json.dumps({"operator_user_id": "111", "guild_id": 1, "diagnoses_channel_id": 2, "chat_channel_id": 3, "token": "t"}))
            (p / "a").write_text("tok\n")
            cfg, _ = logic.load_config(str(p / "static.json"), str(p / "sec.json"), str(p / "a"), str(p / "a"))
            self.assertEqual(cfg.forecasts_channel_id, 0)
            (p / "static.json").write_text(json.dumps({"toolbelt_url": "http://x:8090", "n8n_chat_url": "http://y", "forecasts_channel_id": 99}))
            cfg, _ = logic.load_config(str(p / "static.json"), str(p / "sec.json"), str(p / "a"), str(p / "a"))
            self.assertEqual(cfg.forecasts_channel_id, 99)


if __name__ == "__main__":
    unittest.main()
