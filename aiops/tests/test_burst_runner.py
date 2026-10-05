"""10h burst runner (aiops/runner/burst_runner.py): what it accepts, where the code it runs comes from, what it passes to the harness, how it reads the
result, and the unix-socket round trip with the Toolbelt's client. Git and the harness are faked with small scripts in a temp checkout."""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("runner", "toolbelt"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import burst_exec  # noqa: E402
import burst_runner as br  # noqa: E402

BRANCH = "agent/k8s/21-outline-resources"
SHA = "c" * 40
OTHER = "d" * 40


def line(**kw):
    return json.dumps({"v": 1, "request_id": "r1", "op": "test", "branch": BRANCH, "sha": SHA, "component": "outline", **kw}).encode()


class Rig(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.checkout, self.state = self.root / "checkout", self.root / "var"
        (self.checkout / "scripts" / "burst").mkdir(parents=True)
        (self.state / "state").mkdir(parents=True)
        self.log = io.StringIO()
        self.r = br.BurstRunner(self.checkout, self.state, "https://example.invalid/x.git", timeout=30, out=self.log)
        # no network, no git: the checkout is "clean" and the branch is wherever the test says
        self.head = SHA
        self.r.sync_checkout = lambda: "m" * 40
        self.r._branch_head = lambda branch: self.head

    def script(self, name, body):
        p = self.checkout / "scripts" / "burst" / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return p

    def harness(self, rc=0, summary=None, offline=None, args_file=None):
        sdir = self.state / "state" / "k8s-test" / SHA
        body = ""
        if args_file:
            body += f'echo "$@" > {args_file}\n'
        if summary is not None:
            body += f"mkdir -p {sdir}\ncat > {sdir}/summary.json <<'EOF'\n{json.dumps(summary['json'])}\nEOF\ncat > {sdir}/summary.md <<'EOF'\n{summary['md']}\nEOF\n"
        if offline is not None:
            body += f"mkdir -p {sdir}\ncat > {sdir}/offline.json <<'EOF'\n{json.dumps(offline)}\nEOF\n"
        self.script("k8s-pr-test", body + f"exit {rc}\n")
        self.down = self.root / "down-ran"
        self.script("burst-down", f"touch {self.down}\n")

    def ask(self, **kw):
        return self.r.handle_line(line(**kw))


class Validation(Rig):
    def test_only_an_agent_k8s_branch_a_full_sha_and_a_plain_app_name(self):
        for bad, code in (({"branch": "main"}, "denied"), ({"branch": "agent/docs/3-x"}, "denied"), ({"branch": "agent/k8s/3-x; id"}, "denied"),
                          ({"sha": "abc"}, "bad-request"), ({"sha": "G" * 40}, "bad-request"), ({"component": "../x"}, "bad-request"),
                          ({"component": "A b"}, "bad-request"), ({"component": ""}, "bad-request")):
            got = self.ask(**bad)
            self.assertFalse(got["ok"], bad)
            self.assertTrue(got["error"].startswith(code), (bad, got["error"]))

    def test_unknown_fields_ops_and_versions_are_refused(self):
        self.assertIn("unexpected field", self.ask(extra="x")["error"])
        self.assertFalse(self.r.handle_line(json.dumps({"v": 1, "op": "shell", "cmd": "id"}).encode())["ok"])
        self.assertFalse(self.r.handle_line(json.dumps({"v": 2, "op": "status"}).encode())["ok"])
        self.assertFalse(self.r.handle_line(b"not json")["ok"])

    def test_a_branch_whose_head_moved_is_never_tested(self):
        self.harness(rc=0)
        self.head = OTHER
        got = self.ask()
        self.assertIn("sha-moved", got["error"])
        self.assertFalse(self.down.exists())     # nothing was started, so nothing to tear down

    def test_one_test_at_a_time(self):
        self.r._lock.acquire()
        self.assertIn("busy", self.ask()["error"])
        self.r._lock.release()
        self.assertTrue(self.r.handle_line(b'{"v":1,"op":"status"}')["ok"])


class Running(Rig):
    def test_a_passing_run_returns_the_summary_and_always_tears_down(self):
        self.harness(rc=0, summary={"json": {"verdict": {"passed": True}}, "md": "**Burst-cluster test: PASSED**\nInstalled: core."})
        got = self.ask()
        self.assertTrue(got["ok"], got)
        res = got["result"]
        self.assertEqual((res["passed"], res["commit"]), (True, SHA))
        self.assertIn("PASSED", res["summary_md"])
        self.assertTrue(self.down.exists())
        self.assertIn("test-done", self.log.getvalue())

    def test_a_failing_cluster_test_is_a_normal_answer_with_passed_false(self):
        self.harness(rc=1, summary={"json": {"verdict": {"passed": False}}, "md": "**Burst-cluster test: FAILED**\n- workload: outline/outline 0/2"})
        res = self.ask()["result"]
        self.assertFalse(res["passed"])
        self.assertIn("outline/outline 0/2", res["summary_md"])

    def test_a_green_summary_with_a_nonzero_exit_is_not_a_pass(self):
        self.harness(rc=3, summary={"json": {"verdict": {"passed": True}}, "md": "x"})
        self.assertFalse(self.ask()["result"]["passed"])

    def test_an_offline_gate_failure_becomes_a_readable_failure(self):
        self.harness(rc=1, offline={"verdict": {"passed": False, "problems": ["ExternalSecrets read Vault paths that no Terraform module declares: k8s/outline/oidcc"]}})
        res = self.ask()["result"]
        self.assertFalse(res["passed"])
        self.assertIn("offline gate, no cluster was built", res["summary_md"])
        self.assertIn("k8s/outline/oidcc", res["summary_md"])

    def test_a_harness_that_leaves_no_summary_fails_closed(self):
        self.harness(rc=1)
        res = self.ask()["result"]
        self.assertFalse(res["passed"])
        self.assertIn("exited 1 without a summary", res["summary_md"])

    def test_the_harness_gets_the_commit_and_app_only_and_runs_from_the_runners_checkout(self):
        args = self.root / "args"
        self.harness(rc=0, summary={"json": {"verdict": {"passed": True}}, "md": "ok"}, args_file=args)
        self.ask()
        self.assertEqual(args.read_text().split(), ["--ref", SHA, "--only", "outline", "--ttl", "2", "--timeout", "1500"])

    def test_a_run_past_the_time_limit_is_stopped_and_torn_down(self):
        self.script("k8s-pr-test", "sleep 60\n")
        self.script("burst-down", f"touch {self.root / 'down-ran'}\n")
        self.r.timeout = 1
        res = self.ask()["result"]
        self.assertFalse(res["passed"])
        self.assertIn("time limit", res["summary_md"])
        self.assertTrue((self.root / "down-ran").exists())

    def test_only_credential_shaped_variables_reach_the_harness(self):
        os.environ["SOME_OTHER_SECRET"] = "x"
        os.environ["DIGITALOCEAN_TOKEN"] = "t"
        try:
            env = self.r._env()
        finally:
            del os.environ["SOME_OTHER_SECRET"]
            del os.environ["DIGITALOCEAN_TOKEN"]
        self.assertNotIn("SOME_OTHER_SECRET", env)
        self.assertEqual(env["DIGITALOCEAN_TOKEN"], "t")
        self.assertEqual(env["PATH"], "/usr/local/bin:/usr/bin:/bin")
        self.assertTrue(env["HOME"].startswith(str(self.state)))


class Socket(Rig):
    """The real unix socket, the Toolbelt's own client, and the peer-uid check."""

    def serve(self, uids):
        path = str(self.root / "s.sock")
        srv = br.RunnerServer(path, self.r, uids, group=None)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return path

    def test_an_allowed_peer_gets_an_answer_through_the_toolbelts_client(self):
        self.harness(rc=0, summary={"json": {"verdict": {"passed": True}}, "md": "**Burst-cluster test: PASSED**"})
        client = burst_exec.BurstClient(self.serve({os.getuid()}), test_timeout=30)
        res = burst_exec.clean_result(client.test(BRANCH, SHA, "outline"))
        self.assertTrue(res["passed"])
        self.assertEqual(client.status()["busy"], False)

    def test_a_peer_that_is_not_on_the_allow_list_is_refused_before_anything_is_read(self):
        client = burst_exec.BurstClient(self.serve({os.getuid() + 12345}), test_timeout=10)
        with self.assertRaises(burst_exec.BurstError) as cm:
            client.test(BRANCH, SHA, "outline")
        self.assertEqual(cm.exception.code, "denied")


if __name__ == "__main__":
    unittest.main()
