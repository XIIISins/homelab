"""Phase 10d3: the diagnosis contract - structure, grounding in the audit trail, rendering."""
from __future__ import annotations

import copy
import ipaddress
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import jsonschema

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))
sys.path.insert(0, str(REPO / "aiops" / "tools"))

import core  # noqa: E402
import diagnosis  # noqa: E402
import lint  # noqa: E402
import normalize  # noqa: E402
import server  # noqa: E402
import test_toolbelt as base  # noqa: E402
import tools  # noqa: E402

SCHEMA = lint.load_schema("diagnosis.v1.schema.json")
ACTIONS = server.load_action_ids(REPO)


def good(incident_id=1):
    return {
        "schema_version": "aiops.diagnosis/v1", "incident_id": incident_id, "layer": "workload", "confidence": "medium",
        "summary": "The triage runbook applies; nothing points at the host or hypervisor.",
        "reasoning": "The runbook lookup matches the alert's check and no other layer shows a fault.",
        "evidence": [{"tool": "registry.runbook", "args": {"id": "RB-ZBX-TRIAGE"}, "finding": "catch-all triage runbook, stub maturity"}],
        "known_issue_refs": ["docs/known-issues/zabbix.md"], "runbook_id": "RB-ZBX-TRIAGE",
        "proposed_actions": [{"action_id": sorted(ACTIONS)[0], "reason": "read-only status check"}],
        "next_checks": ["look at the service logs"], "needs_human": False,
    }


class Structure(unittest.TestCase):
    def mutations(self):
        g = good()
        out = {"good": g}
        for name, fn in {
            "no-summary": lambda d: d.pop("summary"),
            "short-summary": lambda d: d.update(summary="short"),
            "bad-layer": lambda d: d.update(layer="cloud"),
            "bad-confidence": lambda d: d.update(confidence="certain"),
            "extra-field": lambda d: d.update(execute="rm -rf /"),
            "bad-version": lambda d: d.update(schema_version="v2"),
            "incident-zero": lambda d: d.update(incident_id=0),
            "incident-bool": lambda d: d.update(incident_id=True),
            "needs-human-str": lambda d: d.update(needs_human="no"),
            "too-much-evidence": lambda d: d.update(evidence=[d["evidence"][0]] * 13),
            "evidence-extra-key": lambda d: d["evidence"][0].update(raw="x"),
            "evidence-bad-tool": lambda d: d["evidence"][0].update(tool="Kube Get"),
            "evidence-args-list": lambda d: d["evidence"][0].update(args=[]),
            "short-finding": lambda d: d["evidence"][0].update(finding="x"),
            "ref-outside-docs": lambda d: d.update(known_issue_refs=["../etc/passwd"]),
            "ref-not-md": lambda d: d.update(known_issue_refs=["docs/known-issues/zabbix.txt"]),
            "too-many-refs": lambda d: d.update(known_issue_refs=["docs/known-issues/a.md"] * 6),
            "bad-runbook": lambda d: d.update(runbook_id="rb-lower"),
            "bad-action": lambda d: d["proposed_actions"][0].update(action_id="Restart Everything"),
            "four-actions": lambda d: d.update(proposed_actions=[d["proposed_actions"][0]] * 4),
            "action-extra": lambda d: d["proposed_actions"][0].update(command="reboot"),
            "six-checks": lambda d: d.update(next_checks=["abc"] * 6),
            "layer-without-evidence": lambda d: d.update(evidence=[]),
        }.items():
            d = copy.deepcopy(g)
            fn(d)
            out[name] = d
        unknown = copy.deepcopy(g)
        unknown.update(layer="unknown", evidence=[])
        out["unknown-without-evidence-is-fine"] = unknown
        return out

    def test_the_validator_and_the_json_schema_agree_on_every_case(self):
        validator = jsonschema.Draft202012Validator(SCHEMA)
        for name, d in self.mutations().items():
            schema_ok = not list(validator.iter_errors(d))
            ours_ok = not diagnosis.structure(d)
            if name == "layer-without-evidence":  # stateful rule the schema documents but cannot express
                self.assertTrue(schema_ok and not ours_ok, name)
                continue
            self.assertEqual(schema_ok, ours_ok, f"{name}: schema={schema_ok} validator={ours_ok} {diagnosis.structure(d)}")

    def test_good_passes_and_every_bad_mutation_fails(self):
        for name, d in self.mutations().items():
            problems = diagnosis.structure(d)
            if name in ("good", "unknown-without-evidence-is-fine"):
                self.assertEqual(problems, [], name)
            else:
                self.assertTrue(problems, f"{name} should have failed")

    def test_not_an_object(self):
        for bad in ("text", [], None, 3):
            self.assertEqual(diagnosis.structure(bad), ["diagnosis must be a JSON object"])


class Grounding(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.tmp / "repo" / "docs" / "known-issues").mkdir(parents=True)
        (self.tmp / "repo" / "docs" / "known-issues" / "zabbix.md").write_text("x")
        audit: list[dict] = []
        c = core.Config(replay_dir=self.tmp / "replays", live=tools.LiveConfig(root=REPO, repo_dir=self.tmp / "repo"))
        self.tb = core.Toolbelt(c, normalize.load_routes(), base.KNOWN, clock=base.Clock(), audit=audit.append, action_ids=ACTIONS)
        self.audit = audit
        self.inc = self.tb.ingest_zabbix(base.ev())["incident_id"]
        self.tb.call_tool("registry.runbook", {"id": "RB-ZBX-TRIAGE"}, self.inc)

    def post(self, d):
        return self.tb.diagnose(self.inc, {"diagnosis": d, "model": "test-model"})

    def problems(self, d):
        with self.assertRaises(core.Rejected) as cm:
            self.post(d)
        self.assertEqual(cm.exception.status, 422)
        return cm.exception.detail["problems"]

    def test_a_grounded_diagnosis_is_accepted_and_rendered(self):
        out = self.post(good(self.inc))
        self.assertTrue(out["ok"])
        self.assertIn("Proposed actions (proposals only - nothing was executed)", out["content"])
        self.assertIn("incident #1 - 1 alert(s) - 1 tool call(s) - test-model", out["content"])

    def test_evidence_for_a_call_that_was_never_made_is_rejected(self):
        d = good(self.inc)
        d["evidence"][0] = {"tool": "kube.get", "args": {"kind": "nodes"}, "finding": "all nodes are Ready"}
        self.assertIn("served no such call", self.problems(d)[0])

    def test_same_tool_different_arguments_is_not_the_same_call(self):
        d = good(self.inc)
        d["evidence"][0]["args"] = {"id": "RB-FLUX-HR-STALLED"}
        self.assertTrue(self.problems(d))

    def test_a_replayed_call_counts_as_served(self):
        f = self.tmp / "replays" / "demo" / "registry.actions"
        f.mkdir(parents=True)
        (f / f"{tools.args_hash('registry.actions', {})}.json").write_text(json.dumps({"actions": {}}))
        self.tb.call_tool("registry.actions", {}, self.inc, replay="demo")
        d = good(self.inc)
        d["evidence"].append({"tool": "registry.actions", "args": {}, "finding": "action registry consulted"})
        self.assertTrue(self.post(d)["ok"])

    def test_calls_for_another_incident_do_not_count(self):
        self.tb.clock.t += 200  # past the correlation window, so this is a different incident
        other = self.tb.ingest_zabbix(base.ev(host="hlin2", trigger="Other trigger"))["incident_id"]
        self.assertNotEqual(other, self.inc)
        d = good(other)
        with self.assertRaises(core.Rejected) as cm:
            self.tb.diagnose(other, {"diagnosis": d})
        self.assertIn("served no such call", cm.exception.detail["problems"][0])

    def test_wrong_incident_id_inside_the_document(self):
        self.assertIn("does not match", self.problems(good(999))[0])

    def test_unknown_runbook_action_and_doc_are_rejected(self):
        d = good(self.inc)
        d.update(runbook_id="RB-NOT-A-THING")
        d["proposed_actions"][0]["action_id"] = "wipe-the-fleet"
        d["known_issue_refs"] = ["docs/known-issues/does-not-exist.md"]
        text = " | ".join(self.problems(d))
        self.assertIn("runbook registry", text)
        self.assertIn("action registry", text)
        self.assertIn("does not exist in the repo", text)

    def test_secret_shaped_strings_are_rejected_anywhere(self):
        for field_edit in (
            lambda d: d.update(summary="It failed with Bearer abcdefghijklmnopqrstuvwxyz0123456789 in the header"),
            lambda d: d["evidence"][0].update(finding="key sk-ant-api03-abcdefghijklmnopqrstu was in the output"),
            lambda d: d.update(reasoning="webhook https://discord.com/api/webhooks/123456/abcDEF-ghi_JKL leaked"),
            lambda d: d["next_checks"].append("password = hunter2hunter2"),
        ):
            d = good(self.inc)
            field_edit(d)
            self.assertTrue(any("contains" in p for p in self.problems(d)), d)

    def test_a_failed_diagnosis_is_not_stored_and_a_good_one_replaces_the_previous(self):
        bad = good(self.inc)
        bad["layer"] = "cloud"
        self.problems(bad)
        self.assertIsNone(self.tb.db.execute("SELECT 1 FROM diagnoses").fetchone())
        self.post(good(self.inc))
        again = good(self.inc)
        again["confidence"] = "high"
        self.post(again)
        rows = self.tb.db.execute("SELECT diagnosis_json FROM diagnoses").fetchall()
        self.assertEqual((len(rows), json.loads(rows[0][0])["confidence"]), (1, "high"))

    def test_unknown_incident_and_bad_body(self):
        with self.assertRaises(core.Rejected) as cm:
            self.tb.diagnose(999, {"diagnosis": good(999)})
        self.assertEqual(cm.exception.status, 404)
        with self.assertRaises(core.Rejected) as cm:
            self.tb.diagnose(self.inc, ["nope"])
        self.assertEqual(cm.exception.status, 400)

    def test_rendering_stays_under_the_discord_limit_and_never_mentions_anyone(self):
        d = good(self.inc)
        d["summary"] = "word " * 119
        d["reasoning"] = "reason " * 210
        d["next_checks"] = ["check " * 30] * 5
        out = self.post(d)["content"]
        self.assertLessEqual(len(out), 1900)
        self.assertNotIn("@everyone", out)


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        c = core.Config(live=tools.LiveConfig(root=REPO))
        cls.tb = core.Toolbelt(c, normalize.load_routes(), base.KNOWN, clock=base.Clock(), audit=lambda r: None, action_ids=ACTIONS)
        cls.inc = cls.tb.ingest_zabbix(base.ev())["incident_id"]
        cls.tb.call_tool("registry.runbook", {"id": "RB-ZBX-TRIAGE"}, cls.inc)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.tb, base.TOKEN, [ipaddress.ip_network("127.0.0.0/8")]))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls._tmp.cleanup()

    def post(self, path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", json.dumps(body).encode(), method="POST")
        req.add_header("Authorization", f"Bearer {base.TOKEN}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_422_carries_the_problems_so_the_workflow_can_retry(self):
        d = good(self.inc)
        d["evidence"][0] = {"tool": "kube.get", "args": {"kind": "nodes"}, "finding": "all nodes are Ready"}
        d["known_issue_refs"] = []
        status, out = self.post(f"/diagnosis/{self.inc}", {"diagnosis": d})
        self.assertEqual(status, 422)
        self.assertIn("served no such call", out["problems"][0])

    def test_200_returns_discord_ready_content(self):
        d = good(self.inc)
        d["known_issue_refs"] = []
        status, out = self.post(f"/diagnosis/{self.inc}", {"diagnosis": d, "model": "claude-test"})
        self.assertEqual(status, 200)
        self.assertTrue(out["content"].startswith("**Likely layer: the workload**"))


if __name__ == "__main__":
    unittest.main()
