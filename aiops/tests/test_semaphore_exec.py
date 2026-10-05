"""Phase 10e1: the executor's Semaphore client (request shapes) and the reach proof of its identity (mint_semaphore_exec.py)."""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import actions  # noqa: E402
import mint_semaphore_exec as mint  # noqa: E402

TOKEN = "exec-token"


class FakeSemaphore(BaseHTTPRequestHandler):
    log: list = []
    polls = 0

    def log_message(self, *a):
        return

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        FakeSemaphore.log.append((method, self.path, body, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self._send(401, {})
        p = self.path
        if method == "GET" and p == "/projects":
            return self._send(200, [{"id": 7, "name": "aiops"}])
        if method == "GET" and p == "/project/7/templates":
            return self._send(200, [{"id": 11, "name": "aiops-service-status"}, {"id": 12, "name": "aiops-restart-unit"}])
        if method == "POST" and p == "/project/7/tasks":
            return self._send(201, {"id": 99})
        if method == "GET" and p == "/project/7/tasks/99":
            FakeSemaphore.polls += 1
            return self._send(200, {"status": "running" if FakeSemaphore.polls < 2 else "success"})
        if method == "GET" and p == "/project/7/tasks/99/output":
            return self._send(200, [{"output": "\x1b[0;32mok: [canary-1]\x1b[0m"}, {"output": '"msg": "AIOPS_RESULT {\\"ok\\": true}"'}])
        return self._send(404, {})

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")


class SemaphoreAPITests(unittest.TestCase):
    def setUp(self):
        FakeSemaphore.log, FakeSemaphore.polls = [], 0
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeSemaphore)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.api = actions.SemaphoreAPI(f"http://127.0.0.1:{self.srv.server_address[1]}", TOKEN)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_project_is_found_by_name_and_templates_by_name(self):
        self.assertEqual(self.api.template_id("aiops-restart-unit"), 12)
        self.assertEqual(self.api.project, 7)
        with self.assertRaises(actions.Refused) as cm:
            self.api.template_id("asgard-apply")
        self.assertEqual(cm.exception.status, 404)

    def test_start_sends_the_environment_as_a_json_string_and_task_fields(self):
        tid = self.api.start(12, {"target_host": "canary-1", "unit": "vlagent.service"}, {"limit": "canary-1", "arguments": ["--tags", "vlagent"]})
        self.assertEqual(tid, 99)
        method, path, body, auth = next(e for e in FakeSemaphore.log if e[0] == "POST")
        self.assertEqual((path, auth), ("/project/7/tasks", "Bearer " + TOKEN))
        self.assertEqual(body["template_id"], 12)
        self.assertEqual(json.loads(body["environment"]), {"target_host": "canary-1", "unit": "vlagent.service"})  # a JSON STRING, per Semaphore
        self.assertEqual((body["limit"], json.loads(body["arguments"])), ("canary-1", ["--tags", "vlagent"]))
        self.assertFalse(body["dry_run"])

    def test_no_limit_or_arguments_unless_the_registry_sets_them(self):
        self.api.start(11, {"unit": "x.service"}, {})
        body = next(e[2] for e in FakeSemaphore.log if e[0] == "POST")
        self.assertNotIn("limit", body)
        self.assertNotIn("arguments", body)

    def test_status_and_output_are_stripped_of_ansi(self):
        self.assertEqual(self.api.status(99), "running")
        self.assertEqual(self.api.status(99), "success")
        out = self.api.output(99)
        self.assertEqual(out[0], "ok: [canary-1]")
        self.assertIn("AIOPS_RESULT", out[1])
        parsed = actions.parse_output(out, "canary-1")
        self.assertTrue(parsed["ok"])

    def test_a_refused_token_is_a_clean_refusal_not_a_traceback(self):
        bad = actions.SemaphoreAPI(self.api.base, "wrong")
        with self.assertRaises(actions.Refused) as cm:
            bad.template_id("aiops-restart-unit")
        self.assertEqual(cm.exception.status, 502)
        dead = actions.SemaphoreAPI("http://127.0.0.1:1", TOKEN)
        dead.retry_delay = 0
        calls = []
        real = actions.urllib.request.urlopen
        actions.urllib.request.urlopen = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            with self.assertRaises(actions.Refused) as cm:
                dead.status(1)
            self.assertEqual(len(calls), 3)  # a GET is retried; a POST is not
            calls.clear()
            with self.assertRaises(actions.Refused):
                dead._call("POST", "/x", {})
            self.assertEqual(len(calls), 1)
        finally:
            actions.urllib.request.urlopen = real
        self.assertIn("unreachable", cm.exception.message)


class ProofTests(unittest.TestCase):
    """prove() decides PASS/FAIL from what the executor's token is told; a leak must be caught."""

    TPLS = [{"id": 11, "name": "aiops-service-status"}, {"id": 12, "name": "aiops-restart-unit"}]

    def caller(self, leak=None):
        def call(method, path, body):
            if (method, path) == ("GET", "/projects"):
                return 200, [{"id": 7, "name": "aiops"}] if leak != "sees-all" else [{"id": 7, "name": "aiops"}, {"id": 1, "name": "asgard"}]
            if (method, path) == ("GET", "/project/7/templates"):
                return 200, self.TPLS
            if (method, path) == ("GET", "/project/7/tasks/last"):
                return 200, []
            if leak and leak in path:
                return 200, {}                     # the leak: this should have been refused
            if method == "POST" and path == "/users":
                return 403, {}
            return 403, {}
        return call

    def test_a_correctly_scoped_token_passes_every_row(self):
        rows = mint.prove(self.caller(), 7, [1], 42, ["aiops-restart-unit", "aiops-service-status"])
        self.assertTrue(all(ok for _, ok, _ in rows), [r for r in rows if not r[1]])
        labels = " ".join(r[0] for r in rows)
        for needle in ("edits a template", "creates a template", "creates a user", "raises its own role", "runs a task in project 1", "reads project 1 templates"):
            self.assertIn(needle, labels)

    def test_reading_another_project_is_caught(self):
        rows = mint.prove(self.caller(leak="/project/1/templates"), 7, [1], 42, ["aiops-service-status"])
        bad = [r[0] for r in rows if not r[1]]
        self.assertEqual(bad, ["reads project 1 templates (must be refused)"])

    def test_running_a_task_elsewhere_is_caught(self):
        rows = mint.prove(self.caller(leak="/project/1/tasks"), 7, [1], 42, ["aiops-service-status"])
        self.assertEqual([r[0] for r in rows if not r[1]], ["runs a task in project 1 (must be refused)"])

    def test_seeing_more_than_one_project_is_caught(self):
        rows = mint.prove(self.caller(leak="sees-all"), 7, [1], 42, ["aiops-service-status"])
        self.assertTrue(any(not ok and "exactly one project" in label for label, ok, _ in rows))

    def test_a_missing_registry_template_is_caught(self):
        rows = mint.prove(self.caller(), 7, [], 42, ["aiops-service-status", "aiops-flux-reconcile"])
        self.assertTrue(any(not ok and "all registry templates present" in label for label, ok, _ in rows))

    def test_the_registry_templates_the_proof_expects_are_the_applied_ones(self):
        names = mint.registry_templates()
        self.assertIn("aiops-restart-unit", names)
        for new in ("aiops-start-guest", "aiops-rebuild-converge", "aiops-rebuild-verify"):  # canary rebuild stage (2026-10-03)
            self.assertIn(new, names)
        self.assertIn("aiops-canary-fault", names)  # the soak injector's template (applied 2026-10-05)
        self.assertEqual(len(names), 11)


if __name__ == "__main__":
    unittest.main()
