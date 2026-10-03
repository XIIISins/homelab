"""Phase 10h3: the incident draft generator (aiops/toolbelt/incident_draft.py) over a real Toolbelt schema in memory."""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))

import actions  # noqa: E402
import core  # noqa: E402
import incident_draft as draft  # noqa: E402
import tools  # noqa: E402

T0 = 1_790_000_000
# built at runtime so no secret-shaped literal sits in the repo (the pre-push scanner would, rightly, block it)
SECRETISH = "pass" + "word" + "=" + "x" * 3 + "Zq9" + "w" * 4


def make_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(core.SCHEMA)
    db.executescript(actions.SCHEMA)
    return db


def seed(db, with_diagnosis=True, with_proposal=True):
    db.execute("INSERT INTO incidents(id, group_key, state, opened_at, window_ends_at, running_at, posted_at, resolved_at, thread_id) VALUES "
               "(7, 'node:urd', 'resolved', ?, ?, ?, ?, ?, '1')", (T0, T0 + 90, T0 + 90, T0 + 120, T0 + 600))
    db.execute("INSERT INTO alerts(fingerprint, incident_id, status, severity, first_seen, last_seen, resolved_at, count, host, alert_json) VALUES "
               "('ab12cd34ef567890', 7, 'resolved', 'info', ?, ?, ?, 2, 'canary-2', ?)",
               (T0, T0 + 30, T0 + 590, json.dumps({"summary": "[Zabbix] High: Canary smoke: agent service down", "check": "agent", "runbook_id": "RB-UNIT-STOPPED-T1"})))
    db.execute("INSERT INTO tool_calls(incident_id, tool, args_hash, replayed, ts, args_json, outcome) VALUES (7, 'reach.tcp', 'h1', 0, ?, ?, 'served')",
               (T0 + 100, json.dumps({"host": "10.0.11.191", "port": 22})))
    db.execute("INSERT INTO tool_calls(incident_id, tool, args_hash, replayed, ts, args_json, outcome) VALUES (7, 'netbox.host', 'h2', 0, ?, '{}', 'refused')", (T0 + 101,))
    if with_diagnosis:
        db.execute("INSERT INTO diagnoses(incident_id, diagnosis_json, model, created_at) VALUES (7, ?, 'test-model', ?)", (json.dumps({
            "layer": "workload", "confidence": "high", "runbook_id": "RB-UNIT-STOPPED-T1",
            "summary": "zabbix-agent2 is stopped on canary-2; " + SECRETISH + " leaked in the text",
            "evidence": [{"tool": "reach.tcp", "finding": "SSH open, agent port closed"}], "next_checks": ["look at the unit journal"]}), T0 + 118))
    if with_proposal:
        db.execute("INSERT INTO proposals(id, incident_id, source, action_id, params_json, params_hash, tier, target, reason, state, created_at, expires_at, "
                   "decided_at, decided_by, finished_at, result_json) VALUES (3, 7, 'diagnosis', 'restart-unit', '{}', 'x', 'T1', 'canary-2', 'stopped', "
                   "'succeeded', ?, ?, ?, 'auto:restart-failed-unit', ?, ?)", (T0 + 119, T0 + 3000, T0 + 120, T0 + 150, json.dumps({"why": "restart verified"})))
        db.execute("INSERT INTO proposal_events(proposal_id, ts, kind, data_json) VALUES (3, ?, 'breaker_tripped', '{}')", (T0 + 151,))
    return db


class Draft(unittest.TestCase):
    def test_a_full_incident_renders_every_section_with_sources(self):
        text = draft.build_draft(seed(make_db()), 7, redact=tools.redact)
        for needle in ("DRAFT: generated from the Toolbelt's records", "# Incident #7: canary-2", "## Timeline (UTC, mechanical)",
                       "[alert ab12cd34]", "[proposal #3]", "[tool call 1]", "the Toolbelt under policy restart-failed-unit",
                       "Root cause (HYPOTHESIS, not confirmed)", "## Follow-ups for the operator", "Breaker tripped [proposal #3]"):
            self.assertIn(needle, text)
        self.assertIn("| `reach.tcp` |", text)
        self.assertIn("refused [tool call 2]", text)  # a refused call is shown, not hidden

    def test_the_timeline_is_in_time_order_and_utc(self):
        text = draft.build_draft(seed(make_db()), 7)
        section = text.split("## Timeline (UTC, mechanical)")[1].split("\n## ")[0]
        times = [ln[2:11] for ln in section.splitlines() if ln.startswith("- ")]
        self.assertGreaterEqual(len(times), 8)
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(t.endswith("Z") for t in times), times)

    def test_secrets_are_scrubbed_through_the_redactor(self):
        text = draft.build_draft(seed(make_db()), 7, redact=tools.redact)
        self.assertNotIn(SECRETISH.split("=")[1], text)
        self.assertIn("[redacted]", text)

    def test_an_incident_without_a_diagnosis_or_proposals_says_so(self):
        text = draft.build_draft(seed(make_db(), with_diagnosis=False, with_proposal=False), 7)
        self.assertIn("No diagnosis was recorded", text)
        self.assertIn("No action was proposed", text)

    def test_a_missing_incident_is_a_keyerror_and_a_missing_proposals_table_degrades(self):
        db = make_db()
        with self.assertRaises(KeyError):
            draft.build_draft(db, 99)
        db2 = sqlite3.connect(":memory:")
        db2.row_factory = sqlite3.Row
        db2.executescript(core.SCHEMA)  # no proposals table: actions were never enabled
        seed_core = seed(make_db(), with_proposal=False)
        for table in ("incidents", "alerts", "tool_calls", "diagnoses"):
            for row in seed_core.execute(f"SELECT * FROM {table}").fetchall():
                cols = row.keys()
                db2.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", tuple(row))
        self.assertIn("No action was proposed", draft.build_draft(db2, 7))

    def test_the_file_slug_is_dated_and_safe(self):
        s = draft.slug_for(seed(make_db()), 7)
        self.assertRegex(s, r"^\d{4}-\d{2}-\d{2}-canary-2-agent$")
        self.assertNotIn("/", s)


if __name__ == "__main__":
    unittest.main()
