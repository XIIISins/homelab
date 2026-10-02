"""Phase 10d2: native Zabbix events -> alerts (aiops/tools/zabbix_event.py).

Covers the adapter's behaviour, the Zabbix local-time fix in normalize, the
event schema, and that the lint check for the sender/schema contract bites.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "tools"))

import lint  # noqa: E402
import normalize  # noqa: E402
import zabbix_event  # noqa: E402

CASES = REPO / "aiops" / "fixtures" / "zabbix-native"
ROUTES = normalize.load_routes()
KNOWN = {r["id"] for r in yaml.safe_load((REPO / "aiops" / "runbooks.yml").read_text())["runbooks"]}
EV_SCHEMA = lint.load_schema("zabbix-event.v1.schema.json")


def case(name: str) -> dict:
    return json.loads((CASES / f"{name}.json").read_text())


class ZabbixLocalTime(unittest.TestCase):
    """Hugin runs Europe/Amsterdam: {EVENT.DATE} {EVENT.TIME} are local, not UTC."""

    def test_summer_time_is_utc_plus_two(self):
        self.assertEqual(normalize._zbx_ts("2026.10.01 12:34:56"), "2026-10-01T10:34:56Z")

    def test_winter_time_is_utc_plus_one(self):
        self.assertEqual(normalize._zbx_ts("2026.12.01 10:00:00"), "2026-12-01T09:00:00Z")

    def test_garbage_is_none(self):
        self.assertIsNone(normalize._zbx_ts("yesterday"))
        self.assertIsNone(normalize._zbx_ts("2026.13.45 99:99:99"))


class Adapter(unittest.TestCase):
    def test_every_fixture_matches(self):
        for p in sorted(CASES.glob("*.json")):
            c = json.loads(p.read_text())
            got = zabbix_event.from_zabbix_event(c["event"], c["received_at"], ROUTES, KNOWN)
            self.assertEqual(got, c["expected"], p.name)

    def test_same_problem_has_the_same_fingerprint_on_both_paths(self):
        """The property that makes dedupe/resolve work while both paths exist."""
        native = case("native-high-problem-routed")["expected"][0]
        hermod = json.loads((REPO / "aiops/fixtures/cases/zabbix-high-problem.json").read_text())["expected"][0]
        self.assertEqual(native["fingerprint"], hermod["fingerprint"])
        self.assertEqual(native["fired_at"], hermod["fired_at"])  # both UTC now

    def test_resolved_half_keeps_the_fingerprint(self):
        firing = case("native-high-problem-routed")["expected"][0]
        resolved = case("native-high-resolved")["expected"][0]
        self.assertEqual(firing["fingerprint"], resolved["fingerprint"])
        self.assertEqual((firing["status"], resolved["status"]), ("firing", "resolved"))
        self.assertEqual(resolved["resolved_at"], "2026-10-01T10:39:10Z")

    def test_canary_is_diagnosable_but_capped_at_info(self):
        a = case("native-canary-high-info")["expected"][0]
        self.assertEqual((a["severity"], a["labels"]["aiops_canary"]), ("info", "true"))
        self.assertEqual(a["native_severity"], "High")  # the real severity is not lost

    def test_warning_is_not_an_alert(self):
        c = case("native-warning-not-alertable")
        self.assertEqual(zabbix_event.from_zabbix_event(c["event"], c["received_at"], ROUTES, KNOWN), [])

    def test_known_tag_overrides_routing_and_records_what_routing_said(self):
        a = case("native-disaster-tag-override")["expected"][0]
        self.assertEqual(a["runbook_id"], "RB-ETCD-RAFT-SYSLOG-FLOOD")
        self.assertEqual(a["labels"]["runbook_id_routed"], "RB-ZBX-TRIAGE")
        self.assertEqual(a["labels"]["runbook_id_source"], "trigger-tag")

    def test_unknown_tag_never_points_the_agent_at_a_missing_runbook(self):
        a = case("native-unknown-tag-ignored")["expected"][0]
        self.assertEqual(a["runbook_id"], "RB-ZBX-TRIAGE")
        self.assertEqual(a["labels"]["runbook_id_tag_ignored"], "RB-DOES-NOT-EXIST")

    def test_without_a_registry_any_wellformed_tag_is_accepted(self):
        c = case("native-unknown-tag-ignored")
        a = zabbix_event.from_zabbix_event(c["event"], c["received_at"], ROUTES, None)[0]
        self.assertEqual(a["runbook_id"], "RB-DOES-NOT-EXIST")

    def test_labels_are_all_strings_and_bounded(self):
        c = copy.deepcopy(case("native-high-problem-routed"))
        c["event"]["opdata"] = "x" * 5000
        a = zabbix_event.from_zabbix_event(c["event"], c["received_at"], ROUTES, KNOWN)[0]
        self.assertTrue(all(isinstance(v, str) for v in a["labels"].values()))
        self.assertLessEqual(len(a["labels"]["zabbix_opdata"]), zabbix_event._LABEL_MAX)


class EventSchema(unittest.TestCase):
    def good(self):
        return copy.deepcopy(case("native-high-problem-routed")["event"])

    def test_good_event_validates(self):
        self.assertEqual(lint.schema_errors(self.good(), EV_SCHEMA), [])

    def test_missing_event_id_rejected(self):
        e = self.good(); del e["event_id"]
        self.assertTrue(lint.schema_errors(e, EV_SCHEMA))

    def test_unknown_field_rejected(self):
        e = self.good(); e["surprise"] = "x"
        self.assertTrue(lint.schema_errors(e, EV_SCHEMA))

    def test_resolved_needs_the_recovery_event_id(self):
        e = self.good(); e["status"] = "RESOLVED"; del e["recovery_event_id"]
        self.assertTrue(lint.schema_errors(e, EV_SCHEMA))

    def test_non_utc_timestamp_rejected(self):
        e = self.good(); e["fired_at"] = "2026-10-01T12:34:56+02:00"
        self.assertTrue(lint.schema_errors(e, EV_SCHEMA))

    def test_malformed_runbook_id_rejected(self):
        e = self.good(); e["runbook_id"] = "rm -rf /"
        self.assertTrue(lint.schema_errors(e, EV_SCHEMA))

    def test_unknown_severity_rejected(self):
        e = self.good(); e["severity"] = "Catastrophic"
        self.assertTrue(lint.schema_errors(e, EV_SCHEMA))


class SenderContract(unittest.TestCase):
    """n8n-webhook.js and the schema must describe the same payload."""

    JS = REPO / "ansible/roles/zabbix-server/templates/n8n-webhook.js"

    def tree(self, js_text: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        dst = tmp / "ansible/roles/zabbix-server/templates"
        dst.mkdir(parents=True)
        (dst / "n8n-webhook.js").write_text(js_text)
        return tmp

    def test_committed_script_and_schema_agree(self):
        self.assertEqual(lint.check_zabbix_native(REPO, ROUTES, KNOWN), [])

    def test_key_extraction_finds_the_payload(self):
        self.assertGreaterEqual(len(lint.js_payload_keys(self.JS.read_text())), 20)

    def test_a_new_script_field_without_a_schema_entry_is_caught(self):
        js = self.JS.read_text().replace("        sent_at: nowIsoUtc()\n", "        sent_at: nowIsoUtc(),\n        sneaky_extra: 'x'\n")
        errs = lint.check_zabbix_native(self.tree(js), ROUTES, KNOWN)
        self.assertTrue(any("sneaky_extra" in e for e in errs), errs)

    def test_a_dropped_script_field_is_caught(self):
        js = self.JS.read_text().replace("        host_ip: clean(p.host_ip),\n", "")
        errs = lint.check_zabbix_native(self.tree(js), ROUTES, KNOWN)
        self.assertTrue(any("host_ip" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
