#!/usr/bin/env python3
"""Toolbelt API HTTP shell (Phase 10d2). Stdlib only; the logic lives in core.py.

  POST /ingest/zabbix            native aiops.zabbix-event/v1 -> {action, incident_id, ...}
  GET  /group/<id>               the correlated incident (alerts, state, priority, model hint)
  POST /group/<id>/state         {"state": "running|posted|resolved", "thread_id": "..."}
  POST /diagnosis/<id>           {"diagnosis": {...}, "model": "..."} -> validated + Discord-ready, or 422 + problems
  POST /tool/<name>              {"args": {...}, "incident_id": N}; header X-AIOPS-Replay: <scenario> replays
  GET  /stats                    breaker counters
  GET  /healthz                  liveness (no auth, no data)

Every route except /healthz needs `Authorization: Bearer <token>` AND a client address
inside the allow-list: two independent checks, because the token alone is one leaked
file away from being a hole. The token is read from a file (never an env var or flag, so
it is not visible in /proc or `ps`). The server speaks plain HTTP on an internal
interface; there is nothing here that can write to any system the agent diagnoses.
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402

MAX_BODY = 64 * 1024
_GROUP = re.compile(r"^/group/(\d+)$")
_GROUP_STATE = re.compile(r"^/group/(\d+)/state$")
_TOOL = re.compile(r"^/tool/([a-z]+\.[a-z_]+)$")
_DIAG = re.compile(r"^/diagnosis/(\d+)$")
_REPLAY = re.compile(r"^/replay/([a-z0-9-]+)/latest$")


def make_handler(tb: core.Toolbelt, token: str, allow: list):
    class Handler(BaseHTTPRequestHandler):
        server_version = "aiops-toolbelt"
        sys_version = ""

        def log_message(self, fmt, *args):  # noqa: D401 - audit goes through tb.audit, not stderr
            return

        def _send(self, status: int, body: dict) -> None:
            raw = json.dumps(body, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authorised(self) -> bool:
            try:
                ip = ipaddress.ip_address(self.client_address[0])
            except ValueError:
                return False
            if not any(ip in net for net in allow):
                tb.audit("denied", reason="ip", client=str(ip), path=self.path)
                return False
            got = self.headers.get("Authorization", "")
            if not got.startswith("Bearer ") or not hmac.compare_digest(got[7:].encode(), token.encode()):
                tb.audit("denied", reason="token", client=str(ip), path=self.path)
                return False
            return True

        def _body(self) -> object:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise core.Rejected(413, "body too large")
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw or b"{}")
            except json.JSONDecodeError:
                raise core.Rejected(400, "body is not JSON")

        def _route(self, method: str) -> None:
            path = self.path.split("?", 1)[0]
            if method == "GET" and path == "/healthz":
                return self._send(200, {"ok": True})
            if not self._authorised():
                return self._send(403, {"error": "forbidden"})
            try:
                if method == "POST" and path == "/ingest/zabbix":
                    return self._send(200, tb.ingest_zabbix(self._body(), self.headers.get("X-AIOPS-Replay") or None))
                if method == "GET" and (m := _GROUP.match(path)):
                    return self._send(200, tb.group(int(m.group(1))))
                if method == "POST" and (m := _GROUP_STATE.match(path)):
                    body = self._body()
                    if not isinstance(body, dict) or not isinstance(body.get("state"), str):
                        raise core.Rejected(400, "need {\"state\": ...}")
                    return self._send(200, tb.set_state(int(m.group(1)), body["state"], body.get("thread_id")))
                if method == "POST" and (m := _TOOL.match(path)):
                    body = self._body()
                    if not isinstance(body, dict):
                        raise core.Rejected(400, "body must be an object")
                    return self._send(200, tb.call_tool(m.group(1), body.get("args", {}), body.get("incident_id"),
                                                        self.headers.get("X-AIOPS-Replay")))
                if method == "POST" and (m := _DIAG.match(path)):
                    return self._send(200, tb.diagnose(int(m.group(1)), self._body()))
                if method == "GET" and (m := _REPLAY.match(path)):
                    return self._send(200, tb.replay_latest(m.group(1)))
                if method == "GET" and path == "/watchdog":
                    return self._send(200, tb.watchdog())
                if method == "GET" and path == "/stats":
                    return self._send(200, tb.stats())
            except core.Rejected as e:
                return self._send(e.status, {"error": e.message, **e.detail})
            except Exception as e:  # never leak a traceback to the caller; the audit log has it
                tb.audit("error", path=path, error=type(e).__name__)
                return self._send(500, {"error": "internal"})
            # unknown or write-shaped route: refused here, not by the caller's good behaviour
            tb.audit("denied", reason="unknown-route", method=method, path=path)
            return self._send(404, {"error": "not found"})

        def do_GET(self):  # noqa: N802
            self._route("GET")

        def do_POST(self):  # noqa: N802
            self._route("POST")

        def do_PUT(self):  # noqa: N802
            self._route("PUT")

        def do_DELETE(self):  # noqa: N802
            self._route("DELETE")

        def do_PATCH(self):  # noqa: N802
            self._route("PATCH")

    return Handler


def load_runbooks(root: Path) -> set[str]:
    import yaml

    return {r["id"] for r in yaml.safe_load((root / "aiops" / "runbooks.yml").read_text())["runbooks"]}


def load_action_ids(root: Path) -> set[str]:
    import yaml

    return set(yaml.safe_load((root / "aiops" / "actions.yml").read_text())["actions"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--listen", default="127.0.0.1:8090")
    ap.add_argument("--db", default="/var/lib/aiops-toolbelt/toolbelt.sqlite3")
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--allow", action="append", required=True, help="CIDR allowed to call (repeatable)")
    ap.add_argument("--placement-file", help="JSON {host: hypervisor-node}, refreshed from NetBox")
    ap.add_argument("--daily-run-cap", type=int, default=40)
    ap.add_argument("--replay-dir", help="aiops/replays: recorded tool responses (X-AIOPS-Replay scenarios)")
    ap.add_argument("--creds-dir", help="directory of <name>.json read-only backend credentials (root loader output)")
    ap.add_argument("--repo-dir", help="read-only clone of the homelab repo for the repo-history tools")
    args = ap.parse_args(argv)

    token = Path(args.token_file).read_text().strip()
    if len(token) < 32:
        print("token too short (need >= 32 chars)", file=sys.stderr)
        return 2
    cfg = core.Config(db_path=args.db, daily_run_cap=args.daily_run_cap)
    cfg.live = core.tools.LiveConfig(root=core.REPO, repo_dir=Path(args.repo_dir) if args.repo_dir else None,
                                     creds_dir=Path(args.creds_dir) if args.creds_dir else None)
    if args.replay_dir:
        cfg.replay_dir = Path(args.replay_dir)
    if args.placement_file:
        cfg.placement_file = Path(args.placement_file)  # hot-reloaded; a missing file at start is fine
    import normalize  # noqa: E402 (path set up by core)

    tb = core.Toolbelt(cfg, normalize.load_routes(), load_runbooks(core.REPO), action_ids=load_action_ids(core.REPO))
    host, _, port = args.listen.rpartition(":")
    srv = ThreadingHTTPServer((host, int(port)), make_handler(tb, token, [ipaddress.ip_network(a) for a in args.allow]))
    tb.audit("start", listen=args.listen, daily_run_cap=cfg.daily_run_cap)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
