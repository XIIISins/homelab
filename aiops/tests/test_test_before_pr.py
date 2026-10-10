"""Test before PR (classes with `test_before_pr`): the branch is pushed and tested first; the PR opens when the test passed or cannot run and never
when it failed. The Toolbelt half (the `testing` state, the test folded into the request's approval) and the dispatcher half (advance_testing)."""
from __future__ import annotations

import copy
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests", "author", "bot"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import actions  # noqa: E402
import dispatcher  # noqa: E402
import drafts  # noqa: E402
import scope  # noqa: E402
import test_author_dispatcher as tad  # noqa: E402
import test_change_requests as tcr  # noqa: E402

REAL = scope.load_classes((REPO / "aiops" / "author-classes.yml").read_text())
BRANCH = "agent/k8s/5-label-it"
SHA = "a" * 40


def proposal_view(state="succeeded", seconds=400):
    return {"id": 77, "action_id": "pr-burst-test", "state": state, "params": {"component": "outline", "pr_sha": SHA},
            "result": {"steps": [{"step": "test", "result": {"summary_md": "- built a cluster and applied the app", "seconds": seconds}}], "why": "an app failed"}}


class Rig(tcr.Rig):
    """The change-request rig with the burst-test side of the Engine stubbed: what it was asked, and what the test now says."""

    def __init__(self, **kw):
        super().__init__(**kw)
        e = self.tb.engine
        self.proposed, self.decided, self.view = [], [], None
        self.refuse = None

        def propose(branch, thread, cid):
            self.proposed.append((branch, cid))
            return {"proposal": 77, "component": "outline"}

        def decide(pid, decision, **kw):
            if self.refuse:
                raise actions.Refused(409, self.refuse)
            self.decided.append((pid, decision, kw))
            return {}

        e.propose_pr_burst_test, e.decide, e.pr_test_view = propose, decide, lambda branch: self.view

    def k8s(self, **kw):
        st, cr = self.file(**{"class": "k8s", "title": "Label the outline app", "body": "Add the label.", **kw})
        assert st == 200, cr
        return cr

    def to_testing(self, cr=None):
        cr = cr or self.k8s()
        self.call(tcr.T_APPR, "POST", f"/change-requests/{cr['id']}/decision", {"decision": "approve", "by": tcr.OP})
        assert self.call(tcr.T_AUTH, "POST", "/change-requests/claim", {})[0] == 200
        st, out = self.call(tcr.T_AUTH, "POST", f"/change-requests/{cr['id']}/report", {"state": "testing", "branch": BRANCH, "summary": "Added the label.",
                                                                                        "tests": {"scope rules": "pass", "_files": [{"filename": "k8s/asgard/apps/outline/deployment.yaml", "additions": 1, "deletions": 0}]}})
        assert st == 200, out
        return cr["id"]

    def get(self, cid):
        return self.call(tcr.T_APPR, "GET", f"/change-requests/{cid}")[1]


class ToolbeltSide(unittest.TestCase):
    def setUp(self):
        self.r = Rig()
        self.addCleanup(self.r.close)

    def test_the_k8s_and_rightsizing_classes_are_marked_and_untested_ones_are_not(self):
        c = REAL["classes"]
        self.assertTrue(c["k8s"]["test_before_pr"] and c["k8s"]["burst_test"])
        self.assertTrue(c["rightsizing"]["test_before_pr"] and c["rightsizing"]["burst_test"])
        for name in ("docs", "drift-note", "capacity"):
            self.assertFalse(c[name].get("test_before_pr"), name)
        for name, cls in c.items():   # a flag on a class with no test would wait for nothing
            if cls.get("test_before_pr"):
                self.assertTrue(cls.get("burst_test") or cls.get("canary_test"), name)

    def test_the_dispatcher_reports_testing_and_the_test_is_proposed_and_approved_with_the_request(self):
        cid = self.r.to_testing()
        v = self.r.get(cid)
        self.assertEqual((v["state"], v["branch"], v["test_before_pr"]), ("testing", BRANCH, True))
        self.assertEqual(self.r.proposed, [(BRANCH, cid)])
        self.assertEqual(len(self.r.decided), 1)
        pid, decision, kw = self.r.decided[0]
        self.assertEqual((pid, decision, kw["by"], kw["ref"]), (77, "approve", tcr.OP, f"change-request-{cid}"))   # the OPERATOR who approved the request
        ev = [e for e in self.r.call(tcr.T_APPR, "GET", "/change-requests/feed?after=0")[1]["events"] if e["kind"] == "pr_test"]
        self.assertTrue(ev[-1]["data"]["approved_with_request"])
        self.assertEqual(v["tests"]["scope rules"], "pass")

    def test_the_view_follows_the_test_and_the_pr_may_open_from_testing_without_a_second_proposal(self):
        cid = self.r.to_testing()
        self.r.view = proposal_view("running")
        self.assertEqual(self.r.get(cid)["pr_test"]["status"], "running")
        self.r.view = proposal_view("succeeded")
        self.assertEqual(self.r.get(cid)["pr_test"]["status"], "passed")
        st, out = self.r.call(tcr.T_AUTH, "POST", f"/change-requests/{cid}/report", {"state": "pr-open", "pr_url": tcr.PR, "branch": BRANCH})
        self.assertEqual((st, out["state"], out["pr_url"], out["summary"]), (200, "pr-open", tcr.PR, "Added the label."))   # the summary survives the detour
        self.assertEqual(len(self.r.proposed), 1)    # the PR opening is not a reason to test again

    def test_a_failed_test_ends_the_request_without_a_pr(self):
        cid = self.r.to_testing()
        st, out = self.r.call(tcr.T_AUTH, "POST", f"/change-requests/{cid}/report", {"state": "failed", "error": "the burst test failed, so no PR was opened: x"})
        self.assertEqual((st, out["state"], out["pr_url"]), (200, "failed", None))
        self.assertIsNotNone(out["finished_at"])

    def test_from_testing_only_pr_open_or_failed_are_possible_and_only_once(self):
        cid = self.r.to_testing()
        for body in ({"state": "no-change"}, {"state": "testing", "branch": BRANCH}, {"state": "pr-open", "pr_url": "https://example.com/pull/1"}):
            self.assertIn(self.r.call(tcr.T_AUTH, "POST", f"/change-requests/{cid}/report", body)[0], (400, 409), body)

    def test_an_untested_class_cannot_go_through_testing_and_a_branch_is_required(self):
        st, cr = self.r.file()    # docs
        self.r.call(tcr.T_APPR, "POST", f"/change-requests/{cr['id']}/decision", {"decision": "approve", "by": tcr.OP})
        self.r.call(tcr.T_AUTH, "POST", "/change-requests/claim", {})
        self.assertEqual(self.r.call(tcr.T_AUTH, "POST", f"/change-requests/{cr['id']}/report", {"state": "testing", "branch": BRANCH})[0], 409)
        r2 = Rig()
        self.addCleanup(r2.close)
        k8s = r2.k8s()
        r2.call(tcr.T_APPR, "POST", f"/change-requests/{k8s['id']}/decision", {"decision": "approve", "by": tcr.OP})
        r2.call(tcr.T_AUTH, "POST", "/change-requests/claim", {})
        self.assertEqual(r2.call(tcr.T_AUTH, "POST", f"/change-requests/{k8s['id']}/report", {"state": "testing"})[0], 400)

    def test_a_request_in_testing_counts_toward_the_open_pr_limit_and_can_be_cancelled(self):
        r = Rig(max_open_prs=1)
        self.addCleanup(r.close)
        cid = r.to_testing()
        r.file(title="Another change", source_ref="x2")
        second = r.call(tcr.T_APPR, "GET", "/change-requests?state=pending")[1]["change_requests"][0]
        r.call(tcr.T_APPR, "POST", f"/change-requests/{second['id']}/decision", {"decision": "approve", "by": tcr.OP})
        got = r.call(tcr.T_AUTH, "POST", "/change-requests/claim", {})[1]
        self.assertIsNone(got["change_request"])
        self.assertEqual(got["why"], "open-pr-limit")
        st, out = r.call(tcr.T_APPR, "POST", f"/change-requests/{cid}/decision", {"decision": "cancel", "by": tcr.OP})
        self.assertEqual((st, out["state"]), (200, "cancelled"))

    def test_a_request_stuck_in_testing_fails_after_the_timeout_and_keeps_its_branch(self):
        cid = self.r.to_testing()
        self.r.clock.t += 2 * 3600 + 60
        v = self.r.get(cid)
        self.assertEqual(v["state"], "failed")
        self.assertIn("test stage did not finish", v["error"])
        self.assertEqual(v["branch"], BRANCH)

    def test_a_test_that_cannot_be_approved_with_the_request_says_so_instead_of_blocking(self):
        self.r.refuse = "the kill switch is engaged: nothing may be approved or run"
        cid = self.r.to_testing()
        ev = [e for e in self.r.call(tcr.T_APPR, "GET", "/change-requests/feed?after=0")[1]["events"] if e["kind"] == "pr_test"][-1]["data"]
        self.assertIn("could not be approved", ev["ineligible"])
        self.assertEqual(self.r.get(cid)["pr_test"]["status"], "not-tested")

    def test_the_fold_can_be_switched_off(self):
        r = Rig(fold_test_approval=False)
        self.addCleanup(r.close)
        r.to_testing()
        self.assertEqual((r.proposed, r.decided), ([(BRANCH, r.tb.cr.view(1)["id"])], []))

    def test_a_test_that_cannot_even_be_proposed_is_recorded_for_the_dispatcher(self):
        self.r.tb.engine.propose_pr_burst_test = lambda *a: {"ineligible": "the PR touches 2 apps (a, b); one app per test"}
        cid = self.r.to_testing()
        self.assertEqual(self.r.decided, [])
        pt = self.r.get(cid)["pr_test"]
        self.assertEqual((pt["status"], pt["reason"]), ("not-tested", "the PR touches 2 apps (a, b); one app per test"))

    def test_the_card_tells_the_operator_that_approving_also_approves_the_test(self):
        card = drafts.card({**self.r.k8s(), "test_before_pr": True})
        self.assertIn("Tested first", dict((n, v) for n, v, _ in card["fields"]))
        self.assertIn("ONE test", dict((n, v) for n, v, _ in card["fields"])["Tested first"])
        docs = drafts.card({**self.r.file()[1], "test_before_pr": False})
        self.assertNotIn("Tested first", dict((n, v) for n, v, _ in docs["fields"]))
        self.assertEqual(drafts.buttons({"state": "testing"}), ["cancel"])
        self.assertIn("testing", drafts.ANNOUNCE)
        self.assertIn("no PR is opened if it fails", drafts.announcement({"state": "testing", "branch": BRANCH}))


# ---- the dispatcher half --------------------------------------------------------------------------------------------------------------------

class TB(tad.FakeTB):
    def __init__(self):
        super().__init__()
        self.testing_list, self.retests, self.failed = [], [], []

    def testing(self):
        return self.testing_list

    def retest(self, cid):
        self.retests.append(cid)
        return 200, {}

    def listed(self, state):
        return self.failed if state == "failed" else []


class GH(tad.FakeGH):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.existing = None

    def find_open_pr(self, head):
        return self.existing


def tested_classes():
    c = copy.deepcopy(tad.CLASSES)
    c["classes"]["docs"].update(burst_test=True, test_before_pr=True)
    return c


class DispatcherSide(tad.Rig):
    def setUp(self):
        super().setUp()
        self.cfg = dispatcher.Config(repo="XIIISins/homelab", repo_url=self.url, work=str(self.work), classes=tested_classes(), tools_url="http://x", tools_cli="/c", agent_file="/a.md")
        self.tb, self.gh = TB(), GH()

    def pushed(self):
        patch = tad.patch_of(self.url, {"docs/incidents/2026-10-04-nvme.md": "# NVMe\n\nbody\n"}, self.tmp)
        return self.disp(patch).process(dict(tad.CR))

    def cr(self, status, **pt):
        base = {"id": 7, "class": "docs", "title": "Draft the NVMe latency note", "body": "write it", "source": "operator", "source_ref": "", "decided_by": "111",
                "branch": "agent/docs/7-draft-the-nvme-latency-note", "summary": "Wrote the note.", "updated_at": time.time() - 60, "state": "testing",
                "tests": {"scope rules": "pass", "secret scan": "pass", "turns": 5, "cost_usd": 0.3,
                          "_files": [{"filename": "docs/incidents/2026-10-04-nvme.md", "additions": 3, "deletions": 0}]},
                "pr_test": {"kind": "burst", "status": status, "proposal": 77, "component": "outline", "sha": "aaaaaaaa", **pt}}
        return base

    def advance(self, cr):
        return self.disp().advance_testing(cr)

    def test_the_branch_is_pushed_and_the_toolbelt_told_but_no_pr_is_opened(self):
        out = self.pushed()
        self.assertEqual(out["state"], "testing")
        self.assertIn("refs/heads/agent/docs/7-draft-the-nvme-latency-note", self.branches())
        self.assertEqual(self.gh.created, [])
        cid, rep = self.tb.reports[-1]
        self.assertEqual((cid, rep["state"], rep["branch"]), (7, "testing", "agent/docs/7-draft-the-nvme-latency-note"))
        self.assertEqual(rep["tests"]["_files"][0]["filename"], "docs/incidents/2026-10-04-nvme.md")
        self.assertEqual(rep["tests"]["secret scan"], "pass")

    def test_a_toolbelt_that_refuses_to_start_the_test_fails_the_request_loudly(self):
        class Refusing(TB):
            def report(self, cid, **f):
                self.reports.append((cid, f))
                return (409, {}) if f.get("state") == "testing" else (200, {})
        self.tb = Refusing()
        out = self.pushed()
        self.assertEqual(out["state"], "failed")
        self.assertIn("refused to start its test", self.tb.reports[-1][1]["error"])

    def test_a_passed_test_opens_the_pr_with_the_result_already_in_the_description(self):
        self.pushed()
        self.assertEqual(self.advance(self.cr("passed", markdown="- the app became Ready", seconds=416)), "pr-open:passed")
        head, base, title, body = self.gh.created[0]
        self.assertEqual((head, base), ("agent/docs/7-draft-the-nvme-latency-note", "main"))
        self.assertIn("## Burst-cluster test\n**Passed** on a burst cluster", body)
        self.assertIn("- the app became Ready", body)
        self.assertIn("`docs/incidents/2026-10-04-nvme.md` (+3 -0)", body)
        self.assertIn("- scope rules: pass", body)
        self.assertNotIn("turns", body)
        self.assertEqual(self.gh.labels, [(200, "agent-authored")])
        rep = self.tb.reports[-1][1]
        self.assertEqual((rep["state"], rep["pr_url"]), ("pr-open", "https://github.com/XIIISins/homelab/pull/200"))

    def test_a_failed_test_opens_no_pr_and_ends_the_request_with_the_reason(self):
        self.pushed()
        self.assertEqual(self.advance(self.cr("failed", reason="an app never became Ready")), "failed")
        self.assertEqual(self.gh.created, [])
        rep = self.tb.reports[-1][1]
        self.assertEqual(rep["state"], "failed")
        self.assertIn("burst test failed, so no PR was opened: an app never became Ready", rep["error"])
        self.assertIn("refs/heads/agent/docs/7-draft-the-nvme-latency-note", self.branches())   # kept for inspection

    def test_a_running_test_waits_until_the_timeout_then_the_pr_says_it_was_still_running(self):
        self.pushed()
        d = self.disp()
        self.assertEqual(d.advance_testing(self.cr("running")), "waiting")
        self.assertEqual(d.advance_testing(self.cr("proposed")), "waiting")
        self.assertEqual(self.gh.created, [])
        late = {**self.cr("running"), "updated_at": time.time() - dispatcher.Dispatcher.TEST_WAIT - 5}
        self.assertEqual(d.advance_testing(late), "pr-open:timeout")
        self.assertIn("Running on a burst cluster", self.gh.created[0][3])

    def test_a_test_that_cannot_run_opens_the_pr_saying_not_tested_and_why(self):
        self.pushed()
        cr = self.cr("not-tested", reason="the PR touches 2 apps (a, b); one app per test")
        cr["pr_test"].pop("proposal")
        self.assertEqual(self.advance(cr), "pr-open:not-tested")
        self.assertIn("**Not tested**: the PR touches 2 apps (a, b); one app per test", self.gh.created[0][3])

    def test_not_yet_evaluated_waits_briefly_and_a_temporary_failure_is_retried_for_a_while(self):
        self.pushed()
        d = self.disp()
        fresh = {**self.cr("not-tested", reason="the test has not been evaluated yet"), "updated_at": time.time() - 30}
        self.assertEqual(d.advance_testing(fresh), "waiting")
        temp = self.cr("not-tested", reason="could not read the branch head from GitHub (HTTP 503)", retry=True)
        self.assertEqual(d.advance_testing(temp), "waiting")
        self.assertEqual(self.tb.retests, [7])
        self.assertEqual(d.advance_testing(temp), "waiting")
        self.assertEqual(self.tb.retests, [7])   # not again within five minutes
        old = {**temp, "updated_at": time.time() - dispatcher.Dispatcher.RETRY_WINDOW - 5}
        self.assertEqual(d.advance_testing(old), "pr-open:not-tested")

    def test_an_expired_or_rejected_test_opens_the_pr_with_that_said(self):
        for status, words in (("rejected", "the operator rejected the test"), ("expired", "the approval window expired")):
            self.gh.created.clear()
            self.assertEqual(self.advance(self.cr(status)), f"pr-open:{status}")
            self.assertIn(words, self.gh.created[0][3])

    def test_a_pr_that_already_exists_is_found_not_duplicated(self):
        class Twice(GH):
            def create_pr(self, *a):
                return 422, {"message": "A pull request already exists"}
        self.gh = Twice()
        self.gh.existing = {"number": 201, "html_url": "https://github.com/XIIISins/homelab/pull/201"}
        self.assertEqual(self.advance(self.cr("passed")), "pr-open:passed")
        self.assertEqual(self.tb.reports[-1][1]["pr_url"], "https://github.com/XIIISins/homelab/pull/201")
        self.gh.existing = None
        self.assertEqual(self.advance(self.cr("passed")), "pr-refused")   # nothing found and GitHub refused: try again next tick

    def test_drive_testing_survives_one_bad_request(self):
        good = self.cr("failed", reason="x")
        self.tb.testing_list = [{"id": 8, "class": "nope"}, good]
        self.disp().drive_testing()
        self.assertEqual(self.tb.reports[-1][0], 7)

    def test_a_failed_branch_is_deleted_after_three_days_and_never_a_branch_that_is_not_an_agent_branch(self):
        self.pushed()
        d = self.disp()
        old = {"id": 7, "branch": "agent/docs/7-draft-the-nvme-latency-note", "error": "the burst test failed, so no PR was opened: x", "finished_at": time.time() - 4 * 86400}
        young = {**old, "id": 8, "branch": "agent/docs/8-other", "finished_at": time.time() - 3600}
        other = {**old, "id": 9, "error": "the author session did not report in time"}
        self.tb.failed = [young, other, old]
        self.assertEqual(d.cleanup_failed_branches(), 1)
        self.assertNotIn("agent/docs/7-draft-the-nvme-latency-note", self.branches())
        self.assertFalse(d.delete_branch("main"))
        self.assertFalse(d.delete_branch("agent/../main"))
        self.assertIn("refs/heads/main", self.branches())


if __name__ == "__main__":
    unittest.main()
