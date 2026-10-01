"""Phase 10b1: canaries are non-prod and must never alert at high severity.

Covers (1) the hermod_summary callback caps, (2) the normalizer cap + label,
(3) the playbook wiring that keeps canaries out of the prod reconcile while
leaving them reachable for replay-role, and (4) the Zabbix webhook cap.
The callback test stubs `ansible` so it runs in the aiops CI job (PyYAML +
jsonschema only).
"""
from __future__ import annotations

import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AIOPS = REPO / "aiops"
PLAYBOOKS = REPO / "ansible" / "playbooks"
sys.path.insert(0, str(AIOPS / "tools"))

import normalize  # noqa: E402


def load_callback():
    try:
        import ansible.plugins.callback  # noqa: F401
    except ImportError:  # CI aiops job has no ansible: stub the one base class
        mod = types.ModuleType("ansible.plugins.callback")
        mod.CallbackBase = type("CallbackBase", (), {"__init__": lambda self: setattr(self, "_display", None)})
        for name in ("ansible", "ansible.plugins"):
            sys.modules.setdefault(name, types.ModuleType(name))
        sys.modules["ansible.plugins.callback"] = mod
    spec = importlib.util.spec_from_file_location("hermod_summary_under_test", REPO / "ansible/callback_plugins/hermod_summary.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.CallbackModule


class _Playbook:
    def __init__(self, name):
        self._file_name = f"/x/playbooks/{name}"


def run(cls, wrapper, totals):
    cb = cls()
    cb.posted = True  # never POST from a test (atexit hook is a no-op)
    cb.v2_playbook_on_start(_Playbook(wrapper))
    for host, (failed, unreachable, changed) in totals.items():
        cb.totals[host] = {"ok": 5, "changed": changed, "failed": failed, "unreachable": unreachable}
    return cb._build_payload()


class CallbackCaps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cb = load_callback()

    def test_prod_apply_failure_is_still_critical(self):
        p = run(self.cb, "apply.yml", {"mimir": (1, 0, 0)})
        self.assertEqual(p["tag"], "critical")
        self.assertTrue(p["title"].startswith("Apply failed"))

    def test_nonprod_apply_failure_is_capped_at_info(self):
        for totals in ({"canary-1": (1, 0, 0)}, {"canary-1": (0, 1, 0)}):
            p = run(self.cb, "nonprod-apply.yml", totals)
            self.assertEqual(p["tag"], "info")
            self.assertTrue(p["title"].startswith("[non-prod] Apply failed"))

    def test_nonprod_apply_success_is_silent(self):
        self.assertIsNone(run(self.cb, "nonprod-apply.yml", {"canary-1": (0, 0, 3)}))

    def test_nonprod_drift_failure_is_info_and_changes_only_is_silent(self):
        p = run(self.cb, "nonprod-drift-check.yml", {"canary-1": (0, 1, 0)})
        self.assertEqual(p["tag"], "info")
        self.assertTrue(p["title"].startswith("[non-prod] Drift check failed"))
        self.assertIsNone(run(self.cb, "nonprod-drift-check.yml", {"canary-1": (0, 0, 4)}))

    def test_prod_drift_changes_still_alert(self):
        self.assertEqual(run(self.cb, "drift-check.yml", {"mimir": (0, 0, 2)})["tag"], "alert")

    def test_no_nonprod_mode_ever_emits_above_info(self):
        for wrapper in ("nonprod-apply.yml", "nonprod-drift-check.yml"):
            for totals in ({"c": (1, 0, 0)}, {"c": (0, 1, 1)}, {"c": (0, 0, 1)}, {"c": (0, 0, 0)}):
                p = run(self.cb, wrapper, totals)
                self.assertTrue(p is None or p["tag"] == "info", (wrapper, totals))


class NormalizerCap(unittest.TestCase):
    R = "2026-10-01T12:40:00Z"

    @classmethod
    def setUpClass(cls):
        cls.routes = normalize.load_routes()

    def test_nonprod_apply_has_its_own_fingerprint_and_label(self):
        wire = {"title": "[non-prod] Apply failed: 1 host(s) failed/unreachable", "body": "", "tag": "info"}
        prod = {"title": "Apply failed: 1 host(s) failed/unreachable", "body": "", "tag": "critical"}
        n = normalize.normalize(wire, self.R, self.routes)[0]
        p = normalize.normalize(prod, self.R, self.routes)[0]
        self.assertEqual((n["severity"], n["host"], n["labels"]["aiops_canary"]), ("info", "nonprod", "true"))
        self.assertEqual(n["check"], "apply-failed")  # a real route, not the catch-all
        self.assertNotEqual(n["fingerprint"], p["fingerprint"])
        self.assertEqual(p["severity"], "critical")
        self.assertNotIn("aiops_canary", p["labels"])

    def test_canary_host_is_capped_even_if_tagged_critical_or_alert(self):
        body = "**Host:** canary-2\n**Severity:** Disaster\n"
        for tag in ("critical", "alert"):
            out = normalize.normalize({"title": "[Zabbix] Disaster: x: down", "body": body, "tag": tag}, self.R, self.routes)[0]
            self.assertEqual(out["severity"], "info", tag)
        self.assertEqual(out["labels"]["aiops_canary"], "true")

    def test_info_tag_is_a_valid_severity_and_not_an_alert_drop(self):
        out = normalize.normalize({"title": "brand new thing", "body": "", "tag": "info"}, self.R, self.routes)
        self.assertEqual(out[0]["severity"], "info")
        self.assertEqual(normalize.normalize({"title": "x", "body": "", "tag": "media"}, self.R, self.routes), [])

    def test_non_canary_critical_is_untouched(self):
        body = "**Host:** canary-lookalike.example\n**Severity:** High\n"
        out = normalize.normalize({"title": "[Zabbix] High: x: down", "body": body, "tag": "critical"}, self.R, self.routes)[0]
        self.assertEqual(out["severity"], "critical")


class HermodInfoTag(unittest.TestCase):
    def test_info_tag_is_wired_end_to_end(self):
        role = REPO / "ansible/roles/hermod-api"
        d = (role / "defaults/main.yml").read_text(encoding="utf-8")
        t = (role / "templates/apprise.yml.j2").read_text(encoding="utf-8")
        self.assertIn('info:     "ansible/hermod/discord/info"', d)
        self.assertIn("hermod_api_discord_urls.info", t)
        self.assertIn("- tag: info", t)
        self.assertNotIn("@everyone", t)  # the info block carries no mention


class Wiring(unittest.TestCase):
    def read(self, name):
        return (PLAYBOOKS / name).read_text(encoding="utf-8")

    def test_canaries_are_not_in_the_prod_reconcile(self):
        site = self.read("site.yml")
        self.assertNotRegex(site, r"(?m)^- import_playbook: (asgard-canary|site-nonprod)")
        self.assertIn("import_playbook: asgard-canary.yml", self.read("site-nonprod.yml"))

    def test_nonprod_wrappers_import_nonprod_not_prod(self):
        for w in ("nonprod-apply.yml", "nonprod-drift-check.yml"):
            text = self.read(w)
            self.assertIn("import_playbook: site-nonprod.yml", text)
            self.assertNotRegex(text, r"import_playbook: site\.yml")

    def test_replay_wrappers_reach_canaries_via_site_nonprod(self):
        for w in ("aiops-replay-role.yml", "aiops-replay-role-check.yml"):
            text = self.read(w)
            self.assertIn("import_playbook: aiops-replay-guard.yml", text)
            self.assertIn("import_playbook: site.yml", text)
            self.assertIn("import_playbook: site-nonprod.yml", text)
            # the guard must run before either converge import
            self.assertLess(text.index("aiops-replay-guard.yml"), text.index("import_playbook: site.yml"))
            self.assertLess(text.index("import_playbook: site.yml"), text.index("import_playbook: site-nonprod.yml"))

    def test_fleet_agent_sweeps_exclude_canary(self):
        for f in ("vlagent.yml", "zabbix-agent.yml"):
            self.assertRegex(self.read(f), r"hosts: all:[^\n]*:!canary")

    def test_canary_t1_entries_are_exact_names(self):
        import yaml

        reg = yaml.safe_load((AIOPS / "actions.yml").read_text(encoding="utf-8"))
        names = [h for h in reg["host_tiers"]["T1"] if h.startswith("canary")]
        self.assertEqual(names, ["canary-1", "canary-2", "canary-3"])
        for h in reg["actions"]["restart-unit"]["guard"]["allowed_units"]:
            if h.startswith("canary"):
                self.assertIn(h, names)

    def test_nonprod_templates_exist_and_use_the_wrappers(self):
        tf = (REPO / "terraform/semaphore/templates.tf").read_text(encoding="utf-8")
        self.assertIn("ansible/playbooks/nonprod-drift-check.yml", tf)
        self.assertIn("ansible/playbooks/nonprod-apply.yml", tf)
        m = re.search(r'resource "semaphoreui_project_template" "asgard_nonprod_drift_check".*?\n}\n', tf, re.S)
        self.assertIn('arguments                   = ["--check", "--diff"]', m.group(0))


class ZabbixWebhookCap(unittest.TestCase):
    def test_webhook_caps_canary_hosts_at_info(self):
        js = (REPO / "ansible/roles/zabbix-server/templates/hermod-webhook.js").read_text(encoding="utf-8")
        self.assertIn("/^canary-[0-9]+$/.test(params.host_name", js)
        self.assertIn("tag = 'info'", js)
        # same pattern as the normalizer
        self.assertEqual(normalize._NONPROD_HOST.pattern, "^canary-[0-9]+$")


if __name__ == "__main__":
    unittest.main()
