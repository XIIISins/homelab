"""Phase 10d2: the Frigg token loader (ansible/roles/aiops-toolbelt/files/aiops-toolbelt-token-load.py).

Runs the real loader against a fake Vault on loopback and checks what lands on disk and
what is printed: the token goes to a 0400 file and nowhere else.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOADER = REPO / "ansible" / "roles" / "aiops-toolbelt" / "files" / "aiops-toolbelt-token-load.py"
spec = importlib.util.spec_from_file_location("loader", LOADER)
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)

TOKEN = "Z" * 48


def fake_vault(secret_value=TOKEN, login_ok=True, missing=()):
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            return

        def _reply(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):  # noqa: N802
            seen.append(("POST", self.path))
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n))
            if login_ok and body.get("role_id") == "rid" and body.get("secret_id") == "sid":
                return self._reply(200, {"auth": {"client_token": "vt"}})
            self._reply(400, {"errors": ["bad"]})

        def do_GET(self):  # noqa: N802
            seen.append(("GET", self.path, self.headers.get("X-Vault-Token")))
            if self.headers.get("X-Vault-Token") != "vt":
                return self._reply(403, {})
            if any(m in self.path for m in missing):
                return self._reply(404, {"errors": []})
            self._reply(200, {"data": {"data": {"value": secret_value}}})

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


class Deployment(unittest.TestCase):
    def test_every_toolbelt_module_is_shipped_to_frigg(self):
        """actions.py imports autonomy.py: a module missing from the role's file list crashes the service at start (found in 10f)."""
        import re
        text = (REPO / "ansible" / "roles" / "aiops-toolbelt" / "defaults" / "main.yml").read_text()
        shipped = set(re.findall(r"^  - (toolbelt/[a-z_]+\.py)$", text, re.M))
        present = {f"toolbelt/{p.name}" for p in (REPO / "aiops" / "toolbelt").glob("*.py")}
        self.assertEqual(present - shipped, set())


class DispatcherRestart(unittest.TestCase):
    def test_a_code_deploy_also_restarts_the_dispatcher_which_runs_the_same_files(self):
        """The dispatcher runs author/*.py and reads author-classes.yml from the Toolbelt's code tree, but only the author role's
        handler restarted it: a deploy through the Toolbelt tag left the OLD dispatcher running (found 2026-10-04: the new retry logic
        sat unused for twenty minutes)."""
        import yaml
        tasks = yaml.safe_load((REPO / "ansible/roles/aiops-toolbelt/tasks/main.yml").read_text())
        copy = next(t for t in tasks if t.get("name") == "Install the API code and data from the repo")
        # ...and the loopback GitHub read proxy runs github_read_proxy.py from the same tree (2026-10-05: a stale allowlist answered 404 for k8s).
        self.assertEqual(sorted(copy["notify"]), ["Restart aiops-author", "Restart aiops-toolbelt", "Restart aiops-toolbelt-ghread"])
        handlers = yaml.safe_load((REPO / "ansible/roles/aiops-author/handlers/main.yml").read_text())
        self.assertIn("Restart aiops-author", [h["name"] for h in handlers])
        own = yaml.safe_load((REPO / "ansible/roles/aiops-toolbelt/handlers/main.yml").read_text())
        self.assertIn("Restart aiops-toolbelt-ghread", [h["name"] for h in own])
        play = (REPO / "ansible/playbooks/asgard-control.yml").read_text()
        self.assertIn("role: aiops-author", play)  # the handler is only visible where both roles are in one play


class Loader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.approle = self.dir / "vault-approle.env"
        self.approle.write_text("# comment\nFRIGG_ROLE_ID=rid\nFRIGG_SECRET_ID=sid\n")
        self.out = self.dir / "token"

    def tearDown(self):
        self.tmp.cleanup()

    def run_loader(self, srv, **extra):
        env = {
            "VAULT_ADDR": f"http://127.0.0.1:{srv.server_address[1]}",
            "AIOPS_TOOLBELT_APPROLE_ENV": str(self.approle),
            "AIOPS_TOOLBELT_TOKEN_FILE": str(self.out),
            **extra,
        }
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            loader.main(env)
        return buf.getvalue()

    def test_token_lands_in_a_0400_file_and_is_never_printed(self):
        srv, seen = fake_vault()
        try:
            printed = self.run_loader(srv)
        finally:
            srv.shutdown()
        self.assertEqual(self.out.read_text().strip(), TOKEN)
        self.assertEqual(stat.S_IMODE(os.stat(self.out).st_mode), 0o400)
        self.assertNotIn(TOKEN, printed)
        self.assertNotIn("vt", printed.split())
        self.assertFalse((self.dir / "token.tmp").exists())
        self.assertEqual(seen[1][1], "/v1/secret/data/ansible/aiops/toolbelt-token")

    def test_a_short_token_in_vault_refuses_to_start(self):
        srv, _ = fake_vault(secret_value="short")
        try:
            with self.assertRaises(SystemExit):
                self.run_loader(srv)
        finally:
            srv.shutdown()
        self.assertFalse(self.out.exists())

    def test_a_failed_login_leaves_no_file(self):
        srv, _ = fake_vault(login_ok=False)
        try:
            with self.assertRaises(Exception):
                self.run_loader(srv)
        finally:
            srv.shutdown()
        self.assertFalse(self.out.exists())

    def test_extra_credentials_are_written_0400_and_a_missing_one_only_warns(self):
        srv, _ = fake_vault(missing=("not-minted",))
        creds = self.dir / "creds"
        try:
            printed = self.run_loader(
                srv, AIOPS_TOOLBELT_CREDS_DIR=str(creds),
                AIOPS_TOOLBELT_EXTRA_CREDS=json.dumps({"pve": "secret/data/pve", "zabbix": "secret/data/not-minted"}))
        finally:
            srv.shutdown()
        self.assertEqual(json.loads((creds / "pve.json").read_text()), {"value": TOKEN})
        self.assertEqual(stat.S_IMODE(os.stat(creds / "pve.json").st_mode), 0o400)
        self.assertFalse((creds / "zabbix.json").exists())
        self.assertIn("WARNING: credential 'zabbix' not loaded", printed)
        self.assertNotIn(TOKEN, printed)
        self.assertEqual(self.out.read_text().strip(), TOKEN)  # the API token still loaded

    def test_a_credential_that_disappears_from_vault_removes_the_stale_file(self):
        creds = self.dir / "creds"
        creds.mkdir()
        (creds / "pve.json").write_text("{}")
        srv, _ = fake_vault(missing=("pve",))
        try:
            self.run_loader(srv, AIOPS_TOOLBELT_CREDS_DIR=str(creds),
                            AIOPS_TOOLBELT_EXTRA_CREDS=json.dumps({"pve": "secret/data/pve"}))
        finally:
            srv.shutdown()
        self.assertFalse((creds / "pve.json").exists())

    def test_a_restart_replaces_the_file_atomically(self):
        self.out.write_text("stale\n")
        srv, _ = fake_vault()
        try:
            self.run_loader(srv)
        finally:
            srv.shutdown()
        self.assertEqual(self.out.read_text().strip(), TOKEN)

    def test_required_files_are_written_0400_and_never_printed(self):
        srv, seen = fake_vault()
        files = {"approver-token": {"path": "secret/data/ansible/aiops/approver-token", "field": "value", "out": str(self.dir / "approver-token"), "min_len": 32},
                 "operators": {"path": "secret/data/ansible/ratatoskr/discord-bot", "field": "value", "out": str(self.dir / "operators"), "min_len": 17}}
        try:
            printed = self.run_loader(srv, AIOPS_TOOLBELT_EXTRA_FILES=json.dumps(files))
        finally:
            srv.shutdown()
        for name in ("approver-token", "operators"):
            self.assertEqual((self.dir / name).read_text().strip(), TOKEN)
            self.assertEqual(stat.S_IMODE(os.stat(self.dir / name).st_mode), 0o400)
        self.assertNotIn(TOKEN, printed)
        self.assertIn("approver-token written to", printed)
        self.assertTrue(any(e[1] == "/v1/secret/data/ansible/ratatoskr/discord-bot" for e in seen if e[0] == "GET"))

    def test_a_missing_or_short_required_file_refuses_to_start(self):
        for kwargs in ({"missing": ("approver-token",)}, {"secret_value": "x" * 40}):
            srv, _ = fake_vault(**kwargs)
            files = {"approver-token": {"path": "secret/data/ansible/aiops/approver-token", "field": "value",
                                        "out": str(self.dir / "approver-token"), "min_len": 48 if "secret_value" in kwargs else 32}}
            try:
                with self.assertRaises(BaseException):
                    self.run_loader(srv, AIOPS_TOOLBELT_EXTRA_FILES=json.dumps(files))
            finally:
                srv.shutdown()
            self.assertFalse((self.dir / "approver-token").exists())


if __name__ == "__main__":
    unittest.main()
