"""10h burst-cluster test, slice 2: the runner's pure parts (aiops/runner/k8s_burst_run.py). The live behaviour is proven by running it on a burst
cluster (docs/procedures/k8s-burst-test.md); these tests hold the safety rails and the things that must not drift from the repo."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "runner"))

import k8s_burst_plan as kb  # noqa: E402
import k8s_burst_run as kr  # noqa: E402


class FakeKube(kr.Kube):
    def __init__(self, nodes):
        self.nodes = nodes

    def json(self, args, timeout=120):
        return {"items": [{"metadata": {"name": n}} for n in self.nodes]}


class Guard(unittest.TestCase):
    def test_only_a_cluster_made_of_burst_nodes_is_accepted(self):
        self.assertEqual(kr.guard(FakeKube(["burst-1", "burst-2"])), ["burst-1", "burst-2"])
        for bad in (["gondul"], ["burst-1", "einherjar-urd"], [], ["burst-1x"], ["my-burst-1"]):
            with self.assertRaises(kr.Fail, msg=str(bad)):
                kr.guard(FakeKube(bad))


class VaultBootstrap(unittest.TestCase):
    def test_the_eso_policy_is_the_one_terraform_declares(self):
        tf = (REPO / "terraform" / "vault" / "main.tf").read_text()
        m = re.search(r'resource "vault_policy" "eso" \{.*?<<-EOT\n(.*?)\n\s*EOT', tf, flags=re.S)
        body = "\n".join(line[4:] if line.startswith("    ") else line for line in m.group(1).splitlines())
        self.assertEqual(body.strip(), kr.ESO_POLICY.strip())

    def test_the_eso_role_binds_the_same_service_account_as_terraform(self):
        tf = (REPO / "terraform" / "vault" / "main.tf").read_text()
        self.assertIn('bound_service_account_names      = ["external-secrets"]', tf)
        self.assertIn('bound_service_account_namespaces = ["external-secrets"]', tf)

    def test_random_values_have_the_shape_the_property_name_asks_for_and_are_never_equal(self):
        self.assertTrue(json.loads(kr.random_value("peppers_json")))
        self.assertIn("BEGIN PRIVATE KEY", kr.random_value("deploy_key_pem"))
        a, b = kr.random_value("password"), kr.random_value("password")
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 32)


class Seeds(unittest.TestCase):
    def test_every_vault_path_the_repo_reads_is_declared_by_terraform_so_a_typo_cannot_pass_on_burst(self):
        scanned = kb.scan(REPO)
        plan = kb.seed_plan(scanned)
        known = kr.plan_known_paths(REPO)
        self.assertGreater(len(known), 10)
        missing = kb.check_paths(plan, known)
        # Paths minted by something other than terraform/vault are listed here with where they come from; anything else is a real finding.
        self.assertEqual(missing, [], f"ExternalSecret paths with no terraform/vault declaration: {missing}")


class Verdict(unittest.TestCase):
    C = {"helmreleases": [{"name": "a/a", "ready": True, "message": ""}, {"name": "b/b", "ready": False, "message": "boom"}],
         "externalsecrets": [{"name": "n/x", "ready": True, "message": ""}], "workloads": [{"name": "n/d", "kind": "deployments", "want": 2, "ready": 1}],
         "pods_unhealthy": []}

    def test_a_not_ready_helmrelease_and_a_short_workload_fail_the_run(self):
        v = kr.verdict(self.C, set())
        self.assertFalse(v["passed"])
        self.assertEqual(len(v["problems"]), 2)

    def test_names_that_cannot_be_ready_on_burst_are_waived_but_still_listed(self):
        v = kr.verdict(self.C, {"b/b", "n/d"})
        self.assertTrue(v["passed"])
        self.assertEqual(v["waived"], ["b/b", "n/d"])

    def test_the_markdown_summary_names_what_was_skipped_and_never_contains_a_value(self):
        s = {"verdict": {"passed": True, "problems": [], "waived": []}, "commit": "a" * 40, "seconds": 90, "nodes": ["burst-1"],
             "only": ["netbox"], "render": {"skipped": {"metallb": "x"}}, "vault": {"seeded_paths": 12}, "phases": {"flux": 20.0}}
        md = kr.markdown(s)
        self.assertIn("PASSED", md)
        self.assertIn("metallb", md)
        self.assertIn("12 paths", md)
        self.assertIn("netbox", md)


if __name__ == "__main__":
    unittest.main()
