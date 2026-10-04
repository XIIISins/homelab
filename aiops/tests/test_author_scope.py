"""Phase 10h2: the agent PR scope rules (aiops/author/scope.py + aiops/author-classes.yml).

The real class file is loaded, so a change that loosens the deny list or enables a class without a test fails here.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "author"))
import scope  # noqa: E402

CFG = scope.load_classes((REPO / "aiops" / "author-classes.yml").read_text())


def f(name, status="modified", add=3, dele=1, prev=None):
    d = {"filename": name, "status": status, "additions": add, "deletions": dele}
    if prev:
        d["previous_filename"] = prev
    return d


class Glob(unittest.TestCase):
    def test_star_does_not_cross_directories_and_doublestar_does(self):
        self.assertTrue(scope.matches("docs/incidents/a.md", ["docs/incidents/**"]))
        self.assertTrue(scope.matches("docs/incidents/x/a.md", ["docs/incidents/**"]))
        self.assertTrue(scope.matches("terraform/proxmox/asgard-lxcs/lxcs.tf", ["terraform/proxmox/asgard-lxcs/*.tf"]))
        self.assertFalse(scope.matches("terraform/proxmox/asgard-lxcs/sub/x.tf", ["terraform/proxmox/asgard-lxcs/*.tf"]))
        self.assertTrue(scope.matches("a/b/vault.yml", ["**/vault.yml"]))
        self.assertTrue(scope.matches("vault.yml", ["**/vault.yml"]))


class Branches(unittest.TestCase):
    def test_only_the_dispatcher_shape_is_a_valid_branch(self):
        self.assertEqual(scope.branch_class("agent/docs/12-nvme-latency"), ("docs", 12))
        for b in ("feat/x", "agent/docs/nope", "agent/docs/1-UP", "agent//1-x", "agent/docs/1-x/y"):
            self.assertIsNone(scope.branch_class(b), b)

    def test_agent_pr_detection_is_by_prefix_or_login(self):
        self.assertTrue(scope.is_agent_pr(CFG, "agent/docs/1-x", "someone"))
        self.assertFalse(scope.is_agent_pr(CFG, "feat/x", "someone"))
        cfg = dict(CFG, agent_logins=["bot"])
        self.assertTrue(scope.is_agent_pr(cfg, "feat/x", "bot"))  # a bot login must use the prefix


class Check(unittest.TestCase):
    B = "agent/docs/7-incident-draft"

    def test_a_docs_change_inside_the_class_passes(self):
        self.assertEqual(scope.check(CFG, self.B, [f("docs/incidents/2026-10-04-x.md", "added"), f("docs/known-issues/frigg-control-node.md")]), [])

    def test_a_drift_note_may_only_add_a_note_under_operations_drift(self):
        b = "agent/drift-note/9-frigg-aiops-toolbelt"
        self.assertEqual(scope.check(CFG, b, [f("docs/operations/drift/2026-10-04-frigg-aiops-toolbelt.md", "added")]), [])
        for p in ("ansible/roles/aiops-toolbelt/tasks/main.yml", "docs/incidents/x.md", "docs/operations/decisions.md", "docs/operations/drift/../x.md"):
            self.assertTrue(scope.check(CFG, b, [f(p)]), p)

    def test_every_declared_class_check_is_a_script_under_github_scripts(self):
        import re
        for name, c in CFG["classes"].items():
            for chk in c.get("checks", []):
                self.assertRegex(chk["script"], r"^\.github/scripts/[a-z0-9_-]+\.py$", name)
                self.assertTrue((REPO / chk["script"]).is_file(), chk["script"])

    def test_the_agents_own_guardrails_are_forbidden_in_every_class(self):
        for p in (".github/workflows/ci.yml", "CLAUDE.md", "aiops/actions.yml", "aiops/author-classes.yml", "aiops/author/scope.py",
                  "aiops/toolbelt/actions.py", "terraform/vault/policies.tf", "ansible/inventory/group_vars/all/vault.yml",
                  "docs/operations/decisions.md", ".claude/agents/aiops-author.md", "scripts/ssh/operator-agent"):
            bad = scope.check(CFG, self.B, [f(p)])
            self.assertTrue(any("forbidden" in b or "outside" in b for b in bad), p)
            self.assertTrue(scope.matches(p, CFG["deny"]), p)

    def test_outside_the_class_is_refused(self):
        self.assertTrue(scope.check(CFG, self.B, [f("k8s/asgard/apps/x/deploy.yaml")]))

    def test_deletes_and_renames_out_of_scope_are_refused(self):
        self.assertTrue(any("deleting" in b for b in scope.check(CFG, self.B, [f("docs/incidents/a.md", "removed")])))
        bad = scope.check(CFG, self.B, [f("docs/incidents/a.md", "renamed", prev="CLAUDE.md")])
        self.assertTrue(any("CLAUDE.md" in b for b in bad))

    def test_size_limits(self):
        many = [f(f"docs/incidents/{i}.md", "added") for i in range(13)]
        self.assertTrue(any("files changed" in b for b in scope.check(CFG, self.B, many)))
        self.assertTrue(any("changed lines" in b for b in scope.check(CFG, self.B, [f("docs/incidents/a.md", add=700)])))

    def test_disabled_and_unknown_classes_and_bad_branches(self):
        self.assertTrue(any("not enabled" in b for b in scope.check(CFG, "agent/capacity/1-x", [f("ansible/roles/pg-backup/defaults/main.yml")])))
        self.assertTrue(any("unknown class" in b for b in scope.check(CFG, "agent/bogus/1-x", [f("docs/incidents/a.md")])))
        self.assertTrue(scope.check(CFG, "agent/docs/oops", [f("docs/incidents/a.md")]))

    def test_the_change_requests_own_path_list_can_only_narrow(self):
        ok = [f("docs/incidents/a.md", "added")]
        self.assertEqual(scope.check(CFG, self.B, ok, declared_allow=["docs/incidents/**"]), [])
        self.assertTrue(scope.check(CFG, self.B, [f("docs/procedures/p.md")], declared_allow=["docs/incidents/**"]))

    def test_enabled_classes_never_allow_a_denied_path(self):
        # every allow glob of every class, probed with a representative denied file, must still be refused
        for name, cls in CFG["classes"].items():
            for denied in ("CLAUDE.md", "aiops/actions.yml", ".github/workflows/ci.yml", "ansible/inventory/group_vars/all/vault.yml"):
                cfg = dict(CFG, classes={name: dict(cls, enabled=True)})
                self.assertTrue(scope.check(cfg, f"agent/{name}/1-x", [f(denied)]), (name, denied))


class Cli(unittest.TestCase):
    def run_cli(self, branch, files, author=""):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as t:
            json.dump(files, t)
        p = subprocess.run([sys.executable, str(REPO / "aiops/author/scope.py"), "--classes", str(REPO / "aiops/author-classes.yml"),
                            "--branch", branch, "--author", author, "--files-json", t.name], capture_output=True, text=True)
        Path(t.name).unlink()
        return p.returncode, p.stdout

    def test_exit_codes(self):
        self.assertEqual(self.run_cli("feat/x", [f("CLAUDE.md")])[0], 0)  # not an agent PR
        self.assertEqual(self.run_cli("agent/docs/1-x", [f("docs/incidents/a.md", "added")])[0], 0)
        rc, out = self.run_cli("agent/docs/1-x", [f("CLAUDE.md")])
        self.assertEqual(rc, 1)
        self.assertIn("forbidden", out)


if __name__ == "__main__":
    unittest.main()
