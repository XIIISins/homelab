#!/usr/bin/env python3
"""The burst runner (Phase 10h): the only place a burst cluster is built unattended, to test an agent-authored `k8s/` PR.

Design: docs/plans/active/10h-k8s-burst-test.md. Procedure: docs/procedures/k8s-burst-test.md. Tests: aiops/tests/test_burst_runner.py.
Client: aiops/toolbelt/burst_exec.py (the Toolbelt's `pr-burst-test` step). Same shape as the rebuild runner and it reuses that module's socket server.

A tiny server on a unix socket. One JSON line in, one line out, and it never receives a command: only a branch, a commit and an app name (`test`).

Protocol (v1)
  {"v":1,"request_id":"<uuid>","op":"test","branch":"agent/k8s/<n>-<slug>","sha":"<40 hex>","component":"<app>"}
    -> {"v":1,"request_id":...,"ok":true,"result":{"passed":bool,"seconds":int,"summary_md":"...","commit":"<sha>"}}
       or {"ok":false,"error":"<code>: <text>"}   codes: bad-request, denied, busy, checkout-failed, sha-moved, runner-failed
  {"v":1,"op":"status"} -> {"ok":true,"busy":bool,"last":{...}}

Defence in depth, each layer independent of the Toolbelt that calls us:
  1. the socket is group-readable only by `aiops-burst-clients` and every connection's uid is checked (SO_PEERCRED) against an allow-list;
  2. the branch must match agent/k8s/<n>-<slug>, the commit 40 hex and the app a plain name: nothing else reaches a subprocess;
  3. THE CODE THAT RUNS IS NEVER THE PR'S: the harness (scripts/burst/k8s-pr-test, aiops/runner/k8s_burst_*.py) comes from this runner's own clean
     checkout of origin/main, reset on every request. The PR's commit is only ever the TREE UNDER TEST: it is rendered into YAML and applied to a
     throwaway cluster that has no route to prod;
  4. the PR commit is fetched by branch and must equal the approved sha (`sha-moved` otherwise);
  5. one test at a time (`busy`), a hard timeout, and burst-down always runs afterwards (the Frigg TTL reaper is the last backstop);
  6. (outside this file) the credentials it holds are a DigitalOcean token scoped to the burst resources and a state-only AWS identity.

Stdlib only. The socket server below is the rebuild runner's, trimmed (copied so this service ships without the rebuild runner's PyYAML/registry code)."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import pwd
import re
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

PROTOCOL = 1
MAX_LINE = 4096
BRANCH = re.compile(r"^agent/k8s/[0-9]+-[a-z0-9][a-z0-9-]*$")
SHA = re.compile(r"^[0-9a-f]{40}$")
COMPONENT = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_SUMMARY = 2800


class Refuse(Exception):
    def __init__(self, code: str, text: str):
        super().__init__(f"{code}: {text}")
        self.code, self.text = code, text


def _tail(s: str, n: int = 300) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s[-n:]


class BurstRunner:
    """`rr.RunnerServer` calls `audit` and `handle_line`; everything else is internal."""

    def __init__(self, checkout: Path, state_dir: Path, repo_url: str, timeout: int = 2700, clock=time.time, out=None):
        self.checkout, self.state_dir, self.repo_url, self.timeout, self.clock = checkout, state_dir, repo_url, timeout, clock
        self._out = out or sys.stdout
        self._lock = threading.Lock()
        self._last: dict = {}

    # -- logging ------------------------------------------------------------------------------------------------------
    def audit(self, event: str, **fields) -> None:
        line = {"ts": int(self.clock()), "component": "aiops-burst-runner", "event": event, **fields}
        self._out.write(json.dumps(line, sort_keys=True) + "\n")
        self._out.flush()

    # -- subprocess ---------------------------------------------------------------------------------------------------
    def _env(self) -> dict:
        """The credentials arrive in this process's environment (systemd EnvironmentFile, written by the root loader); PATH and HOME are fixed here."""
        keep = {k: v for k, v in os.environ.items() if k.startswith(("DIGITALOCEAN_", "AWS_", "ANSIBLE_", "SSH_AUTH_SOCK", "VAULT_", "TF_", "TAILSCALE_"))}
        home = str(self.state_dir / "home")
        return {**keep, "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "BURST_STATE_DIR": str(self.state_dir / "state"), "GIT_TERMINAL_PROMPT": "0",
                "TF_IN_AUTOMATION": "1", "ANSIBLE_FORCE_COLOR": "0", "ANSIBLE_NOCOLOR": "1"}

    def _git(self, *args: str, timeout: int = 180) -> tuple[int, str]:
        p = subprocess.run(["git", "-C", str(self.checkout), *args], capture_output=True, text=True, timeout=timeout, env=self._env())
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    def sync_checkout(self) -> str:
        """A clean checkout of origin/main: clone once, then fetch and hard-reset on every request. Returns the commit the harness will run from."""
        if not (self.checkout / ".git").exists():
            self.checkout.parent.mkdir(parents=True, exist_ok=True)
            p = subprocess.run(["git", "clone", "--quiet", self.repo_url, str(self.checkout)], capture_output=True, text=True, timeout=300, env=self._env())
            if p.returncode != 0:
                raise Refuse("checkout-failed", _tail(p.stderr))
        for args in (("fetch", "--quiet", "--prune", "origin"), ("checkout", "--quiet", "--detach", "origin/main"), ("reset", "--quiet", "--hard", "origin/main"),
                     ("clean", "-fdxq", "--exclude=terraform/digitalocean-burst/.terraform")):
            rc, out = self._git(*args)
            if rc != 0:
                raise Refuse("checkout-failed", f"git {args[0]}: {_tail(out)}")
        rc, head = self._git("rev-parse", "HEAD")
        return head.strip()

    def _branch_head(self, branch: str) -> str:
        rc, out = self._git("ls-remote", "origin", f"refs/heads/{branch}")
        parts = out.split()
        if rc != 0 or not parts or not SHA.match(parts[0]):
            raise Refuse("checkout-failed", "the branch is not on origin")
        return parts[0]

    # -- protocol -----------------------------------------------------------------------------------------------------
    def handle_line(self, raw: bytes) -> dict:
        try:
            req = json.loads(raw)
            if not isinstance(req, dict) or req.get("v") != PROTOCOL:
                raise ValueError
        except ValueError:
            return {"v": PROTOCOL, "ok": False, "error": "bad-request: not a v1 JSON object"}
        rid = req.get("request_id")
        try:
            if req.get("op") == "status":
                return {"v": PROTOCOL, "ok": True, "busy": self._lock.locked(), "last": self._last}
            if req.get("op") == "test":
                res = self._op_test(req)
                return {"v": PROTOCOL, "request_id": rid, "ok": True, "result": res}
            raise Refuse("bad-request", "unknown op")
        except Refuse as r:
            self.audit("refused", code=r.code, text=r.text[:160])
            return {"v": PROTOCOL, "request_id": rid, "ok": False, "error": f"{r.code}: {r.text}"}
        except Exception as e:  # noqa: BLE001 - the server must answer, never die
            self.audit("error", error=type(e).__name__)
            return {"v": PROTOCOL, "request_id": rid, "ok": False, "error": "runner-failed: internal error"}

    @staticmethod
    def _only_keys(req: dict, allowed: set) -> None:
        extra = set(req) - allowed
        if extra:
            raise Refuse("bad-request", f"unexpected field(s): {', '.join(sorted(extra))[:80]}")

    def _op_test(self, req: dict) -> dict:
        self._only_keys(req, {"v", "op", "request_id", "branch", "sha", "component"})
        branch, sha, comp = req.get("branch"), req.get("sha"), req.get("component")
        if not (isinstance(branch, str) and BRANCH.match(branch)):
            raise Refuse("denied", "the branch is not an agent k8s branch")
        if not (isinstance(sha, str) and SHA.match(sha)):
            raise Refuse("bad-request", "sha must be 40 hex characters")
        if not (isinstance(comp, str) and COMPONENT.match(comp)):
            raise Refuse("bad-request", "component must be a plain app name")
        if not self._lock.acquire(blocking=False):
            raise Refuse("busy", "another burst test is running")
        t0 = self.clock()
        try:
            main = self.sync_checkout()
            head = self._branch_head(branch)
            if head != sha:
                raise Refuse("sha-moved", f"the branch head is {head[:12]}, not the approved {sha[:12]}")
            self.audit("test-start", branch=branch, sha=sha[:12], component=comp, harness=main[:12])
            res = self._run(sha, comp, t0)
            self._last = {"branch": branch, "sha": sha, "component": comp, "passed": res["passed"], "at": int(self.clock()), "seconds": res["seconds"]}
            self.audit("test-done", branch=branch, sha=sha[:12], passed=res["passed"], seconds=res["seconds"])
            return res
        finally:
            self._lock.release()

    def _run(self, sha: str, comp: str, t0: float) -> dict:
        """Run the harness from the clean main checkout against the PR's commit. Whatever happens, tear the cluster down afterwards."""
        argv = [str(self.checkout / "scripts" / "burst" / "k8s-pr-test"), "--ref", sha, "--only", comp, "--ttl", "2", "--timeout", "1500"]
        proc = subprocess.Popen(argv, cwd=str(self.checkout), env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        timed_out = False
        try:
            proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
        finally:
            self._teardown()
        return self._result(sha, proc.returncode, timed_out, self.clock() - t0)

    def _teardown(self) -> None:
        """Belt and braces: the harness traps its own exit, but a killed run may not have. burst-down is idempotent and safe on an empty state."""
        with contextlib.suppress(Exception):
            subprocess.run([str(self.checkout / "scripts" / "burst" / "burst-down"), "--yes"], cwd=str(self.checkout), env=self._env(),
                           capture_output=True, text=True, timeout=900)

    def _result(self, sha: str, rc: int | None, timed_out: bool, seconds: float) -> dict:
        d = self.state_dir / "state" / "k8s-test" / sha
        md, passed = "", False
        if timed_out:
            md = "**Burst-cluster test: FAILED**\n\nProblems:\n- the test exceeded the runner's time limit and was stopped; the cluster was destroyed"
        elif (d / "summary.json").exists():
            with contextlib.suppress(Exception):
                passed = bool(json.loads((d / "summary.json").read_text()).get("verdict", {}).get("passed")) and rc == 0
            md = (d / "summary.md").read_text() if (d / "summary.md").exists() else ""
        elif (d / "offline.json").exists():   # the offline gate failed: no cluster was built
            off = json.loads((d / "offline.json").read_text())
            probs = off.get("verdict", {}).get("problems", [])
            md = "**Burst-cluster test: FAILED** (offline gate, no cluster was built)\n\nProblems:\n" + "\n".join(f"- {p}" for p in probs[:10])
        else:
            md = f"**Burst-cluster test: FAILED**\n\nProblems:\n- the harness exited {rc} without a summary"
        return {"passed": bool(passed), "seconds": int(seconds), "summary_md": md[:MAX_SUMMARY], "commit": sha}


def peer_uid(conn: socket.socket) -> int | None:
    """The kernel-attested uid of the connecting process (SO_PEERCRED on Linux; LOCAL_PEERCRED on macOS for dev/tests)."""
    try:
        if hasattr(socket, "SO_PEERCRED"):
            _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
            return uid
        data = conn.getsockopt(0, 0x001, 76)
        return struct.unpack_from("=Ii", data)[1]
    except (OSError, struct.error):
        return None


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        srv = self.server
        runner = srv.runner
        uid = peer_uid(self.request)
        if uid is None or uid not in srv.allowed_uids:
            runner.audit("peer-refused", uid=uid)
            self._send({"v": PROTOCOL, "ok": False, "error": "denied: peer not allowed"})
            return
        self.request.settimeout(10)
        try:
            raw = self.rfile.readline(MAX_LINE + 1)
        except (OSError, TimeoutError):
            return
        self.request.settimeout(None)   # the test itself runs for minutes; the client waits on the same connection
        if not raw:
            return
        if len(raw) > MAX_LINE or not raw.endswith(b"\n"):
            self._send({"v": PROTOCOL, "ok": False, "error": "bad-request: line too long or not newline-terminated"})
            return
        self._send(runner.handle_line(raw.rstrip(b"\n")))

    def _send(self, obj: dict) -> None:
        with contextlib.suppress(OSError):
            self.wfile.write((json.dumps(obj, sort_keys=True) + "\n").encode("utf-8"))
            self.wfile.flush()


class RunnerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path: str, runner: BurstRunner, allowed_uids, group: str | None = None, mode: int = 0o660):
        self.runner = runner
        self.allowed_uids = frozenset(allowed_uids)
        self._path, self._group, self._mode = path, group, mode
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        old = os.umask(0o117)   # never world-accessible, not even for an instant
        try:
            super().__init__(path, _Handler)
        finally:
            os.umask(old)

    def server_bind(self) -> None:
        super().server_bind()
        os.chmod(self._path, self._mode)
        if self._group:
            os.chown(self._path, -1, __import__("grp").getgrnam(self._group).gr_gid)


def main(argv=None) -> int:
    e = os.environ.get
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--checkout", default=e("AIOPS_BURST_CHECKOUT", "/var/lib/aiops-burst/checkout"))
    ap.add_argument("--state-dir", default=e("AIOPS_BURST_STATE_DIR", "/var/lib/aiops-burst"))
    ap.add_argument("--socket", default=e("AIOPS_BURST_SOCKET", "/run/aiops-burst/runner.sock"))
    ap.add_argument("--socket-group", default=e("AIOPS_BURST_SOCKET_GROUP", "aiops-burst-clients"))
    ap.add_argument("--repo-url", default=e("AIOPS_BURST_REPO_URL", "https://github.com/XIIISins/homelab.git"))
    ap.add_argument("--timeout", type=int, default=int(e("AIOPS_BURST_TIMEOUT", "2700")))
    ap.add_argument("--allow-uid", type=int, action="append", default=[])
    ap.add_argument("--allow-user", action="append", default=[])
    args = ap.parse_args(argv)
    uids = set(args.allow_uid)
    for name in args.allow_user:
        try:
            uids.add(pwd.getpwnam(name).pw_uid)
        except KeyError:
            print(f"unknown --allow-user {name!r}", file=sys.stderr)
            return 2
    if not uids:
        print("no allowed peer uid configured (--allow-uid / --allow-user): refusing to start", file=sys.stderr)
        return 2
    state = Path(args.state_dir)
    for sub in ("home", "state"):
        (state / sub).mkdir(parents=True, exist_ok=True)
    runner = BurstRunner(Path(args.checkout), state, args.repo_url, args.timeout)
    srv = RunnerServer(args.socket, runner, uids, args.socket_group or None)
    runner.audit("started", socket=args.socket, allowed_uids=sorted(uids))
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown, daemon=True).start())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(args.socket)
    return 0


if __name__ == "__main__":
    sys.exit(main())
