"""Tests for the Phase 10c tooling. stdlib unittest (the repo has no pytest convention).

    python3 -m unittest discover -s aiops/tests -v      # from the repo root

Two halves: (1) the committed data is clean; (2) the linter actually catches
each class of mistake it claims to (a validator that never fails proves nothing).
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))

import lint  # noqa: E402
import normalize  # noqa: E402

AIOPS = lint.AIOPS
ROOT = lint.ROOT


def docs():
    return (
        copy.deepcopy(lint.load_yaml(AIOPS / "runbooks.yml")),
        copy.deepcopy(lint.load_yaml(AIOPS / "actions.yml")),
        copy.deepcopy(lint.load_yaml(AIOPS / "alert-routing.yml")),
    )


def rb_by_id(rb_doc, rid):
    return next(r for r in rb_doc["runbooks"] if r["id"] == rid)


class CommittedDataIsClean(unittest.TestCase):
    def test_lint_is_clean(self):
        self.assertEqual(lint.run(), [])

    def test_every_critical_capable_route_has_a_runbook(self):
        _, _, rt = docs()
        for r in rt["routes"]:
            if r["severity"] in ("critical", "any"):
                self.assertIsNotNone(r["runbook_id"], r["id"])

    def test_every_fixture_critical_alert_carries_runbook_id(self):
        n = 0
        for p in (AIOPS / "fixtures" / "cases").glob("*.json"):
            for a in json.loads(p.read_text())["expected"]:
                if a["severity"] == "critical":
                    n += 1
                    self.assertTrue(a["runbook_id"], p.name)
        self.assertGreaterEqual(n, 8)

    def test_only_policy_covered_t1_work_is_auto_and_no_t3_above_none(self):
        rb, reg, _ = docs()
        covered = {p["action"] for p in reg["autonomy"]["policies"].values()} | {p["action"] for p in reg["rebuild"]["policies"].values()}
        covered_rb = {p["runbook"] for p in reg["autonomy"]["policies"].values()}
        for r in rb["runbooks"]:
            if r["automatable"] == "auto":
                self.assertIn(r["id"], covered_rb, r["id"])
            if r["tier"] == "T3":
                self.assertEqual(r["automatable"], "none", r["id"])
        for n, a in reg["actions"].items():
            if a["tier"] != "T0" and a["max_autonomy"] == "auto":
                self.assertEqual(a["tier"], "T1", n)
                self.assertIn(n, covered, n)

    def test_replay_actions_share_one_tag_allowlist(self):
        _, reg, _ = docs()
        self.assertEqual(
            reg["actions"]["replay-role"]["guard"]["allowed_tags"],
            reg["actions"]["replay-role-check"]["guard"]["allowed_tags"],
        )


class NormalizerBehaviour(unittest.TestCase):
    routes = normalize.load_routes()
    R = "2026-10-01T12:40:00Z"

    def test_fingerprint_is_stable_and_field_sensitive(self):
        a = normalize.fingerprint("zabbix", "hlin", "pg-ha", "x")
        self.assertEqual(a, normalize.fingerprint("zabbix", "hlin", "pg-ha", "x"))
        self.assertNotEqual(a, normalize.fingerprint("zabbix", "eir", "pg-ha", "x"))
        self.assertRegex(a, r"^[0-9a-f]{16}$")

    def test_slug(self):
        self.assertEqual(normalize.slug("FS [/]: Space is critically low (used > 90%)"), "fs-space-is-critically-low-used-90")
        self.assertEqual(normalize.slug("!!!"), "unknown")

    def test_media_is_not_an_alert(self):
        self.assertEqual(normalize.normalize({"title": "x", "body": "y", "tag": "media"}, self.R, self.routes), [])

    def test_firing_and_resolved_share_a_fingerprint(self):
        f = json.loads((AIOPS / "fixtures/cases/zabbix-high-problem.json").read_text())["expected"][0]
        r = json.loads((AIOPS / "fixtures/cases/zabbix-high-resolved.json").read_text())["expected"][0]
        self.assertEqual(f["fingerprint"], r["fingerprint"])
        self.assertEqual((f["status"], r["status"]), ("firing", "resolved"))

    def test_s4_cert_warning_and_critical_share_a_fingerprint(self):
        c = json.loads((AIOPS / "fixtures/cases/s4-critical-bundle.json").read_text())["expected"][1]
        w = json.loads((AIOPS / "fixtures/cases/s4-warning-cert.json").read_text())["expected"][0]
        self.assertEqual(c["fingerprint"], w["fingerprint"])
        self.assertEqual((c["severity"], w["severity"]), ("critical", "alert"))

    def test_unknown_critical_still_gets_a_runbook(self):
        out = normalize.normalize({"title": "brand new thing", "body": "", "tag": "critical"}, self.R, self.routes)
        self.assertEqual(out[0]["runbook_id"], "RB-ALERT-UNCLASSIFIED")

    def test_every_source_resolves_a_critical_message(self):
        samples = {
            "zabbix": "[Zabbix] High: Whatever: it broke",
            "s4-prober": "Infra health: 1 critical finding(s)",
            "patroni": "Patroni: something else entirely",
            "semaphore": "Drift check failed: 1 host(s) failed/unreachable",
            "frigg": "Frigg: something new",
            "unknown": "no idea",
        }
        for src, title in samples.items():
            body = "- a brand new finding\n" if src == "s4-prober" else "**Host:** x\n"
            out = normalize.normalize({"title": title, "body": body, "tag": "critical"}, self.R, self.routes)
            self.assertTrue(out and all(a["runbook_id"] for a in out), src)


class LinterCatchesMistakes(unittest.TestCase):
    def test_t3_cannot_be_above_none(self):
        rb, reg, _ = docs()
        r = rb_by_id(rb, "RB-PG-HA-DEGRADED")
        r["automatable"] = "approval"
        errs = lint.check_runbooks(rb, reg, ROOT)
        self.assertTrue(any("T3 runbook must be automatable none" in e for e in errs), errs)

    def test_auto_requires_proof_and_low_tier(self):
        rb, reg, _ = docs()
        r = rb_by_id(rb, "RB-FLUX-HR-STALLED")
        r["automatable"] = "auto"
        errs = lint.check_runbooks(rb, reg, ROOT)
        self.assertTrue(any("idempotency_proof" in e for e in errs), errs)
        self.assertTrue(any("caps at approval" in e for e in errs), errs)

    def test_automatable_needs_remediation_and_none_forbids_it(self):
        rb, reg, _ = docs()
        rb_by_id(rb, "RB-FLUX-HR-STALLED")["remediation"] = []
        rb_by_id(rb, "RB-ESO-FORCE-SYNC")["remediation"] = ["flux-reconcile"]
        errs = lint.check_runbooks(rb, reg, ROOT)
        self.assertTrue(any("needs at least one remediation" in e for e in errs), errs)
        self.assertTrue(any("automatable none but remediation" in e for e in errs), errs)

    def test_remediation_tier_may_not_exceed_runbook_tier(self):
        rb, reg, _ = docs()
        reg["actions"]["flux-reconcile-reset"]["tier"] = "T2"
        errs = lint.check_runbooks(rb, reg, ROOT)
        self.assertTrue(any("exceeds the runbook tier" in e for e in errs), errs)

    def test_missing_marker_and_orphan_marker(self):
        rb, reg, _ = docs()
        rb_by_id(rb, "RB-ESO-FORCE-SYNC")["id"] = "RB-ESO-RENAMED"
        errs = lint.check_runbooks(rb, reg, ROOT)
        self.assertTrue(any("RB-ESO-RENAMED: marker must appear exactly once" in e for e in errs), errs)
        self.assertTrue(any("orphan marker RB-ESO-FORCE-SYNC" in e for e in errs), errs)

    def test_mutating_verify_command_rejected(self):
        rb, reg, _ = docs()
        rb_by_id(rb, "RB-ESO-FORCE-SYNC")["verify"] = {"command": "kubectl delete externalsecret foo -n bar"}
        rb_by_id(rb, "RB-LXC-BOOT-DRIFT")["verify"] = {"command": "ansible-playbook playbooks/site.yml"}
        errs = lint.check_runbooks(rb, reg, ROOT)
        self.assertTrue(any("looks mutating" in e for e in errs), errs)
        self.assertTrue(any("without --check" in e for e in errs), errs)

    def test_unknown_action_references(self):
        rb, reg, _ = docs()
        rb_by_id(rb, "RB-PG-HA-DEGRADED")["diagnostics"] = ["no-such-action"]
        rb_by_id(rb, "RB-VAULT-RAFT-DRAIN")["verify"] = {"action": "restart-unit"}  # exists but is not T0
        errs = lint.check_runbooks(rb, reg, ROOT)
        self.assertTrue(any("'no-such-action' is not in the registry" in e for e in errs), errs)
        self.assertTrue(any("verify.action restart-unit must be T0" in e for e in errs), errs)

    def test_action_without_verify_or_tier_fails_the_schema(self):
        _, reg, _ = docs()
        schema = lint.load_schema("actions.v1.schema.json")
        for field in ("verify", "tier"):
            broken = copy.deepcopy(reg)
            del broken["actions"]["service-status"][field]
            errs = lint.schema_errors(broken, schema)
            self.assertTrue(any(f"'{field}' is a required property" in e for e in errs), (field, errs))

    def test_mutator_cannot_be_auto_and_t3_cannot_exceed_none(self):
        _, reg, _ = docs()
        reg["actions"]["restart-unit"]["max_autonomy"] = "auto"
        reg["actions"]["service-status"]["tier"] = "T3"
        errs = lint.check_actions(reg, ROOT)
        self.assertTrue(any("mutating action may not be auto" in e for e in errs), errs)
        self.assertTrue(any("T3 action must be max_autonomy none" in e for e in errs), errs)

    def test_template_and_playbook_references_must_resolve(self):
        _, reg, _ = docs()
        reg["actions"]["service-status"]["semaphore"]["template"] = "aiops-nope"
        reg["actions"]["vault-status"]["semaphore"]["playbook"] = "ansible/playbooks/aiops-nope.yml"
        reg["actions"]["patroni-status"]["semaphore"]["playbook"] = "ansible/playbooks/aiops-vault-status.yml"
        errs = lint.check_actions(reg, ROOT)
        self.assertTrue(any("template 'aiops-nope' not defined" in e for e in errs), errs)
        self.assertTrue(any("registry says 'ansible/playbooks/aiops-vault-status.yml'" in e for e in errs), errs)
        self.assertTrue(any("aiops-nope.yml does not exist" in e for e in errs), errs)

    def test_placeholders_and_allowlist_hosts_must_resolve(self):
        _, reg, _ = docs()
        reg["actions"]["restart-unit"]["guard"]["allowed_units"]["urd"] = ["x.service"]  # a hypervisor, not T1
        reg["actions"]["replay-role"]["semaphore"]["task_fields"]["arguments"] = ["--limit", "{nope}"]
        errs = lint.check_actions(reg, ROOT)
        self.assertTrue(any("'urd' is not in host_tiers.T1" in e for e in errs), errs)
        self.assertTrue(any("{nope} is not a declared extra_var" in e for e in errs), errs)

    def test_routing_needs_catchall_and_runbooks(self):
        rb, _, rt = docs()
        rt["routes"] = [r for r in rt["routes"] if r["id"] != "zbx-catchall"]
        rt["routes"][0]["runbook_id"] = "RB-DOES-NOT-EXIST"
        errs = lint.check_routing(rt, rb)
        self.assertTrue(any("source zabbix must end with a catch-all" in e for e in errs), errs)
        self.assertTrue(any("RB-DOES-NOT-EXIST is not in runbooks.yml" in e for e in errs), errs)
        rb2, _, rt2 = docs()
        next(r for r in rt2["routes"] if r["id"] == "s4-catchall")["runbook_id"] = None
        errs = lint.check_routing(rt2, rb2)
        self.assertTrue(any("critical-capable route has no runbook_id" in e for e in errs), errs)

    def test_quoted_markers_in_prose_are_not_anchors(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "docs" / "known-issues"
            d.mkdir(parents=True)
            (d / "x.md").write_text(
                "prose `<!-- runbook: RB-QUOTED -->` and\n```\n<!-- runbook: RB-FENCED -->\n```\n<!-- runbook: RB-REAL -->\n- bullet\n"
            )
            self.assertEqual(lint.doc_markers(Path(td)), {"RB-REAL": ["docs/known-issues/x.md"]})

    def test_parse_templates(self):
        tf = lint.parse_templates((ROOT / "terraform/semaphore/templates.tf").read_text())
        self.assertEqual(tf["asgard-apply"], "ansible/playbooks/apply.yml")
        self.assertIn("aiops-flux-reconcile", tf)


if __name__ == "__main__":
    unittest.main()
