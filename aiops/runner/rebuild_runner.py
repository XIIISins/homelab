#!/usr/bin/env python3
"""The rebuild runner (Phase 10g, slice B): the only place Terraform may run unattended.

Design: docs/plans/active/10g-rebuild-loop.md ("Running `terraform apply` from an automated path", option 2).
Procedure: docs/procedures/aiops-rebuild.md. Tests: aiops/tests/test_rebuild_runner.py.

A tiny server on a unix socket. One JSON object per line in, one per line out. It never receives a command: only a
class and a target (`plan`) or a plan id (`apply`). Everything that reaches a subprocess is either a constant, a value
from the operator-set CLI flags, or the one Terraform address built from a registry-validated target.

Protocol (v1)
  {"v":1,"request_id":"<uuid>","op":"plan","class":"canary","target":"canary-2"}
    -> {"v":1,"request_id":...,"ok":true,"plan_id":"<sha256>","address":"<terraform address>",
        "summary":{"action":"replace","changes":1,"identity":{"name","vmid","node","ip"}},
        "origin_main":"<sha>","expires_at":<unix>,"problems":[]}
       or ok:false + "error":"plan-rejected: ..." + "problems":[...] when the plan checker refuses the plan
  {"v":1,"request_id":...,"op":"apply","plan_id":"<sha256>"}
    -> {"ok":true,"result":{"applied":true,"resources":1},"seconds":<n>,"origin_main":"<sha>"}
       or {"ok":false,"error":"<code>: <text>"}
  {"v":1,"op":"status"} -> {"ok":true,"busy":false,"last":{...}}
  error codes: plan-unknown, plan-expired, origin-moved, denied, terraform-failed, busy
  (also: bad-request for a malformed line, checkout-failed when git cannot sync the clean checkout)

Defence in depth, each layer independent of the Toolbelt that calls us:
  1. the socket is group-readable only by `aiops-rebuild-clients` and every connection's uid is checked (SO_PEERCRED)
     against an allow-list;
  2. the target is re-validated here against the registry in OUR OWN clean checkout (aiops/actions.yml): the deny
     list, the class allow-list, the class->module/address table below (only `canary` is enabled);
  3. the plan is machine-checked with rebuild.check_plan (exactly one in-scope replace/create, identity unchanged);
  4. apply takes only a plan id we made, unexpired (registry plan_max_age_seconds), whose planfile still hashes to its
     id, from a checkout whose HEAD is still origin/main;
  5. one operation at a time, fleet-wide (`busy`);
  6. (outside this file) the PVE API token can only touch the `aiops-canary` pool.

Stdlib + PyYAML (to read the registry, same dependency as the Toolbelt) + aiops/toolbelt/rebuild.py (reused, not copied).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
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
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "toolbelt"))

import rebuild  # noqa: E402  (aiops/toolbelt/rebuild.py: RebuildPolicy, check_plan, identity)

PROTOCOL = 1
MAX_LINE = 4096
REGISTRY_REL = "aiops/actions.yml"
_REQ_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_PLAN_ID = re.compile(r"^[0-9a-f]{64}$")
_WORD = re.compile(r"^[a-z0-9-]{1,32}$")

# ---------------------------------------------------------------------------------------------------------------------
# class -> Terraform module + resource address. DERIVED FROM THE REAL MODULES; extend only with a reviewed PR.
#
#   canary  terraform/proxmox/asgard-lxcs/lxcs.tf: `resource "proxmox_virtual_environment_container" "canary"` with
#           `for_each = local.canary_nodes` keyed canary-1..3 (vmid 1190-1192, node urd) -> address
#           proxmox_virtual_environment_container.canary["canary-2"]. The documented lossless `-replace` in
#           docs/procedures/canary-pool.md uses exactly this address.
#
# Every other registry class (adguard-replica: its resources are individual, not a for_each; tailscale-router: root
# ticket module; offsite: digitalocean; worker: asgard-k3s) has NO entry here, so it is `denied` by construction until
# a later slice adds a verified row. `target_re` and `vmids` are a second, registry-independent gate on the target.
# ---------------------------------------------------------------------------------------------------------------------
CLASS_TABLE: dict[str, dict] = {
    "canary": {
        "module_dir": "terraform/proxmox/asgard-lxcs",
        "address": 'proxmox_virtual_environment_container.canary["{target}"]',
        "type": "proxmox_virtual_environment_container",
        "target_re": re.compile(r"^canary-[123]$"),
        "vmids": frozenset({1190, 1191, 1192}),
    },
}

# The only environment names that reach git/terraform from the runner's own environment (the credential loader puts
# these in the unit's EnvironmentFile). Nothing else is inherited.
DEFAULT_PASS_ENV = (
    "TF_VAR_proxmox_api_token",
    "TF_VAR_ssh_public_key",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_DEFAULT_REGION",
    "AWS_REGION",
)


@dataclass
class Config:
    checkout: Path
    plan_dir: Path
    terraform: str = "/usr/bin/terraform"
    git: str = "git"
    repo_url: str = "https://github.com/XIIISins/homelab.git"
    remote: str = "origin"
    branch: str = "main"
    enabled_classes: tuple = ("canary",)
    pass_env: tuple = DEFAULT_PASS_ENV
    git_timeout: int = 120
    init_timeout: int = 300
    plan_timeout: int = 300
    apply_timeout: int = 900
    plugin_cache: Path | None = None
    data_dir: Path | None = None
    extra_env: dict = field(default_factory=dict)


class Refuse(Exception):
    """A request refused with a protocol error code."""

    def __init__(self, code: str, text: str, **extra):
        super().__init__(f"{code}: {text}")
        self.code, self.text, self.extra = code, text, extra


def _tail(s: str, n: int = 300) -> str:
    return re.sub(r"[^A-Za-z0-9._:/\[\]\"'=, -]", "?", (s or "").strip()[-n:])


# ---------------------------------------------------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------------------------------------------------


class Runner:
    def __init__(self, cfg: Config, clock=time.time, out=None):
        self.cfg = cfg
        self.clock = clock
        self.out = out or sys.stdout
        self._lock = threading.Lock()  # single flight, fleet-wide
        self._plans: dict[str, dict] = {}
        self._last: dict = {}
        self._audit_lock = threading.Lock()
        cfg.plan_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for stale in cfg.plan_dir.glob("*.tfplan"):  # a restart invalidates every plan (they live in memory only)
            with contextlib.suppress(OSError):
                stale.unlink()

    # -- audit ----------------------------------------------------------------------------------------------------
    def audit(self, event: str, **fields) -> None:
        line = {"ts": int(self.clock()), "component": "aiops-rebuild-runner", "event": event, **fields}
        with self._audit_lock:
            print(json.dumps(line, sort_keys=True, default=str), file=self.out, flush=True)

    # -- subprocess (argv lists only; never a shell) -----------------------------------------------------------------
    def _env(self, cwd_module: str | None = None) -> dict:
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.cfg.plan_dir.parent), "LANG": "C.UTF-8",
               "TF_IN_AUTOMATION": "1", "TF_INPUT": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
        for name in self.cfg.pass_env:
            if name in os.environ:
                env[name] = os.environ[name]
        if self.cfg.plugin_cache:
            self.cfg.plugin_cache.mkdir(parents=True, exist_ok=True)
            env["TF_PLUGIN_CACHE_DIR"] = str(self.cfg.plugin_cache)
        if self.cfg.data_dir and cwd_module:
            d = self.cfg.data_dir / cwd_module.replace("/", "_")
            d.mkdir(parents=True, exist_ok=True)
            env["TF_DATA_DIR"] = str(d)  # outside the checkout, so `git clean -fdx` never removes provider selections
        env.update(self.cfg.extra_env)
        return env

    def _run(self, argv: list[str], cwd: Path, timeout: int, module: str | None = None) -> tuple[int, str, str]:
        try:
            p = subprocess.run(argv, cwd=str(cwd), env=self._env(module), capture_output=True, text=True,
                               timeout=timeout, stdin=subprocess.DEVNULL, check=False)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout}s"
        except OSError as e:
            return 127, "", f"{type(e).__name__}: {e}"

    def _git(self, *args: str, timeout: int | None = None) -> tuple[int, str, str]:
        return self._run([self.cfg.git, "-C", str(self.cfg.checkout), *args], self.cfg.checkout.parent,
                         timeout or self.cfg.git_timeout)

    # -- the clean checkout ----------------------------------------------------------------------------------------
    def sync_checkout(self) -> str:
        """Make the checkout exactly origin/main (the runner's own directory; nothing else ever touches it)."""
        c = self.cfg
        if not (c.checkout / ".git").exists():
            c.checkout.parent.mkdir(parents=True, exist_ok=True)
            rc, _, err = self._run([c.git, "clone", "--quiet", "--depth", "1", "--branch", c.branch, c.repo_url,
                                    str(c.checkout)], c.checkout.parent, c.git_timeout)
            if rc != 0:
                raise Refuse("checkout-failed", f"clone failed: {_tail(err)}")
        for step in (("fetch", "--quiet", "--depth", "1", c.remote, c.branch),
                     ("reset", "--quiet", "--hard", "FETCH_HEAD"),
                     ("clean", "-fdxq", "-e", ".terraform.lock.hcl")):
            rc, _, err = self._git(*step)
            if rc != 0:
                raise Refuse("checkout-failed", f"git {step[0]} failed: {_tail(err)}")
        return self._head()

    def _head(self) -> str:
        rc, out, err = self._git("rev-parse", "HEAD")
        sha = out.strip()
        if rc != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
            raise Refuse("checkout-failed", f"rev-parse failed: {_tail(err)}")
        return sha

    def _origin_sha(self) -> str:
        c = self.cfg
        rc, _, err = self._git("fetch", "--quiet", "--depth", "1", c.remote, c.branch)
        if rc != 0:
            raise Refuse("checkout-failed", f"git fetch failed: {_tail(err)}")
        rc, out, err = self._git("rev-parse", "FETCH_HEAD")
        sha = out.strip()
        if rc != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
            raise Refuse("checkout-failed", f"rev-parse FETCH_HEAD failed: {_tail(err)}")
        return sha

    def _tree_clean(self) -> bool:
        rc, out, _ = self._git("status", "--porcelain")
        return rc == 0 and not out.strip()

    # -- the registry (re-read from OUR checkout) -------------------------------------------------------------------
    def load_policy(self) -> rebuild.RebuildPolicy:
        import yaml  # PyYAML: the one non-stdlib dependency

        try:
            data = yaml.safe_load((self.cfg.checkout / REGISTRY_REL).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            raise Refuse("denied", f"registry unreadable: {type(e).__name__}") from e
        policy = rebuild.RebuildPolicy.from_registry(data if isinstance(data, dict) else {})
        if policy is None:
            raise Refuse("denied", "the registry has no rebuild section")
        return policy

    def validate_target(self, policy: rebuild.RebuildPolicy, cls: str, target: str) -> dict:
        """Everything the runner refuses on its own. Returns the resolved spec (module dir, address, identity)."""
        vmid = policy.vmid_of(target)
        if target in policy.deny_names or (vmid is not None and vmid in policy.deny_vmids):
            raise Refuse("denied", "target is on the deny list")
        rc = policy.class_of(target)
        if rc is None:
            raise Refuse("denied", "target is not in any rebuild class")
        if rc.name != cls:
            raise Refuse("denied", f"target belongs to class {rc.name}, not {cls}")
        entry = CLASS_TABLE.get(cls)
        if entry is None or cls not in self.cfg.enabled_classes:
            raise Refuse("denied", f"class {cls} is not enabled in the runner")
        host = rc.hosts[target]
        if not entry["target_re"].fullmatch(target) or int(host["vmid"]) not in entry["vmids"]:
            raise Refuse("denied", "target is outside the runner's own allow-list for the class")
        module = (self.cfg.checkout / entry["module_dir"]).resolve()
        if self.cfg.checkout.resolve() not in module.parents or not module.is_dir():
            raise Refuse("denied", "module directory is not inside the checkout")
        return {
            "module_dir": entry["module_dir"],
            "module": module,
            "address": entry["address"].format(target=target),
            "type": entry["type"],
            "identity": {"name": target, "vmid": int(host["vmid"]), "node": str(host["node"])},
        }

    # -- request handling ---------------------------------------------------------------------------------------------
    def handle_line(self, raw: bytes) -> dict:
        """One request line -> one response dict. Never raises."""
        req_id = None
        try:
            if len(raw) > MAX_LINE:
                raise Refuse("bad-request", "line too long")
            try:
                req = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as e:
                raise Refuse("bad-request", "not valid JSON") from e
            if not isinstance(req, dict):
                raise Refuse("bad-request", "request must be a JSON object")
            rid = req.get("request_id")
            if rid is not None:
                if not isinstance(rid, str) or not _REQ_ID.fullmatch(rid):
                    raise Refuse("bad-request", "bad request_id")
                req_id = rid
            if req.get("v") != PROTOCOL:
                raise Refuse("bad-request", "unsupported protocol version")
            op = req.get("op")
            handler = {"plan": self._op_plan, "apply": self._op_apply, "status": self._op_status}.get(op)
            if handler is None:
                raise Refuse("bad-request", "unknown op")
            resp = handler(req)
        except Refuse as r:
            self.audit("refused", request_id=req_id, code=r.code, text=r.text)
            resp = {"ok": False, "error": f"{r.code}: {r.text}", **r.extra}
        except Exception as e:  # noqa: BLE001 - a bug must not kill the server or leak a traceback to the peer
            self.audit("internal-error", request_id=req_id, error=type(e).__name__)
            resp = {"ok": False, "error": "terraform-failed: internal error"}
        return {"v": PROTOCOL, **({"request_id": req_id} if req_id else {}), **resp}

    @staticmethod
    def _only_keys(req: dict, allowed: set) -> None:
        extra = set(req) - allowed
        if extra:
            raise Refuse("bad-request", "unexpected field(s): " + ",".join(sorted(_tail(str(k), 20) for k in extra)[:5]))

    def _op_status(self, req: dict) -> dict:
        self._only_keys(req, {"v", "request_id", "op"})
        return {"ok": True, "busy": self._lock.locked(), "last": dict(self._last)}

    def _purge(self) -> None:
        now = self.clock()
        for pid in [p for p, rec in self._plans.items() if rec["expires_at"] <= now]:
            self._drop(pid)
        while len(self._plans) > 8:  # a hard bound
            self._drop(next(iter(self._plans)))

    def _drop(self, plan_id: str) -> None:
        rec = self._plans.pop(plan_id, None)
        if rec:
            with contextlib.suppress(OSError):
                Path(rec["planfile"]).unlink()

    # -- plan -------------------------------------------------------------------------------------------------------
    def _op_plan(self, req: dict) -> dict:
        self._only_keys(req, {"v", "request_id", "op", "class", "target"})
        cls, target = req.get("class"), req.get("target")
        if not (isinstance(cls, str) and _WORD.fullmatch(cls) and isinstance(target, str) and _WORD.fullmatch(target)):
            raise Refuse("bad-request", "class and target must be short lowercase words")
        if not self._lock.acquire(blocking=False):
            raise Refuse("busy", "another operation is in flight")
        t0 = self.clock()
        try:
            self._purge()
            sha = self.sync_checkout()
            policy = self.load_policy()
            spec = self.validate_target(policy, cls, target)
            tf, module = self.cfg.terraform, spec["module"]
            rc, _, err = self._run([tf, "init", "-input=false", "-no-color"], module, self.cfg.init_timeout, spec["module_dir"])
            if rc != 0:
                raise Refuse("terraform-failed", f"init exited {rc}: {_tail(err)}")
            tmp = self.cfg.plan_dir / f"plan-{os.getpid()}-{threading.get_ident()}.tfplan"
            addr = spec["address"]
            rc, _, err = self._run([tf, "plan", f"-replace={addr}", f"-target={addr}", f"-out={tmp}", "-input=false",
                                   "-lock-timeout=60s", "-no-color"], module, self.cfg.plan_timeout, spec["module_dir"])
            if rc != 0:
                # A guest destroyed behind Terraform's back is no longer in state after the refresh, and `-replace` of an
                # address that is not in state fails (found live 2026-10-03, canary-1). The right plan then is a plain
                # single-resource `create`: retry once WITHOUT -replace. Still one -target, and check_plan below still
                # demands exactly one replace/create of the expected address, so a failure for any other reason comes
                # back as a plan with no change and is refused there, never widened.
                self.audit("plan-retry-without-replace", target=target, first_rc=rc)
                rc, _, err = self._run([tf, "plan", f"-target={addr}", f"-out={tmp}", "-input=false",
                                       "-lock-timeout=60s", "-no-color"], module, self.cfg.plan_timeout, spec["module_dir"])
            if rc != 0:
                with contextlib.suppress(OSError):
                    tmp.unlink()
                raise Refuse("terraform-failed", f"plan exited {rc}: {_tail(err)}")
            try:
                digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
                planfile = self.cfg.plan_dir / f"{digest}.tfplan"
                os.chmod(tmp, 0o600)
                os.replace(tmp, planfile)
            except OSError as e:
                raise Refuse("terraform-failed", f"plan file unreadable: {type(e).__name__}") from e
            rc, out, err = self._run([tf, "show", "-json", "-no-color", str(planfile)], module, self.cfg.plan_timeout,
                                     spec["module_dir"])
            if rc != 0:
                self._unlink(planfile)
                raise Refuse("terraform-failed", f"show exited {rc}: {_tail(err)}")
            try:
                plan_json = json.loads(out)
            except ValueError as e:
                self._unlink(planfile)
                raise Refuse("terraform-failed", "show produced invalid JSON") from e
            problems = rebuild.check_plan(plan_json, {"address": addr, "type": spec["type"], "identity": spec["identity"]})
            if problems:
                self._unlink(planfile)
                self._last = {"op": "plan", "class": cls, "target": target, "ok": False, "at": int(self.clock()),
                              "origin_main": sha, "problems": problems[:5]}
                self.audit("plan-rejected", target=target, address=addr, origin_main=sha, problems=problems[:10])
                return {"ok": False, "error": "plan-rejected: the plan failed the shape checks", "problems": problems,
                        "address": addr, "origin_main": sha}
            acting = [r for r in plan_json.get("resource_changes", []) if (r.get("change") or {}).get("actions") not in (["no-op"], ["read"])]
            mine = next(r for r in acting if r.get("address") == addr)
            actions = mine["change"]["actions"]
            ident = rebuild.identity(mine["change"].get("after") or {})
            ip = str(ident.get("ip", "")).split("/")[0]
            summary = {
                "action": "create" if actions == ["create"] else "replace",
                "changes": len(acting),
                "identity": {"name": ident.get("name", target), "vmid": ident.get("vmid", spec["identity"]["vmid"]),
                             "node": ident.get("node", spec["identity"]["node"]), "ip": ip},
            }
            expires = int(t0 + policy.limits.plan_max_age_seconds)
            self._plans[digest] = {"planfile": str(planfile), "address": addr, "class": cls, "target": target,
                                   "origin_main": sha, "expires_at": expires, "changes": len(acting)}
            self._last = {"op": "plan", "class": cls, "target": target, "ok": True, "at": int(self.clock()),
                          "plan_id": digest, "origin_main": sha}
            self.audit("plan-ok", target=target, address=addr, plan_id=digest, origin_main=sha, expires_at=expires,
                       action=summary["action"], seconds=round(self.clock() - t0, 1))
            return {"ok": True, "plan_id": digest, "address": addr, "summary": summary, "origin_main": sha,
                    "expires_at": expires, "problems": []}
        finally:
            self._lock.release()

    @staticmethod
    def _unlink(p: Path) -> None:
        with contextlib.suppress(OSError):
            p.unlink()

    # -- apply ------------------------------------------------------------------------------------------------------
    def _op_apply(self, req: dict) -> dict:
        self._only_keys(req, {"v", "request_id", "op", "plan_id"})
        pid = req.get("plan_id")
        if not (isinstance(pid, str) and _PLAN_ID.fullmatch(pid)):
            raise Refuse("bad-request", "plan_id must be a sha256 hex string")
        if not self._lock.acquire(blocking=False):
            raise Refuse("busy", "another operation is in flight")
        t0 = self.clock()
        rec = None
        try:
            rec = self._plans.get(pid)
            if rec is None:
                raise Refuse("plan-unknown", "no such plan (plans live in memory and die with the runner)")
            if rec["expires_at"] <= self.clock():
                self._drop(pid)
                rec = None
                raise Refuse("plan-expired", "the plan is older than plan_max_age_seconds; plan again")
            planfile = Path(rec["planfile"])
            try:
                ok = hashlib.sha256(planfile.read_bytes()).hexdigest() == pid
            except OSError:
                ok = False
            if not ok:
                raise Refuse("denied", "the plan file no longer matches its id")
            # the checkout must still be exactly the commit the plan was made from, clean, and origin/main must not have moved
            if self._head() != rec["origin_main"] or not self._tree_clean():
                raise Refuse("origin-moved", "the checkout is no longer the planned commit")
            now_origin = self._origin_sha()
            if now_origin != rec["origin_main"]:
                raise Refuse("origin-moved", f"origin/main moved since the plan ({rec['origin_main'][:12]} -> {now_origin[:12]})")
            policy = self.load_policy()  # re-validate (deny list, class table) against the registry at the planned commit
            spec = self.validate_target(policy, rec["class"], rec["target"])
            if spec["address"] != rec["address"]:
                raise Refuse("denied", "the address no longer matches the registry")
            rc, _, err = self._run([self.cfg.terraform, "apply", "-input=false", "-lock-timeout=60s", "-no-color",
                                    str(planfile)], spec["module"], self.cfg.apply_timeout, spec["module_dir"])
            seconds = round(self.clock() - t0, 1)
            self._drop(pid)  # single use: applied or failed, the plan is spent
            rec_ok = rc == 0
            self._last = {"op": "apply", "class": rec["class"], "target": rec["target"], "ok": rec_ok, "plan_id": pid,
                          "at": int(self.clock()), "seconds": seconds, "origin_main": rec["origin_main"]}
            if not rec_ok:
                self.audit("apply-failed", target=rec["target"], plan_id=pid, rc=rc, tail=_tail(err), seconds=seconds)
                raise Refuse("terraform-failed", f"apply exited {rc}: {_tail(err)}")
            self.audit("apply-ok", target=rec["target"], address=rec["address"], plan_id=pid, seconds=seconds,
                       origin_main=rec["origin_main"])
            return {"ok": True, "result": {"applied": True, "resources": rec["changes"]}, "seconds": seconds,
                    "origin_main": rec["origin_main"]}
        except Refuse as r:
            # a plan refused for being moved/denied can never be retried: it is single use
            if rec is not None and r.code in ("origin-moved", "denied", "checkout-failed"):
                self._drop(pid)
            raise
        finally:
            self._lock.release()


# ---------------------------------------------------------------------------------------------------------------------
# The socket server
# ---------------------------------------------------------------------------------------------------------------------


def peer_uid(conn: socket.socket) -> int | None:
    """The kernel-attested uid of the connecting process (SO_PEERCRED on Linux; LOCAL_PEERCRED on macOS for dev/tests)."""
    try:
        if hasattr(socket, "SO_PEERCRED"):
            _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
            return uid
        data = conn.getsockopt(0, 0x001, 76)  # SOL_LOCAL / LOCAL_PEERCRED -> struct xucred
        return struct.unpack_from("=Ii", data)[1]
    except (OSError, struct.error):
        return None


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        srv: RunnerServer = self.server  # type: ignore[assignment]
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
        self.request.settimeout(None)
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

    def __init__(self, path: str, runner: Runner, allowed_uids, group: str | None = None, mode: int = 0o660):
        self.runner = runner
        self.allowed_uids = frozenset(allowed_uids)
        self._path, self._group, self._mode = path, group, mode
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        old = os.umask(0o117)  # never world-accessible, not even for an instant
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
    ap.add_argument("--checkout", default=e("AIOPS_REBUILD_CHECKOUT", "/var/lib/aiops-rebuild/checkout"))
    ap.add_argument("--plan-dir", default=e("AIOPS_REBUILD_PLAN_DIR", "/var/lib/aiops-rebuild/plans"))
    ap.add_argument("--data-dir", default=e("AIOPS_REBUILD_DATA_DIR", "/var/lib/aiops-rebuild/tfdata"))
    ap.add_argument("--plugin-cache", default=e("AIOPS_REBUILD_PLUGIN_CACHE", "/var/lib/aiops-rebuild/plugin-cache"))
    ap.add_argument("--socket", default=e("AIOPS_REBUILD_SOCKET", "/run/aiops-rebuild/runner.sock"))
    ap.add_argument("--socket-group", default=e("AIOPS_REBUILD_SOCKET_GROUP", "aiops-rebuild-clients"))
    ap.add_argument("--terraform", default=e("AIOPS_REBUILD_TERRAFORM", "/usr/bin/terraform"))
    ap.add_argument("--repo-url", default=e("AIOPS_REBUILD_REPO_URL", "https://github.com/XIIISins/homelab.git"))
    ap.add_argument("--allow-uid", type=int, action="append", default=[])
    ap.add_argument("--allow-user", action="append", default=[])
    ap.add_argument("--enable-class", action="append", default=[])
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
    classes = tuple(args.enable_class) or ("canary",)
    if any(c not in CLASS_TABLE for c in classes):
        print("an --enable-class has no CLASS_TABLE entry", file=sys.stderr)
        return 2
    cfg = Config(checkout=Path(args.checkout), plan_dir=Path(args.plan_dir), terraform=args.terraform,
                 repo_url=args.repo_url, enabled_classes=classes, plugin_cache=Path(args.plugin_cache),
                 data_dir=Path(args.data_dir))
    runner = Runner(cfg)
    srv = RunnerServer(args.socket, runner, uids, args.socket_group or None)
    runner.audit("started", socket=args.socket, allowed_uids=sorted(uids), classes=list(classes))
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
