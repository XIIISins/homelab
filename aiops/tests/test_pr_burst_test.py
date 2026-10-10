"""10h burst-cluster test of an agent `k8s/` PR: what the Toolbelt reads from GitHub (aiops/toolbelt/pr_test.py `inspect_k8s_pr`), the `pr-burst-test`
registry action, the engine's burst step (aiops/toolbelt/burst_exec.py, a FAKE runner here) and the PR description's `## Burst-cluster test` section.
The runner itself (aiops/runner/burst_runner.py) has its own tests."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests", "author", "n8n"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import actions  # noqa: E402
import burst_exec  # noqa: E402
import dispatcher  # noqa: E402
import pr_test  # noqa: E402
import test_actions as ta  # noqa: E402
import test_change_requests as tcr  # noqa: E402

BRANCH = "agent/k8s/21-outline-resources"
SHA = "c" * 40
OTHER = "d" * 40
GUARD = {"pr_scope": {"kind": "k8s", "classes": ["k8s"]}}
YAML_PATCH = "@@ -1 +1 @@\n-    memory: 256Mi\n+    memory: 512Mi"


def f(name, patch=YAML_PATCH, status="modified", **kw):
    return {"filename": name, "status": status, "patch": patch, "additions": 1, "deletions": 1, **kw}


def gh(files=None, sha=SHA, status="ahead", ahead_by=1, total=1, ref_status=200, cmp_status=200):
    files = [f("k8s/asgard/apps/outline/deployment.yaml")] if files is None else files

    def fetch(url):
        if "/git/ref/heads/" in url:
            return ref_status, {"object": {"sha": sha}}
        if "/compare/main..." in url:
            return cmp_status, {"status": status, "ahead_by": ahead_by, "total_commits": total, "files": files}
        return 404, {}
    return fetch


def inspect(fetch, branch=BRANCH):
    return pr_test.inspect_k8s_pr(fetch, branch, GUARD["pr_scope"]["classes"])


class FakeBurst:
    """The burst runner: records what it was asked, answers with a canned result or raises."""

    def __init__(self, passed=True, raises=None, md="**Burst-cluster test: PASSED**\nInstalled: the core plus outline."):
        self.calls, self.passed, self.raises, self.md = [], passed, raises, md

    def test(self, branch, sha, component):
        self.calls.append((branch, sha, component))
        if self.raises:
            raise self.raises
        return {"passed": self.passed, "seconds": 143, "summary_md": self.md, "commit": sha, "unexpected": "dropped"}


class Eligibility(unittest.TestCase):
    def test_one_apps_manifests_are_eligible_and_name_the_app_and_head(self):
        got = inspect(gh([f("k8s/asgard/apps/outline/deployment.yaml"), f("k8s/asgard/apps/outline/service.yaml")]))
        self.assertEqual((got["ok"], got["sha"], got["component"], got["files"]), (True, SHA, "outline", 2))

    def test_only_agent_k8s_branches(self):
        for b in ("main", "feat/x", "agent/docs/3-x", "agent/drift/3-x", "agent/k8s/x-y", "agent/k8s/3-UPPER"):
            self.assertFalse(inspect(gh(), b)["ok"], b)

    def test_nothing_outside_one_apps_yaml_is_tested(self):
        for name in ("k8s/asgard/infrastructure/traefik/helmrelease.yaml", "k8s/asgard/flux-system/apps.yaml", "k8s/asgard/apps/kustomization.yaml",
                     "k8s/asgard/apps/outline/README.md", "k8s/asgard/apps/outline/sub/dir/x.yaml/../../../../x.yaml", "ansible/roles/baseline/tasks/main.yml",
                     "k8s/asgard/apps/Outline/x.yaml", ".github/workflows/ci.yml"):
            self.assertFalse(inspect(gh([f(name)]))["ok"], name)

    def test_one_app_per_test_and_no_deletes_or_renames(self):
        two = inspect(gh([f("k8s/asgard/apps/outline/a.yaml"), f("k8s/asgard/apps/netbox/b.yaml")]))
        self.assertFalse(two["ok"])
        self.assertIn("one app per test", two["reason"])
        self.assertFalse(inspect(gh([f("k8s/asgard/apps/outline/a.yaml", status="removed")]))["ok"])
        self.assertFalse(inspect(gh([f("k8s/asgard/apps/outline/a.yaml", status="renamed", previous_filename="k8s/asgard/apps/outline/b.yaml")]))["ok"])

    def test_github_failures_and_shape_limits_fail_closed(self):
        self.assertFalse(inspect(gh(ref_status=404))["ok"])
        self.assertTrue(inspect(gh(cmp_status=500)).get("transient"))
        self.assertFalse(inspect(gh(status="identical", ahead_by=0))["ok"])
        self.assertFalse(inspect(gh(ahead_by=4, total=4))["ok"])
        self.assertFalse(inspect(gh([f(f"k8s/asgard/apps/outline/t{i}.yaml") for i in range(21)]))["ok"])
        self.assertFalse(inspect(gh([f("k8s/asgard/apps/outline/a.yaml", patch=None)]))["ok"])

    def test_host_level_constructs_block_the_automatic_test_but_removed_lines_do_not(self):
        for line in ("          hostPath:", "      hostNetwork: true", "          privileged: true", "kind: ClusterRole",
                     "kind: CustomResourceDefinition", "kind: MutatingWebhookConfiguration", "allowPrivilegeEscalation: true"):
            got = inspect(gh([f("k8s/asgard/apps/outline/a.yaml", patch="@@ -1 +1 @@\n-old\n+" + line)]))
            self.assertFalse(got["ok"], line)
        self.assertTrue(inspect(gh([f("k8s/asgard/apps/outline/a.yaml", patch="@@ -1 +1 @@\n-      hostNetwork: true\n+      hostNetwork: false")]))["ok"])


class Params(unittest.TestCase):
    P = {"pr_branch": BRANCH, "pr_sha": SHA, "component": "outline"}

    def test_matching_params_pass_a_moved_head_or_another_app_do_not(self):
        self.assertEqual(pr_test.check_k8s_params(gh(), self.P, GUARD), [])
        self.assertTrue(any("head moved" in p for p in pr_test.check_k8s_params(gh(sha=OTHER), self.P, GUARD)))
        self.assertTrue(any("not 'netbox'" in p for p in pr_test.check_k8s_params(gh(), {**self.P, "component": "netbox"}, GUARD)))


class Engine(unittest.TestCase):
    P = {"pr_branch": BRANCH, "pr_sha": SHA, "component": "outline"}

    def setUp(self):
        self._applied = None

    def tearDown(self):
        if self._applied is not None:   # the registry object is shared by every test module: put the operator gate back
            self._applied["applied"] = False

    def make(self, burst=None, applied=True, **kw):
        eng, *_ = ta.make(ta.FakeSemaphore({}), pr_fetch=kw.pop("pr_fetch", gh()), burst=burst, **kw)
        self._applied = eng.reg.get("pr-burst-test")["semaphore"]
        self._applied["applied"] = applied
        return eng

    def test_the_action_is_gated_until_the_runner_is_deployed(self):
        eng = self.make(FakeBurst(), applied=False)
        out = eng.propose_pr_burst_test(BRANCH, None, 7)
        self.assertIn("not applied yet", out["ineligible"])

    def test_the_toolbelt_proposes_for_an_eligible_pr_and_nobody_else_may(self):
        eng = self.make(FakeBurst())
        for source in ("chat", "diagnosis", "operator"):
            with self.assertRaises(actions.Refused) as cm:
                eng.propose(action_id="pr-burst-test", params=self.P, reason="please test it", source=source)
            self.assertIn("proposed by the Toolbelt itself", str(cm.exception.detail["problems"]))
        out = eng.propose_pr_burst_test(BRANCH, "chan-1", 7)
        self.assertEqual(out["component"], "outline")
        v = eng._view(out["proposal"])
        self.assertEqual((v["state"], v["action_id"], v["source"], v["params"]), ("pending", "pr-burst-test", "author", self.P))

    def test_a_pr_it_cannot_test_gets_a_reason_and_an_unconfigured_toolbelt_fails_closed(self):
        eng = self.make(FakeBurst(), pr_fetch=gh([f("k8s/asgard/infrastructure/traefik/x.yaml")]))
        self.assertIn("not an app manifest", eng.propose_pr_burst_test(BRANCH, None, 7)["ineligible"])
        eng2, *_ = ta.make(ta.FakeSemaphore({}))
        self.assertIn("not enabled", eng2.propose_pr_burst_test(BRANCH, None, 7)["ineligible"])

    def test_the_model_is_never_offered_this_action(self):
        import build_ingest
        self.assertNotIn("pr-burst-test", build_ingest.propose_tool_description())

    def test_a_passing_test_succeeds_keeps_the_summary_and_drops_every_other_runner_field(self):
        burst = FakeBurst()
        eng = self.make(burst)
        pid = eng.propose_pr_burst_test(BRANCH, None, 7)["proposal"]
        ta.approve(eng, pid)
        out = eng.execute(pid)
        self.assertEqual(out["state"], "succeeded", out)
        self.assertEqual(burst.calls, [(BRANCH, SHA, "outline")])
        step = out["result"]["steps"][0]
        self.assertEqual((step["step"], step["result"]["passed"], step["result"]["seconds"]), ("test", True, 143))
        self.assertNotIn("unexpected", step["result"])
        s = pr_test.summarize(eng.pr_test_view(BRANCH))
        self.assertEqual((s["kind"], s["status"], s["component"], s["seconds"]), ("burst", "passed", "outline", 143))
        text = pr_test.render(s)
        self.assertIn("**Passed** on a burst cluster", text)
        self.assertIn("Burst-cluster test: PASSED", text)
        self.assertIn("full run output is in the private Discord thread", text)

    def test_an_offline_gate_failure_says_so_in_the_headline(self):
        # Found live (PR 177): "Failed on a burst cluster" above a body that said no cluster was built.
        s = {"kind": "burst", "status": "failed", "component": "microbin", "sha": "d4d5611e", "seconds": 5, "proposal": 34,
             "markdown": "**Burst-cluster test: FAILED** (offline gate, no cluster was built)\n\nProblems:\n- unknown Vault path"}
        text = pr_test.render(s)
        self.assertIn("**Failed** at the offline gate, before a cluster was built", text)
        self.assertNotIn("Failed** on a burst cluster", text)
        on_cluster = dict(s, markdown="**Burst-cluster test: FAILED** (4 burst nodes)\nProblems:\n- workload 0/2")
        self.assertIn("**Failed** on a burst cluster", pr_test.render(on_cluster))

    def test_a_failing_test_ends_verify_failed_and_the_pr_says_failed(self):
        eng = self.make(FakeBurst(passed=False, md="**Burst-cluster test: FAILED**\nProblems:\n- workload: outline/outline 0/2"))
        pid = eng.propose_pr_burst_test(BRANCH, None, 7)["proposal"]
        ta.approve(eng, pid)
        self.assertEqual(eng.execute(pid)["state"], "verify_failed")
        s = pr_test.summarize(eng.pr_test_view(BRANCH))
        self.assertEqual(s["status"], "failed")
        self.assertIn("outline/outline 0/2", pr_test.render(s))

    def test_a_runner_that_refuses_or_is_down_is_a_failed_proposal_not_a_hang(self):
        for err in (burst_exec.BurstError("busy", "another test is running"), burst_exec.BurstError("runner-unreachable", "FileNotFoundError")):
            eng = self.make(FakeBurst(raises=err))
            pid = eng.propose_pr_burst_test(BRANCH, None, 7)["proposal"]
            ta.approve(eng, pid)
            out = eng.execute(pid)
            self.assertEqual(out["state"], "failed", err.code)
            self.assertIn(err.code, json.dumps(out["result"]))

    def test_no_runner_configured_fails_the_run_not_the_toolbelt(self):
        eng = self.make(None)
        pid = eng.propose_pr_burst_test(BRANCH, None, 7)["proposal"]
        ta.approve(eng, pid)
        out = eng.execute(pid)
        self.assertEqual(out["state"], "failed")
        self.assertIn("not configured", json.dumps(out["result"]))

    def test_a_head_that_moved_after_approval_is_never_sent_to_the_runner(self):
        burst = FakeBurst()
        eng = self.make(burst)
        pid = eng.propose_pr_burst_test(BRANCH, None, 7)["proposal"]
        ta.approve(eng, pid)
        eng.cfg.pr_fetch = gh(sha=OTHER)
        out = eng.execute(pid)
        self.assertEqual(out["state"], "failed")
        self.assertIn("head moved", json.dumps(out["result"]))
        self.assertEqual(burst.calls, [])

    def test_the_client_validates_what_comes_back(self):
        client = burst_exec.BurstClient(transport=lambda req, t: {"ok": True, "result": {"passed": "yes"}})
        with self.assertRaises(burst_exec.BurstError):
            client.test(BRANCH, SHA, "outline")
        client = burst_exec.BurstClient(transport=lambda req, t: {"ok": False, "error": "denied: not an agent k8s branch"})
        with self.assertRaises(burst_exec.BurstError) as cm:
            client.test(BRANCH, SHA, "outline")
        self.assertEqual(cm.exception.code, "denied")
        sent = {}
        burst_exec.BurstClient(transport=lambda req, t: sent.update(req=req) or {"ok": True, "result": {"passed": True}}).test(BRANCH, SHA, "outline")
        self.assertEqual({k: v for k, v in sent["req"].items() if k != "request_id"}, {"v": 1, "op": "test", "branch": BRANCH, "sha": SHA, "component": "outline"})


class Wiring(unittest.TestCase):
    """The change request of class `k8s`: PR opens -> test proposed -> the request's own view and the dispatcher's section follow it."""

    def setUp(self):
        self.r = tcr.Rig()
        self.eng = self.r.tb.engine
        self.eng.cfg.semaphore = ta.FakeSemaphore({})
        self.eng.cfg.burst = FakeBurst()
        self.applied = self.eng.reg.get("pr-burst-test")["semaphore"]
        self.applied["applied"] = True

    def tearDown(self):
        self.applied["applied"] = False
        self.r.close()

    def pr_open(self, fetch=None):
        """The `k8s` class tests before its PR (test_before_pr): the dispatcher reports `testing` with the pushed branch."""
        self.eng.cfg.pr_fetch = fetch or gh()
        cr = self.r.approved(**{"class": "k8s", "title": "Raise outline memory"})
        self.r.call(tcr.T_AUTH, "POST", "/change-requests/claim")
        st, out = self.r.call(tcr.T_AUTH, "POST", f"/change-requests/{cr['id']}/report",
                              {"state": "testing", "branch": BRANCH, "summary": "x", "tests": {}})
        self.assertEqual(st, 200, out)
        return cr["id"], self.r.call(tcr.T_AUTH, "GET", f"/change-requests/{cr['id']}")[1]

    def settle(self, cid, want="passed"):
        """The test is approved with the request and runs on its own thread: wait for its status."""
        import time
        for _ in range(100):
            got = self.r.call(tcr.T_AUTH, "GET", f"/change-requests/{cid}")[1]
            if got["pr_test"]["status"] == want:
                return got
            time.sleep(0.05)
        self.fail(f"the test never reached {want}: {got['pr_test']}")

    def test_the_k8s_class_is_enabled_narrowly_and_burst_tested_not_canary_tested(self):
        c = tcr.CLASSES["classes"]["k8s"]
        self.assertTrue(c["enabled"] and c["burst_test"] and not c.get("canary_test"))
        self.assertTrue(all(p.startswith("k8s/asgard/apps/") for p in c["allow"]), c["allow"])

    def test_reporting_a_pushed_k8s_branch_proposes_the_burst_test_and_the_requests_approval_covers_it(self):
        cid, out = self.pr_open()
        self.assertEqual((out["state"], out["pr_test"]["kind"], out["pr_test"]["component"]), ("testing", "burst", "outline"))
        view = self.eng.pr_test_view(BRANCH)
        self.assertEqual((view["action_id"], view["decided_by"]), ("pr-burst-test", tcr.OP))   # approved as the operator who approved the request: no second card to press
        ev = [e for e in self.r.call(tcr.T_APPR, "GET", "/change-requests/feed?after=0")[1]["events"] if e["kind"] == "pr_test"][-1]["data"]
        self.assertTrue(ev["approved_with_request"])
        self.settle(cid)

    def test_an_untestable_k8s_pr_records_why_under_the_burst_heading(self):
        cid, out = self.pr_open(fetch=gh([f("k8s/asgard/apps/outline/a.yaml", patch="@@ -1 +1 @@\n+      hostNetwork: true")]))
        self.assertEqual((out["pr_test"]["kind"], out["pr_test"]["status"]), ("burst", "not-tested"))   # the dispatcher opens the PR saying so
        self.assertIn("hostNetwork", out["pr_test"]["reason"])

    def test_the_view_follows_the_proposal_to_passed_and_the_pr_section_carries_the_summary(self):
        cid, out = self.pr_open()
        got = self.settle(cid)
        self.assertEqual((got["pr_test"]["status"], got["pr_test"]["kind"]), ("passed", "burst"))
        block = dispatcher.canary_block(got["pr_test"])
        self.assertIn("## Burst-cluster test", block)
        self.assertIn("Installed: the core plus outline.", block)
        self.assertNotIn("## Canary test", block)

    def test_the_dispatchers_section_heading_follows_the_class(self):
        self.assertIn("## Canary test", dispatcher.canary_block(None))
        self.assertIn("## Burst-cluster test", dispatcher.canary_block({"kind": "burst", "status": "not-tested", "reason": "not evaluated yet"}))


if __name__ == "__main__":
    unittest.main()
