"""Phase 10h2: the Toolbelt cannot reach the internet (IPAddressDeny=any), so the PR canary-test check reads GitHub through a
loopback-only proxy that serves exactly two GET paths (aiops/toolbelt/github_read_proxy.py). These tests hold the proxy to that
promise over a real loopback socket, run the real eligibility check THROUGH it, and cover the retry of a temporary GitHub failure."""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests", "author"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import github_read_proxy as proxy  # noqa: E402
import pr_test  # noqa: E402
import test_author_dispatcher as tad  # noqa: E402
import test_change_requests as tcr  # noqa: E402
import test_pr_test as tpt  # noqa: E402
import dispatcher  # noqa: E402

BRANCH, SHA = tpt.BRANCH, tpt.SHA


class Upstream:
    """Stands in for api.github.com behind the proxy; records every path it was asked for."""

    def __init__(self, files=None, sha=SHA):
        self.paths, self.status, self.sha = [], {}, sha
        self.files = [tpt.f("ansible/roles/hardening/tasks/main.yml")] if files is None else files

    def get(self, path, timeout=8.0):
        self.paths.append(path)
        if path in self.status:
            return self.status[path]
        if "/git/ref/heads/" in path:
            return 200, json.dumps({"object": {"sha": self.sha}}).encode()
        return 200, json.dumps({"status": "ahead", "ahead_by": 1, "total_commits": 1, "files": self.files}).encode()


class Serving:
    def __enter__(self):
        self.up = Upstream()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), proxy.make_handler("XIIISins/homelab", self.up.get))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()

    def req(self, path, method="GET"):
        r = urllib.request.Request(self.base + path, method=method, data=b"{}" if method in ("POST", "PUT", "PATCH") else None)
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()


REF = f"/repos/XIIISins/homelab/git/ref/heads/{BRANCH}"
CMP = f"/repos/XIIISins/homelab/compare/main...{SHA}"


class Proxy(unittest.TestCase):
    def test_the_two_paths_are_forwarded_with_their_status_and_nothing_else_is(self):
        with Serving() as s:
            self.assertEqual(s.req(REF)[0], 200)
            self.assertEqual(s.req(CMP)[0], 200)
            before = len(s.up.paths)
            for bad in ("/repos/other/repo/git/ref/heads/agent/drift/1-x", "/repos/XIIISins/homelab/git/ref/heads/main",
                        "/repos/XIIISins/homelab/git/ref/heads/feat/x", "/repos/XIIISins/homelab/git/ref/heads/agent/docs/1-x",
                        "/repos/XIIISins/homelab/compare/main...abc", "/repos/XIIISins/homelab/compare/dev..." + SHA,
                        "/repos/XIIISins/homelab/pulls", "/repos/XIIISins/homelab/contents/CLAUDE.md", "/user", "/",
                        REF + "?x=1", REF + "/../../../user", "/repos/XIIISins/homelab/git/ref/heads/agent/drift/1-x/../../../../../etc"):
                self.assertEqual(s.req(bad)[0], 404, bad)
            self.assertEqual(len(s.up.paths), before)  # a refused path never reached GitHub

    def test_every_class_that_gets_a_pr_test_is_forwarded(self):
        # The proxy is the Toolbelt's only way to GitHub: a class missing here fails its PR test with an HTTP 404 (found live with the k8s class).
        with Serving() as s:
            for cls in ("drift", "capacity", "k8s"):
                self.assertEqual(s.req(f"/repos/XIIISins/homelab/git/ref/heads/agent/{cls}/11-label-it")[0], 200, cls)

    def test_it_is_read_only(self):
        with Serving() as s:
            for m in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                code = s.req(REF, m)[0]
                self.assertEqual(code, 405, m)
            self.assertEqual(s.up.paths, [])

    def test_githubs_status_is_relayed_so_a_rate_limit_is_visible_as_one(self):
        with Serving() as s:
            s.up.status[REF] = (403, b'{"message":"API rate limit exceeded"}')
            s.up.status[CMP] = (404, b'{"message":"Not Found"}')
            self.assertEqual(s.req(REF)[0], 403)
            self.assertEqual(s.req(CMP)[0], 404)

    def test_an_unreachable_or_oversize_upstream_is_a_502_never_a_hang_or_a_partial_answer(self):
        with Serving() as s:
            s.up.status[REF] = (0, b"")
            s.up.status[CMP] = (200, b"x" * (proxy.MAX_BYTES + 1))
            self.assertEqual(s.req(REF)[0], 502)
            self.assertEqual(s.req(CMP)[0], 502)

    def test_it_will_only_listen_on_loopback(self):
        for bad in ("0.0.0.0:8092", "10.0.11.30:8092", ":8092"):
            self.assertEqual(proxy.main(["--listen", bad]), 2, bad)


class ThroughTheProxy(unittest.TestCase):
    def test_the_real_eligibility_check_works_through_the_proxy(self):
        with Serving() as s:
            fetch = pr_test.make_fetch(s.base)
            got = pr_test.inspect_pr(fetch, BRANCH, ["drift"], ["hardening", "vlagent"])
            self.assertEqual((got["ok"], got["sha"], got["role_tag"]), (True, SHA, "hardening"))
            self.assertEqual(pr_test.check_params(fetch, {"target_host": "canary-1", "role_tag": "hardening", "pr_branch": BRANCH, "pr_sha": SHA},
                                                  tpt.GUARD), [])
            self.assertEqual(s.up.paths, [REF, CMP, REF, CMP])

    def test_make_fetch_only_translates_the_github_api_and_defaults_to_github(self):
        self.assertIs(pr_test.make_fetch(None), pr_test.gh_fetch)
        with Serving() as s:
            f = pr_test.make_fetch(s.base)
            self.assertEqual(f("http://127.0.0.1/x")[0], 0)
            self.assertEqual(f("https://evil.example/repos/XIIISins/homelab/compare/main..." + SHA)[0], 0)

    def test_a_dead_proxy_is_a_transient_failure_not_a_verdict_on_the_pr(self):
        got = pr_test.inspect_pr(pr_test.make_fetch("http://127.0.0.1:9"), BRANCH, ["drift"], ["hardening"])
        self.assertFalse(got["ok"])
        self.assertTrue(got["transient"])


class Transient(unittest.TestCase):
    def test_outages_and_rate_limits_are_transient_but_a_real_verdict_is_not(self):
        for code, want in ((0, True), (403, True), (429, True), (500, True), (502, True), (404, False), (422, False)):
            got = pr_test.inspect_pr(tpt.gh(ref_status=code), BRANCH, ["drift"], ["hardening"])
            self.assertEqual(bool(got.get("transient")), want, code)
            got = pr_test.inspect_pr(tpt.gh(cmp_status=code), BRANCH, ["drift"], ["hardening"])
            self.assertEqual(bool(got.get("transient")), want, code)
        self.assertFalse(pr_test.inspect_pr(tpt.gh([tpt.f("ansible/roles/pbs/tasks/main.yml")]), BRANCH, ["drift"], ["hardening"]).get("transient"))

    def test_a_not_tested_summary_that_may_be_retried_says_so(self):
        s = pr_test.summarize(None, "could not read the branch head from GitHub (HTTP 0)", True)
        self.assertTrue(s["retry"])
        self.assertIn("will ask again", pr_test.render(s))
        self.assertNotIn("will ask again", pr_test.render(pr_test.summarize(None, "no canary exercises the pbs role")))


class Retry(unittest.TestCase):
    def setUp(self):
        self.r = tcr.Rig()
        self.eng = self.r.tb.engine
        self.eng.cfg.semaphore = tpt.ta.FakeSemaphore(tpt.Engine().script())

    def tearDown(self):
        self.r.close()

    def retest(self, cid, token=tcr.T_AUTH):
        return self.r.call(token, "POST", f"/change-requests/{cid}/pr-test")

    def open_pr(self, fetch):
        self.eng.cfg.pr_fetch = fetch
        cr = self.r.approved(**{"class": "drift", "title": "Fix the hardening banner task"})
        self.r.call(tcr.T_AUTH, "POST", "/change-requests/claim")
        st, out = self.r.call(tcr.T_AUTH, "POST", f"/change-requests/{cr['id']}/report",
                              {"state": "pr-open", "pr_url": tcr.PR, "branch": BRANCH, "summary": "x", "tests": {}})
        self.assertEqual(st, 200, out)
        return cr["id"], out

    def test_a_temporary_github_failure_is_retried_once_it_clears_and_then_proposes_the_test(self):
        cid, out = self.open_pr(tpt.gh(ref_status=0))
        self.assertEqual((out["pr_test"]["status"], out["pr_test"]["retry"]), ("not-tested", True))
        self.assertEqual(self.retest(cid)[0], 429)                       # too soon
        self.r.clock.t += 130
        self.eng.cfg.pr_fetch = tpt.gh(ref_status=0)
        st, again = self.retest(cid)                                     # still down: stays retryable, no proposal
        self.assertEqual((st, again["pr_test"]["status"], again["pr_test"]["retry"]), (200, "not-tested", True))
        self.r.clock.t += 130
        self.eng.cfg.pr_fetch = tpt.gh()                                 # GitHub (or the proxy) is back
        st, ok = self.retest(cid)
        self.assertEqual((st, ok["pr_test"]["status"]), (200, "proposed"))
        self.assertEqual(self.retest(cid)[0], 409)                       # nothing left to retry
        self.assertEqual(self.eng.pr_test_view(BRANCH)["state"], "pending")

    def test_an_attempt_recorded_before_the_transient_flag_existed_is_still_retried(self):
        """The first live run wrote `{"ineligible": "could not read ... (HTTP 0)"}` with no flag; that PR must not be stuck."""
        cid, out = self.open_pr(tpt.gh([tpt.f("ansible/roles/pbs/tasks/main.yml")]))  # first attempt: a real verdict
        self.assertFalse(out["pr_test"]["retry"])
        self.r.tb.cr._event(cid, "pr_test", {"ineligible": "could not read the branch head from GitHub (HTTP 0)"})  # the legacy shape
        self.assertTrue(self.r.call(tcr.T_AUTH, "GET", f"/change-requests/{cid}")[1]["pr_test"]["retry"])
        self.r.clock.t += 130
        self.eng.cfg.pr_fetch = tpt.gh()
        st, ok = self.retest(cid)
        self.assertEqual((st, ok["pr_test"]["status"]), (200, "proposed"))

    def test_a_real_verdict_is_never_retried(self):
        cid, out = self.open_pr(tpt.gh([tpt.f("ansible/roles/pbs/tasks/main.yml")]))
        self.assertFalse(out["pr_test"]["retry"])
        self.r.clock.t += 400
        self.assertEqual(self.retest(cid)[0], 409)

    def test_only_the_author_role_may_ask_and_only_for_an_open_tested_pr(self):
        cid, _ = self.open_pr(tpt.gh(ref_status=0))
        self.r.clock.t += 130
        for token in (tcr.T_APPR, tcr.T_AGENT, tcr.T_TOOLS):
            self.assertEqual(self.retest(cid, token)[0], 403)
        _, pending = self.r.file(title="pending")
        self.assertEqual(self.retest(pending["id"])[0], 409)
        self.assertEqual(self.retest(9999)[0], 404)


class DispatcherRetry(tad.Rig):
    def test_the_dispatcher_asks_again_for_a_temporarily_untested_pr_but_not_more_than_every_five_minutes(self):
        calls = []
        proposed = {"status": "proposed", "proposal": 4}

        def retest(cid):  # what the Toolbelt does: the next list of open PRs carries the new status
            calls.append(cid)
            self.tb.prs[0]["pr_test"] = proposed
            return 200, {"pr_test": proposed}

        self.tb.retest = retest
        gh = self.gh
        gh.bodies = {55: "## Rollback\nrevert\n"}
        gh.get_pr_body = lambda n: gh.bodies.get(n)
        gh.set_pr_body = lambda n, b: (gh.bodies.__setitem__(n, b), 200)[1]
        self.tb.prs = [{"id": 9, "pr_url": "https://github.com/XIIISins/homelab/pull/55", "pr_test": {"status": "not-tested", "retry": True, "reason": "x"}}]
        d = self.disp()
        d.reconcile()
        d.reconcile()
        self.assertEqual(calls, [9])
        self.assertIn("waiting for the operator's approval", gh.bodies[55])  # the fresh view was written into the description
        self.tb.prs = [{"id": 10, "pr_url": "https://github.com/XIIISins/homelab/pull/56", "pr_test": {"status": "not-tested", "retry": False, "reason": "y"}},
                       {"id": 11, "pr_url": "https://github.com/XIIISins/homelab/pull/57", "pr_test": None}]
        d.reconcile()
        self.assertEqual(calls, [9])  # a verdict and an untested class are never retried


class Units(unittest.TestCase):
    def test_the_proxy_unit_is_confined_and_the_toolbelt_unit_points_at_it(self):
        unit = (REPO / "ansible/roles/aiops-toolbelt/templates/aiops-toolbelt-ghread.service.j2").read_text()
        for needle in ("IPAddressDeny={{ aiops_toolbelt_ghread_deny_ranges | join(' ') }}", "IPAddressAllow=127.0.0.0/8", "NoNewPrivileges=yes",
                       "CapabilityBoundingSet=", "ProtectSystem=strict", "--listen 127.0.0.1:", "MemoryMax="):
            self.assertIn(needle, unit)
        tb = (REPO / "ansible/roles/aiops-toolbelt/templates/aiops-toolbelt.service.j2").read_text()
        self.assertIn("--github-read-url http://127.0.0.1:{{ aiops_toolbelt_ghread_port }}", tb)
        self.assertIn("IPAddressDeny=any", tb)  # the Toolbelt itself stays sealed
        defaults = (REPO / "ansible/roles/aiops-toolbelt/defaults/main.yml").read_text()
        self.assertIn("toolbelt/github_read_proxy.py", defaults)
        for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
            self.assertIn(cidr, defaults.split("aiops_toolbelt_ghread_deny_ranges:")[1].splitlines()[0])


if __name__ == "__main__":
    unittest.main()
