"""Ratatoskr is the only approver, and Discord refuses the WHOLE command sync (so the bot crash-loops) if one slash-command
description is over 100 characters (found 2026-10-04 on `/aiops draft-incident`). This reads aiops/bot/bot.py statically
(no discord import needed) and holds every command, option description and choice label to Discord's limits."""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

BOT = Path(__file__).resolve().parents[2] / "aiops" / "bot" / "bot.py"
NAME = re.compile(r"^[\w-]{1,32}$")


def literals():
    """(kind, text) for every string constant in the places Discord measures."""
    tree = ast.parse(BOT.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = ast.unparse(node.func)
        if fn.endswith(".command") or fn == "app_commands.command":
            for kw in node.keywords:
                if kw.arg in ("name", "description") and isinstance(kw.value, ast.Constant):
                    yield ("command-" + kw.arg, kw.value.value)
        elif fn == "app_commands.describe":
            for kw in node.keywords:
                if isinstance(kw.value, ast.Constant):
                    yield ("option-description", kw.value.value)
        elif fn == "app_commands.Choice":
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    yield ("choice-name", kw.value.value)


class DiscordLimits(unittest.TestCase):
    def test_the_scan_finds_the_commands(self):
        kinds = {k for k, _ in literals()}
        self.assertTrue({"command-name", "command-description", "option-description", "choice-name"} <= kinds, kinds)

    def test_descriptions_and_choice_names_are_1_to_100_characters(self):
        for kind, text in literals():
            if kind != "command-name":
                self.assertTrue(1 <= len(text) <= 100, f"{kind} is {len(text)} characters (Discord allows 1-100): {text!r}")

    def test_command_names_are_lowercase_slugs_up_to_32(self):
        for kind, text in literals():
            if kind == "command-name":
                self.assertRegex(text, NAME)
                self.assertEqual(text, text.lower(), text)


if __name__ == "__main__":
    unittest.main()
