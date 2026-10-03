"""Phase 10g slice C: the canary kill/destroy helper's argument validation, the rebuild playbooks' safety text, the
Semaphore template names against the registry, and the canary Zabbix template's guest-unreachable trigger.

Nothing here runs ansible, SSH or Terraform: `scripts/canary/fault` is exercised through its FAULT_VALIDATE_ONLY test
hook, which exits right after argument validation.
"""
from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
FAULT = REPO / "scripts" / "canary" / "fault"
PLAYBOOKS = REPO / "ansible" / "playbooks"


def fault(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, FAULT_VALIDATE_ONLY="1")
    return subprocess.run(["bash", str(FAULT), *args], capture_output=True, text=True, env=env, timeout=20, check=False)


class FaultKillDestroy(unittest.TestCase):
    def test_script_is_valid_bash(self):
        r = subprocess.run(["bash", "-n", str(FAULT)], capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_kill_accepts_each_canary_and_maps_the_vmid(self):
        for n, vmid in ((1, 1190), (2, 1191), (3, 1192)):
            r = fault("kill", f"canary-{n}")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(f"VMID {vmid}", r.stdout)

    def test_destroy_needs_the_explicit_flag(self):
        self.assertEqual(fault("destroy", "canary-2").returncode, 2)
        r = fault("destroy", "canary-2", "yes")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--yes-destroy", r.stderr)
        ok = fault("destroy", "canary-2", "--yes-destroy")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("VMID 1191", ok.stdout)

    def test_everything_that_is_not_a_canary_name_is_refused(self):
        bad = ["canary-4", "canary-0", "canary-", "canary-12", "canary-*", "canary-1,canary-2", "saga", "pbs", "1101", "urd",
               "all", "*", "", "canary-1;ls", "CANARY-1", " canary-1", "canary-1 "]
        for verb in ("kill",):
            for name in bad:
                r = fault(verb, name)
                self.assertNotEqual(r.returncode, 0, f"{verb} {name!r} must be refused")
        for name in bad:
            r = fault("destroy", name, "--yes-destroy")
            self.assertNotEqual(r.returncode, 0, f"destroy {name!r} must be refused")

    def test_arity_is_strict(self):
        self.assertNotEqual(fault("kill").returncode, 0)
        self.assertNotEqual(fault("kill", "canary-1", "extra").returncode, 0)
        self.assertNotEqual(fault("destroy", "canary-1", "--yes-destroy", "extra").returncode, 0)
        self.assertNotEqual(fault("wipe", "canary-1").returncode, 0)

    def test_existing_verbs_still_validate(self):
        self.assertEqual(fault("stop", "canary-1", "vlagent.service").returncode, 0)
        self.assertEqual(fault("flag", "canary-3", "high").returncode, 0)
        self.assertNotEqual(fault("stop", "canary-1", "sshd.service").returncode, 0)
        self.assertNotEqual(fault("flag", "canary-1", "medium").returncode, 0)

    def test_pct_only_runs_behind_the_hostname_guard_on_urd(self):
        text = FAULT.read_text()
        self.assertIn("pct config $vmid | grep -qx 'hostname: $host'", text)
        for line in text.splitlines():
            if re.match(r"\s+(kill|destroy)\)\s+pve ", line):
                self.assertIn('$guard;', line)
                self.assertIn("pve ", line)


class RebuildPlaybooks(unittest.TestCase):
    registry = yaml.safe_load((REPO / "aiops" / "actions.yml").read_text())

    def test_every_new_playbook_exists_and_is_a_valid_play_list(self):
        for name in ("aiops-rebuild-converge", "aiops-rebuild-verify", "aiops-start-guest"):
            doc = yaml.safe_load((PLAYBOOKS / f"{name}.yml").read_text())
            self.assertIsInstance(doc, list, name)

    def test_registry_actions_point_at_them_and_the_template_names_are_in_terraform(self):
        tf = (REPO / "terraform" / "semaphore" / "templates.tf").read_text()
        for action, playbook in (("start-guest", "aiops-start-guest"), ("rebuild-guest", "aiops-rebuild-converge"),
                                 ("rebuild-verify", "aiops-rebuild-verify")):
            sem = self.registry["actions"][action]["semaphore"]
            self.assertEqual(sem["template"], playbook)
            self.assertEqual(sem["playbook"], f"ansible/playbooks/{playbook}.yml")
            self.assertIn(f'name           = "{playbook}"', tf)
            self.assertIn(f'playbook       = "ansible/playbooks/{playbook}.yml"', tf)

    def test_guards_pin_name_vmid_node_and_the_deny_list(self):
        for name in ("aiops-rebuild-converge", "aiops-rebuild-verify", "aiops-start-guest"):
            text = (PLAYBOOKS / f"{name}.yml").read_text()
            self.assertIn("^canary-[123]$", text, name)
            self.assertIn("1189 + (target[-1] | int)", text, name)
            self.assertIn("_deny.names", text, name)
            self.assertIn("_deny.vmids", text, name)
            self.assertIn("rebuild.classes.canary.hosts", text, name)
            self.assertIn("AIOPS_RESULT", text, name)

    def test_converge_needs_a_single_host_limit_and_refuses_check_mode(self):
        text = (PLAYBOOKS / "aiops-rebuild-converge.yml").read_text()
        self.assertIn("ansible_limit == target", text)
        self.assertIn("not ansible_check_mode", text)

    def test_converge_runs_day1_as_root_only_when_root_answers(self):
        text = (PLAYBOOKS / "aiops-rebuild-converge.yml").read_text()
        self.assertIn("hosts: aiops_day1_true", text)
        self.assertIn("ansible_user: root", text)
        self.assertIn("import_playbook: asgard-canary.yml", text)
        self.assertLess(text.index("hosts: aiops_day1_true"), text.index("import_playbook: asgard-canary.yml"))

    def test_start_guest_only_starts(self):
        text = (PLAYBOOKS / "aiops-start-guest.yml").read_text()
        self.assertNotRegex(text, r"pct (destroy|create|stop|restore|set|migrate)")
        self.assertIn("pct start", text)

    def test_verify_reports_each_class_post_condition(self):
        text = (PLAYBOOKS / "aiops-rebuild-verify.yml").read_text()
        for field in ("ssh_as_ansible", "vlagent_active", "zabbix_agent2_active", "zabbix_group_canary",
                      "canary_template_linked", "alert_cleared"):
            self.assertIn(field, text)
        need = {"vlagent-active", "zabbix-agent2-active", "zabbix-group-canary", "canary-template-linked", "alert-cleared", "ssh-as-ansible"}
        self.assertTrue(need <= set(self.registry["rebuild"]["classes"]["canary"]["post_conditions"]))


class CanaryTemplate(unittest.TestCase):
    doc = yaml.safe_load((PLAYBOOKS / "files" / "zabbix" / "canary-smoke-test.yml").read_text())
    tpl = doc["zabbix_export"]["templates"][0]

    def test_guest_unreachable_trigger_is_high_and_uses_a_server_side_icmp_item(self):
        items = {i["key"]: i for i in self.tpl["items"]}
        self.assertIn("icmpping[,3]", items)
        self.assertEqual(items["icmpping[,3]"]["type"], "SIMPLE")
        trig = items["icmpping[,3]"]["triggers"][0]
        self.assertEqual(trig["priority"], "HIGH")
        self.assertTrue(trig["name"].startswith("Canary smoke: guest unreachable ("))
        self.assertIn("icmpping[,3]", trig["expression"])

    def test_uuids_are_unique_v4_and_item_keys_unique(self):
        uuids = [self.tpl["uuid"]]
        for it in self.tpl["items"]:
            uuids.append(it["uuid"])
            uuids += [t["uuid"] for t in it.get("triggers", [])]
        self.assertEqual(len(uuids), len(set(uuids)))
        for u in uuids:
            self.assertRegex(u, r"^[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$")
        keys = [i["key"] for i in self.tpl["items"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_trigger_routes_to_guest_dead_before_the_generic_host_unavailable_route(self):
        routes = yaml.safe_load((REPO / "aiops" / "alert-routing.yml").read_text())["routes"]
        ids = [r["id"] for r in routes if r["source"] == "zabbix"]
        self.assertLess(ids.index("zbx-canary-guest-down"), ids.index("zbx-host-unavailable"))
        name = "Canary smoke: guest unreachable (ICMP ping loss)"
        first = next(r for r in routes if r["source"] == "zabbix" and re.search(r["match"], name, re.I))
        self.assertEqual(first["runbook_id"], "RB-GUEST-DEAD")
        # the agent-heartbeat trigger must still go to the unit runbook, not to the guest runbook
        hb = "Canary smoke: agent service down (zabbix-agent2)"
        self.assertEqual(next(r for r in routes if r["source"] == "zabbix" and re.search(r["match"], hb, re.I))["runbook_id"], "RB-UNIT-STOPPED-T1")


if __name__ == "__main__":
    unittest.main()
