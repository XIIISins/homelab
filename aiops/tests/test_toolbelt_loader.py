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


def fake_vault(secret_value=TOKEN, login_ok=True):
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
            self._reply(200, {"data": {"data": {"value": secret_value}}})

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


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

    def test_a_restart_replaces_the_file_atomically(self):
        self.out.write_text("stale\n")
        srv, _ = fake_vault()
        try:
            self.run_loader(srv)
        finally:
            srv.shutdown()
        self.assertEqual(self.out.read_text().strip(), TOKEN)


if __name__ == "__main__":
    unittest.main()
