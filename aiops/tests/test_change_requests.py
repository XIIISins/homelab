"""Phase 10h2: change requests in the Toolbelt (aiops/toolbelt/change_requests.py) over a real loopback socket.

Four roles, one rig: agent (n8n) may only file, approver (bot) decides, author (dispatcher) claims and reports,
author-tools (the drafting session) reaches /tool/* and nothing else.
"""
from __future__ import annotations

import ipaddress
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests", "author"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import actions  # noqa: E402
import change_requests  # noqa: E402
import core  # noqa: E402
import normalize  # noqa: E402
import scope  # noqa: E402
import server  # noqa: E402
from test_actions import FakeSemaphore  # noqa: E402

ROUTES = normalize.load_routes()
KNOWN = {r["id"] for r in yaml.safe_load((REPO / "aiops" / "runbooks.yml").read_text())["runbooks"]}
REGISTRY = actions.Registry.from_file(REPO / "aiops" / "actions.yml")
CLASSES = scope.load_classes((REPO / "aiops" / "author-classes.yml").read_text())
T_AGENT, T_APPR, T_AUTH, T_TOOLS = "a" * 40, "p" * 40, "u" * 40, "t" * 40
OP = "111111111111111111"
LOOP = [ipaddress.ip_network("127.0.0.0/8")]
PR = "https://github.com/XIIISins/homelab/pull/200"


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class Rig:
    def __init__(self, **crcfg):
        self.clock, self.audit = Clock(), []
        c = core.Config(live=core.tools.LiveConfig(root=REPO), replay_dir=REPO / "aiops" / "replays")
        c.actions = actions.ActionConfig(operators=frozenset({OP}), semaphore=FakeSemaphore({}), poll_seconds=0, sleep=lambda s: None)
        c.change_requests = change_requests.CRConfig(classes=CLASSES, **crcfg)
        self.tb = core.Toolbelt(c, ROUTES, KNOWN, clock=self.clock, audit=self.audit.append, registry=REGISTRY)
        h = server.make_handler(self.tb, T_AGENT, LOOP, T_APPR, LOOP, T_AUTH, LOOP, T_TOOLS, LOOP)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tb.db.close()

    def call(self, token, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def file(self, token=T_APPR, **kw):
        body = {"source": "operator", "class": "docs", "title": "Draft the NVMe latency note", "body": "Write it from the incident.",
                "by": OP, "source_ref": kw.pop("source_ref", "")}
        body.update(kw)
        return self.call(token, "POST", "/change-requests", body)

    def approved(self, **kw):
        st, cr = self.file(**kw)
        assert st == 200, cr
        st, cr = self.call(T_APPR, "POST", f"/change-requests/{cr['id']}/decision", {"decision": "approve", "by": OP})
        assert st == 200, cr
        return cr


class Roles(unittest.TestCase):
    def setUp(self):
        self.r = Rig()

    def tearDown(self):
        self.r.close()

    def test_the_agent_can_file_but_never_decide_claim_or_report(self):
        st, cr = self.r.file(token=T_AGENT, source="chat")
        self.assertEqual((st, cr["state"], cr["created_by"]), (200, "pending", "n8n"))
        for method, path, body in (("POST", f"/change-requests/{cr['id']}/decision", {"decision": "approve", "by": OP}),
                                   ("POST", "/change-requests/claim", {}), ("POST", f"/change-requests/{cr['id']}/report", {"state": "failed"}),
                                   ("GET", "/change-requests", None), ("GET", "/change-requests/feed", None)):
            self.assertEqual(self.r.call(T_AGENT, method, path, body)[0], 403, path)
        self.assertEqual(self.r.file(token=T_AGENT, source="operator")[0], 403)  # cannot pose as the operator

    def test_the_author_can_claim_and_report_but_not_file_or_decide(self):
        st, cr = self.r.file()
        for method, path, body in (("POST", "/change-requests", {"class": "docs"}), ("POST", f"/change-requests/{cr['id']}/decision", {"decision": "approve", "by": OP}),
                                   ("GET", "/change-requests/feed", None), ("GET", "/proposals", None), ("GET", "/flags", None)):
            self.assertEqual(self.r.call(T_AUTH, method, path, body)[0], 403, path)
        # it may list requests (to reconcile open PRs) and read one, nothing more
        self.assertEqual(self.r.call(T_AUTH, "GET", "/change-requests?state=pr-open")[0], 200)
        self.assertEqual(self.r.call(T_AUTH, "GET", f"/change-requests/{cr['id']}")[0], 200)

    def test_the_session_token_reaches_tools_only(self):
        for method, path in (("POST", "/change-requests/claim"), ("GET", "/change-requests/1"), ("GET", "/status"), ("POST", "/proposals")):
            self.assertEqual(self.r.call(T_TOOLS, method, path, {})[0], 403, path)
        self.assertNotEqual(self.r.call(T_TOOLS, "POST", "/tool/repo.history", {"args": {}})[0], 403)

    def test_only_an_operator_can_decide(self):
        _, cr = self.r.file()
        st, out = self.r.call(T_APPR, "POST", f"/change-requests/{cr['id']}/decision", {"decision": "approve", "by": "999"})
        self.assertGreaterEqual(st, 400)
        self.assertEqual(self.r.call(T_APPR, "GET", f"/change-requests/{cr['id']}")[1]["state"], "pending")


class Validation(unittest.TestCase):
    def setUp(self):
        self.r = Rig()

    def tearDown(self):
        self.r.close()

    def test_class_and_paths(self):
        self.assertEqual(self.r.file(**{"class": "bogus"})[0], 400)
        self.assertEqual(self.r.file(**{"class": "capacity"})[0], 409)       # known but disabled
        self.assertEqual(self.r.file(allowed_paths=["CLAUDE.md"])[0], 400)   # denied
        self.assertEqual(self.r.file(allowed_paths=["k8s/**"])[0], 400)      # outside the class
        st, cr = self.r.file(allowed_paths=["docs/incidents/**"])            # a narrowing is fine
        self.assertEqual((st, cr["allowed_paths"]), (200, ["docs/incidents/**"]))

    def test_secret_shaped_text_is_redacted_and_sizes_are_bounded(self):
        fake = "to" + "ken=" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"  # built at runtime: the repo's secret scan blocks fixtures
        st, cr = self.r.file(body="the log said " + fake)
        self.assertEqual(st, 200)
        self.assertNotIn("A1b2C3d4", cr["body"])
        self.assertEqual(self.r.file(title="x" * 200)[0], 400)
        self.assertEqual(self.r.file(body="y" * 5000)[0], 400)

    def test_one_active_request_per_finding(self):
        self.assertEqual(self.r.file(source_ref="forecast:nvme-urd")[0], 200)
        self.assertEqual(self.r.file(source_ref="forecast:nvme-urd")[0], 409)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.r = Rig()

    def tearDown(self):
        self.r.close()

    def test_happy_path_to_a_merged_pr(self):
        cr = self.r.approved()
        st, c = self.r.call(T_AUTH, "POST", "/change-requests/claim")
        self.assertEqual((st, c["change_request"]["id"], c["change_request"]["state"]), (200, cr["id"], "running"))
        st, out = self.r.call(T_AUTH, "POST", f"/change-requests/{cr['id']}/report",
                              {"state": "pr-open", "pr_url": PR, "branch": "agent/docs/1-x", "summary": "wrote it", "tests": {"repo-ci": "pass"}})
        self.assertEqual((st, out["state"], out["tests"]), (200, "pr-open", {"repo-ci": "pass"}))
        st, out = self.r.call(T_AUTH, "POST", f"/change-requests/{cr['id']}/report", {"state": "merged"})
        self.assertEqual((st, out["state"]), (200, "merged"))
        kinds = [e["kind"] for e in self.r.call(T_APPR, "GET", "/change-requests/feed")[1]["events"]]
        self.assertEqual(kinds, ["created", "approved", "claimed", "reported", "merged"])

    def test_a_report_needs_this_repos_pr_and_a_running_request(self):
        cr = self.r.approved()
        self.r.call(T_AUTH, "POST", "/change-requests/claim")
        for url in ("https://github.com/evil/repo/pull/1", "http://github.com/XIIISins/homelab/pull/1", "https://example.com"):
            self.assertEqual(self.r.call(T_AUTH, "POST", f"/change-requests/{cr['id']}/report", {"state": "pr-open", "pr_url": url})[0], 400, url)
        self.assertEqual(self.r.call(T_AUTH, "POST", f"/change-requests/{cr['id']}/report", {"state": "merged"})[0], 409)  # not pr-open yet
        self.r.call(T_APPR, "POST", f"/change-requests/{cr['id']}/decision", {"decision": "cancel", "by": OP})
        self.assertEqual(self.r.call(T_AUTH, "POST", f"/change-requests/{cr['id']}/report", {"state": "pr-open", "pr_url": PR})[0], 409)

    def test_reject_and_cancel(self):
        _, a = self.r.file()
        self.assertEqual(self.r.call(T_APPR, "POST", f"/change-requests/{a['id']}/decision", {"decision": "reject", "by": OP})[1]["state"], "rejected")
        self.assertEqual(self.r.call(T_APPR, "POST", f"/change-requests/{a['id']}/decision", {"decision": "approve", "by": OP})[0], 409)
        self.assertEqual(self.r.call(T_AUTH, "POST", "/change-requests/claim")[1]["why"], "none-approved")

    def test_expiry(self):
        _, p = self.r.file()
        a = self.r.approved(title="another")
        self.r.clock.t += 24 * 3600 + 5
        self.assertEqual(self.r.call(T_APPR, "GET", f"/change-requests/{p['id']}")[1]["state"], "expired")
        self.r.clock.t += 6 * 3600
        self.assertEqual(self.r.call(T_APPR, "GET", f"/change-requests/{a['id']}")[1]["state"], "expired")

    def test_a_dead_claim_fails_and_frees_the_slot(self):
        cr = self.r.approved()
        self.r.call(T_AUTH, "POST", "/change-requests/claim")
        self.r.clock.t += 41 * 60
        got = self.r.call(T_APPR, "GET", f"/change-requests/{cr['id']}")[1]
        self.assertEqual((got["state"], bool(got["error"])), ("failed", True))


class Caps(unittest.TestCase):
    def test_kill_switch_and_maintenance_stop_claims_and_approvals(self):
        r = Rig()
        try:
            cr = r.approved()
            r.tb.engine.set_flag("maintenance", True, by=OP)
            self.assertEqual(r.call(T_AUTH, "POST", "/change-requests/claim")[1]["why"], "maintenance")
            r.tb.engine.set_flag("maintenance", False, by=OP)
            r.tb.engine.set_flag("kill_switch", True, by=OP)
            self.assertEqual(r.call(T_AUTH, "POST", "/change-requests/claim")[1]["why"], "kill-switch")
            _, other = r.file(title="second")
            self.assertEqual(r.call(T_APPR, "POST", f"/change-requests/{other['id']}/decision", {"decision": "approve", "by": OP})[0], 409)
            r.tb.engine.set_flag("kill_switch", False, by=OP)
            self.assertEqual(r.call(T_AUTH, "POST", "/change-requests/claim")[1]["change_request"]["id"], cr["id"])
        finally:
            r.close()

    def test_concurrency_open_prs_and_the_daily_budget(self):
        r = Rig(max_running=2, max_open_prs=1, max_started_per_day=3)
        try:
            for i in range(4):
                r.approved(title=f"t{i}")
            c1 = r.call(T_AUTH, "POST", "/change-requests/claim")[1]["change_request"]
            c2 = r.call(T_AUTH, "POST", "/change-requests/claim")[1]["change_request"]
            self.assertEqual(r.call(T_AUTH, "POST", "/change-requests/claim")[1]["why"], "max-running")
            r.call(T_AUTH, "POST", f"/change-requests/{c1['id']}/report", {"state": "pr-open", "pr_url": PR})
            self.assertEqual(r.call(T_AUTH, "POST", "/change-requests/claim")[1]["why"], "open-pr-limit")
            r.call(T_AUTH, "POST", f"/change-requests/{c1['id']}/report", {"state": "merged"})
            r.call(T_AUTH, "POST", f"/change-requests/{c2['id']}/report", {"state": "no-change"})
            self.assertIsNotNone(r.call(T_AUTH, "POST", "/change-requests/claim")[1]["change_request"])  # the 3rd start of the day
            self.assertEqual(r.call(T_AUTH, "POST", "/change-requests/claim")[1]["why"], "daily-budget")
        finally:
            r.close()

    def test_pending_cap(self):
        r = Rig(max_pending=2)
        try:
            self.assertEqual([r.file(title=f"t{i}")[0] for i in range(3)], [200, 200, 429])
        finally:
            r.close()


if __name__ == "__main__":
    unittest.main()
