"""Gná answers an operator in the chat channel without an @mention (aiops/bot/logic.py `directed_without_mention`, bot.py `on_message` and
`run_bot`). The privileged Message Content intent must be enabled in the Discord Developer Portal first; if it is not, the bot must
still come up (it is the only approver), in mention-only mode."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord

REPO = Path(__file__).resolve().parents[2]
for sub in ("bot", "toolbelt", "tools"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import bot  # noqa: E402
import logic  # noqa: E402

OP, OTHER, BOT_ID = 111, 222, 999
CHAT, DIAG, ELSEWHERE = 30, 20, 77


def cfg(flag=True, state_dir=None):
    return logic.Config(toolbelt_url="http://x:8090", approver_token="t", n8n_chat_url="http://y", n8n_chat_token="c", operator_ids=frozenset({str(OP)}),
                        guild_id=1, diagnoses_channel_id=DIAG, chat_channel_id=CHAT, chat_without_mention=flag, **({"state_dir": Path(state_dir)} if state_dir else {}))


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class Target:
    def __init__(self, tid=555):
        self.id, self.sent = tid, []

    def typing(self):
        return Typing()

    async def send(self, text, **kw):
        self.sent.append(text)


class Brain:
    def __init__(self):
        self.calls = []

    def chat(self, thread_id, author, content):
        self.calls.append((thread_id, author, content))
        return 200, {"reply": "ok"}


def message(content="why is the backup slow?", author=OP, channel=CHAT, parent=None, mention=False, is_bot=False):
    mentions = [SimpleNamespace(id=BOT_ID)] if mention else []
    return SimpleNamespace(author=SimpleNamespace(id=author, bot=is_bot), content=("<@%d> " % BOT_ID if mention else "") + content, mentions=mentions,
                           channel=SimpleNamespace(id=channel, parent_id=parent))


class Gna:
    """A stand-in for the Ratatoskr instance on_message runs against."""

    def __init__(self, c):
        me = SimpleNamespace(id=BOT_ID)
        message.me = me
        self.user, self.cfg, self.locks, self.brain, self.target = me, c, {}, Brain(), Target()

    async def _chat_target(self, msg):
        return self.target

    def deliver(self, msg):
        # `self.user in message.mentions` compares objects: reuse the same user object
        msg.mentions = [self.user] if msg.mentions else []
        asyncio.run(bot.Ratatoskr.on_message(self, msg))
        return self.brain.calls


class Permission(unittest.TestCase):
    def test_the_matrix(self):
        c = cfg()
        d = lambda **k: logic.directed_without_mention(c, k.get("a", OP), k.get("ch", CHAT), k.get("p"))  # noqa: E731
        self.assertTrue(d())                                  # an operator in the chat channel
        self.assertTrue(d(ch=555, p=CHAT))                    # ...or in a thread under it
        self.assertFalse(d(a=OTHER))                          # anyone else still needs a mention
        self.assertFalse(d(ch=555, p=DIAG))                   # diagnosis threads keep mention-only
        self.assertFalse(d(ch=ELSEWHERE))
        self.assertFalse(d(ch=DIAG))
        self.assertFalse(logic.directed_without_mention(cfg(flag=False), OP, CHAT, None))   # off by default

    def test_the_flag_is_read_from_the_static_config_and_defaults_off(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            (p / "sec.json").write_text(json.dumps({"operator_user_id": "111", "guild_id": 1, "diagnoses_channel_id": 2, "chat_channel_id": 3, "token": "t"}))
            (p / "a").write_text("tok\n")
            for static, want in (({}, False), ({"chat_without_mention": True}, True)):
                (p / "static.json").write_text(json.dumps({"toolbelt_url": "http://x:8090", "n8n_chat_url": "http://y", **static}))
                c, _ = logic.load_config(str(p / "static.json"), str(p / "sec.json"), str(p / "a"), str(p / "a"))
                self.assertIs(c.chat_without_mention, want)


class OnMessage(unittest.TestCase):
    def test_off_by_default_an_unmentioned_message_is_ignored_and_a_mention_still_works(self):
        g = Gna(cfg(flag=False))
        self.assertEqual(g.deliver(message()), [])
        self.assertEqual(len(g.deliver(message(mention=True))), 1)

    def test_on_an_operator_without_a_mention_in_the_chat_channel_is_answered(self):
        g = Gna(cfg())
        calls = g.deliver(message("why is the backup slow?"))
        self.assertEqual(calls, [("555", str(OP), "why is the backup slow?")])
        self.assertEqual(g.target.sent, ["ok"])

    def test_a_thread_under_the_chat_channel_is_answered_too(self):
        self.assertEqual(len(Gna(cfg()).deliver(message(channel=555, parent=CHAT))), 1)

    def test_everything_else_without_a_mention_is_ignored(self):
        for kw in ({"author": OTHER}, {"channel": 555, "parent": DIAG}, {"channel": ELSEWHERE}, {"channel": DIAG}, {"is_bot": True}):
            self.assertEqual(Gna(cfg()).deliver(message(**kw)), [], kw)

    def test_a_non_operator_who_mentions_is_still_served_as_before(self):
        self.assertEqual(len(Gna(cfg()).deliver(message(author=OTHER, mention=True))), 1)

    def test_an_empty_message_gets_no_nag_but_an_empty_mention_gets_the_hint(self):
        g = Gna(cfg())
        self.assertEqual(g.deliver(message(content="")), [])
        self.assertEqual(g.target.sent, [])
        g2 = Gna(cfg())
        self.assertEqual(g2.deliver(message(content="", mention=True)), [])
        self.assertEqual(len(g2.target.sent), 1)
        self.assertIn("Mention me", g2.target.sent[0])


class Intents(unittest.TestCase):
    def test_message_content_is_requested_only_when_the_feature_is_on(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertFalse(bot.Ratatoskr(cfg(flag=False, state_dir=t)).intents.message_content)
            self.assertTrue(bot.Ratatoskr(cfg(flag=True, state_dir=t)).intents.message_content)


class Fallback(unittest.TestCase):
    def setUp(self):
        self.runs = []
        outer = self

        class FakeBot:
            def __init__(self, c):
                self.c = c

            def run(self, token, log_handler=None):
                outer.runs.append(self.c.chat_without_mention)
                if self.c.chat_without_mention:
                    raise discord.PrivilegedIntentsRequired(None)

        self.real, bot.Ratatoskr = bot.Ratatoskr, FakeBot
        self.addCleanup(lambda: setattr(bot, "Ratatoskr", self.real))

    def test_a_missing_portal_toggle_leaves_the_approver_running_in_mention_only_mode(self):
        c = cfg(flag=True)
        bot.run_bot(c, "tok")
        self.assertEqual(self.runs, [True, False])           # tried with the intent, then came up without it
        self.assertFalse(c.chat_without_mention)

    def test_without_the_feature_a_privileged_intent_error_is_not_swallowed(self):
        class AlwaysFails:
            def __init__(self, c):
                pass

            def run(self, token, log_handler=None):
                raise discord.PrivilegedIntentsRequired(None)

        bot.Ratatoskr = AlwaysFails
        with self.assertRaises(discord.PrivilegedIntentsRequired):
            bot.run_bot(cfg(flag=False), "tok")


if __name__ == "__main__":
    unittest.main()
