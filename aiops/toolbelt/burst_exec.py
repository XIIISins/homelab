"""10h burst-cluster test of a `k8s/` PR: the Toolbelt's side (design: docs/plans/active/10h-k8s-burst-test.md, procedure: docs/procedures/k8s-burst-test.md).

The Toolbelt never builds a cluster and holds no cloud credential. It proposes `pr-burst-test` for an agent PR it has itself inspected on GitHub,
the operator approves the card, and `execute` asks the BURST RUNNER (aiops/runner/burst_runner.py, a separate root-loaded service on Frigg, same
shape as the rebuild runner) to run the test. The runner answers with `passed` and a public-safe markdown summary (names, counts and booleans, never a
value); that summary is what the PR description carries.

Protocol (v1), one JSON line each way over a unix socket:
  {"v":1,"request_id":"<uuid>","op":"test","branch":"agent/(k8s|rightsizing)/<n>-<slug>","sha":"<40 hex>","component":"<app>"}
    -> {"v":1,"request_id":...,"ok":true,"result":{"passed":bool,"seconds":int,"summary_md":"...","commit":"<sha>"}}
       or {"ok":false,"error":"<code>: <text>"}   codes: bad-request, denied, busy, checkout-failed, sha-moved, runner-failed
  {"v":1,"op":"status"} -> {"ok":true,"busy":bool,"last":{...}}
"""
from __future__ import annotations

import json
import re
import socket
import uuid
from typing import Callable

PROTOCOL_V = 1
DEFAULT_SOCKET = "/run/aiops-burst/runner.sock"
BRANCH = re.compile(r"^agent/(?:k8s|rightsizing)/[0-9]+-[a-z0-9][a-z0-9-]*\Z")   # \Z, not $: `$` also matches before a trailing newline
SHA = re.compile(r"^[0-9a-f]{40}$")
COMPONENT = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_SUMMARY = 2800   # characters of markdown that may enter a PR description


class BurstError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


def unix_transport(path: str = DEFAULT_SOCKET) -> Callable[[dict, float], dict]:
    def call(req: dict, timeout: float) -> dict:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        buf = b""
        try:
            s.connect(path)
            s.sendall((json.dumps(req, separators=(",", ":"), sort_keys=True) + "\n").encode())
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 1_000_000:
                    raise BurstError("bad-response", "the response is too large")
        except OSError as e:
            raise BurstError("runner-unreachable", type(e).__name__)
        finally:
            s.close()
        try:
            return json.loads(buf.split(b"\n", 1)[0])
        except ValueError:
            raise BurstError("bad-response", "not JSON")

    return call


class BurstClient:
    def __init__(self, socket_path: str = DEFAULT_SOCKET, transport: Callable[[dict, float], dict] | None = None, test_timeout: float = 2700.0):
        self.transport = transport or unix_transport(socket_path)
        self.test_timeout = test_timeout

    def test(self, branch: str, sha: str, component: str) -> dict:
        """Run the burst test. Blocks for the whole test (a few minutes to ~40). Raises BurstError on a refusal or an unreachable runner."""
        req = {"v": PROTOCOL_V, "op": "test", "request_id": str(uuid.uuid4()), "branch": branch, "sha": sha, "component": component}
        resp = self.transport(req, self.test_timeout)
        if not isinstance(resp, dict) or not isinstance(resp.get("ok"), bool):
            raise BurstError("bad-response", "no ok field")
        if not resp["ok"]:
            code, _, text = str(resp.get("error") or "runner-refused").partition(":")
            raise BurstError(code.strip()[:40], text.strip()[:200])
        res = resp.get("result")
        if not isinstance(res, dict) or not isinstance(res.get("passed"), bool):
            raise BurstError("bad-response", "no result")
        return res

    def status(self) -> dict:
        return self.transport({"v": PROTOCOL_V, "op": "status"}, 10.0)


def clean_result(res: dict) -> dict:
    """What is stored and shown: the verdict, how long it took and the summary, bounded. No other field of the runner's answer is kept."""
    md = str(res.get("summary_md") or "")[:MAX_SUMMARY]
    return {"passed": bool(res["passed"]), "ok": bool(res["passed"]), "seconds": int(res.get("seconds") or 0), "summary_md": md,
            "commit": str(res.get("commit") or "")[:40]}
