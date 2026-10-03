"""Action proposals, operator approval and the executor (Phase 10e1).

The agent (n8n) can only PROPOSE. A proposal is a registry action plus typed parameters; it is validated
against aiops/actions.yml (declared vars only, patterns full-matched, tier/host/unit/release guards) before it is
stored, and it is bound to its parameters by a hash so that what the operator approves is exactly what runs. Only
the approver credential (held by the Discord bot, never by the agent) can decide a proposal; the decision names
the human (their Discord user id must be on the operator allow-list) and the exact params hash they were shown.

    pending ──approve──> approved ──> running ──> succeeded | failed | verify_failed
       │                    └──kill switch / restart──> cancelled
       ├──reject──> rejected
       └──ttl──> expired
    (a replay proposal is never decidable: acceptance runs must never touch a real host)

The executor runs ONLY registry templates through Semaphore's API, with the declared extra-vars as the task
`environment` (a JSON string; known-issues/zabbix.md), then evaluates the registry `verify` post-condition. The
playbooks re-enforce every guard themselves, so a misbehaving executor still cannot widen one.

Phase 10f adds AUTONOMY: for a narrow, reviewed class of faults (aiops/actions.yml `autonomy:`) the Toolbelt may approve
and run a proposal itself, under the name `auto:<policy>`. It is never the model's decision: a diagnosis only supplies the
proposal, and the Toolbelt applies the policy gates (master switch, kill switch, maintenance, circuit breaker, scope,
layer, confidence, runbook, rate limits) and its OWN read of reality (a precheck) before acting. A run that fails or does
not verify counts toward the breaker; enough of them stop autonomy until an operator re-arms it. A proposal whose fault
has already healed ends `skipped`, not executed.

Everything is pure logic over the Toolbelt's SQLite connection plus an injectable Semaphore client and clock, so
every rule is unit-tested without a socket (aiops/tests/test_actions.py, test_autonomy.py).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import autonomy

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER,
  conversation_id INTEGER,
  thread_id TEXT,
  source TEXT NOT NULL,
  action_id TEXT NOT NULL,
  params_json TEXT NOT NULL,
  params_hash TEXT NOT NULL,
  tier TEXT NOT NULL,
  target TEXT NOT NULL,
  reason TEXT NOT NULL,
  state TEXT NOT NULL,
  replay INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  decided_at INTEGER,
  decided_by TEXT,
  decision_ref TEXT,
  started_at INTEGER,
  finished_at INTEGER,
  result_json TEXT,
  message_ref TEXT
);
CREATE INDEX IF NOT EXISTS proposals_state ON proposals(state, expires_at);
CREATE INDEX IF NOT EXISTS proposals_incident ON proposals(incident_id);
CREATE TABLE IF NOT EXISTS proposal_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  proposal_id INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS flags (
  name TEXT PRIMARY KEY, value INTEGER NOT NULL, set_by TEXT, set_at INTEGER, reason TEXT
);
CREATE TABLE IF NOT EXISTS auto_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, proposal_id INTEGER NOT NULL,
  policy TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS auto_log_ts ON auto_log(ts);
"""

TERMINAL = {"rejected", "expired", "cancelled", "succeeded", "failed", "verify_failed", "skipped"}
_NEXT = {
    "pending": {"approved", "rejected", "expired", "cancelled"},
    "approved": {"running", "cancelled", "failed"},
    "running": {"succeeded", "failed", "verify_failed", "cancelled", "skipped"},
}
# autonomy = the master switch for autonomous healing (default OFF); autonomy_breaker = tripped by the system after repeated
# autonomous failures, re-armed only by an operator.
FLAGS = ("kill_switch", "maintenance", "autonomy", "autonomy_breaker")
AUTO_STATES = ("approved", "running", "succeeded", "failed", "verify_failed")  # an autonomous run that actually started counts against the limits
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_RESULT = re.compile(r"AIOPS_RESULT\s+(\{.*\})")
_RECAP = re.compile(r"^\s*(\S+)\s*:\s*ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)")
_SECRETISH = re.compile(r"(sk-ant-[\w-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/=-]{16,}|"
                        r"discord(?:app)?\.com/api/webhooks/\d+/[\w-]+|\bhvs\.[A-Za-z0-9]{16,}|\bpassword\s*[=:]\s*\S{6,})", re.I)


class Refused(Exception):
    """A request the engine refuses; `status` maps to HTTP in server.py."""

    def __init__(self, status: int, message: str, detail: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.detail = status, message, detail or {}


# ---- registry ------------------------------------------------------------------------------------------

class Registry:
    """The action registry (aiops/actions.yml): parameter validation, guards, target keys."""

    def __init__(self, data: dict):
        self.actions: dict = data["actions"]
        self.tiers: dict = data.get("host_tiers", {})
        self.autonomy = autonomy.Autonomy.from_registry(data)

    @classmethod
    def from_file(cls, path: Path) -> "Registry":
        import yaml

        return cls(yaml.safe_load(Path(path).read_text()))

    def get(self, action_id: str) -> dict:
        a = self.actions.get(action_id)
        if a is None:
            raise Refused(404, f"no such action {action_id!r} in the registry")
        return a

    def validate_params(self, action_id: str, params: object) -> tuple[dict, list[str]]:
        """Only declared vars, typed, patterns full-matched, no control characters. Returns (clean params, problems)."""
        a = self.get(action_id)
        decl: dict = a.get("extra_vars", {})
        problems: list[str] = []
        if not isinstance(params, dict):
            return {}, ["params must be an object"]
        for k in sorted(set(params) - set(decl)):
            problems.append(f"{k!r} is not a declared parameter of {action_id}")
        clean: dict = {}
        for name, spec in decl.items():
            if name not in params:
                if "default" in spec:
                    clean[name] = spec["default"]
                elif spec.get("required"):
                    problems.append(f"missing required parameter {name!r}")
                continue
            v = params[name]
            t = spec["type"]
            if t in ("string", "enum"):
                if not isinstance(v, str) or _CTRL.search(v):
                    problems.append(f"{name} must be a plain string")
                    continue
                if t == "enum" and v not in spec.get("values", []):
                    problems.append(f"{name} must be one of {spec.get('values')}")
                    continue
                if t == "string" and spec.get("pattern") and not re.fullmatch(spec["pattern"], v):
                    problems.append(f"{name} {v!r} does not match {spec['pattern']}")
                    continue
            elif t == "integer":
                if not isinstance(v, int) or isinstance(v, bool):
                    problems.append(f"{name} must be an integer")
                    continue
            elif t == "boolean":
                if not isinstance(v, bool):
                    problems.append(f"{name} must be a boolean")
                    continue
            clean[name] = v
        return clean, problems

    def guard(self, action_id: str, clean: dict) -> list[str]:
        """The registry guard the executor enforces BEFORE anything runs (the playbooks enforce it again)."""
        a = self.get(action_id)
        g = a.get("guard", {})
        p: list[str] = []
        if a.get("max_autonomy") == "none":
            p.append(f"{action_id} has max_autonomy none: it can never be proposed")
        if not a.get("semaphore", {}).get("applied", False):
            p.append(f"the Semaphore template for {action_id} is not applied yet (operator gate)")
        host = clean.get("target_host")
        if g.get("target_policy") == "host_tiers.T1" and host not in self.tiers.get("T1", []):
            p.append(f"{host!r} is not a T1 host; {action_id} may only touch T1 hosts")
        if "allowed_units" in g and clean.get("unit") not in g["allowed_units"].get(host, []):
            p.append(f"unit {clean.get('unit')!r} is not allow-listed on {host!r} for {action_id}")
        if "allowed_tags" in g and clean.get("role_tag") not in g["allowed_tags"]:
            p.append(f"role_tag {clean.get('role_tag')!r} is not an allowed tag")
        if "allowed_releases" in g and clean.get("hr_name") not in g["allowed_releases"]:
            p.append(f"HelmRelease {clean.get('hr_name')!r} is not on the allow-list for {action_id}")
        if clean.get("hr_name") in g.get("denied_releases", []):
            p.append(f"HelmRelease {clean.get('hr_name')!r} is on the stateful deny-list")
        return p

    def target_of(self, action_id: str, clean: dict) -> str:
        if "target_host" in clean:
            return clean["target_host"]
        if "hr_name" in clean:
            return f"hr:{clean.get('hr_namespace', '?')}/{clean['hr_name']}"
        return f"action:{action_id}"

    def semaphore_task(self, action_id: str, clean: dict) -> tuple[str, dict, dict]:
        """(template name, extra-vars for the task environment, task fields) with {var} placeholders resolved."""
        a = self.get(action_id)
        env = dict(clean)
        env.update(a.get("fixed_vars", {}))
        fields = _subst(a["semaphore"].get("task_fields", {}), clean)
        return a["semaphore"]["template"], env, fields


def _subst(node, vars_: dict):
    if isinstance(node, str):
        return re.sub(r"\{(\w+)\}", lambda m: str(vars_.get(m.group(1), m.group(0))), node)
    if isinstance(node, list):
        return [_subst(x, vars_) for x in node]
    if isinstance(node, dict):
        return {k: _subst(v, vars_) for k, v in node.items()}
    return node


def params_hash(action_id: str, params: dict) -> str:
    return hashlib.sha256(json.dumps({"a": action_id, "p": params}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


# ---- Semaphore client + output parsing -----------------------------------------------------------------

class Semaphore(Protocol):
    def template_id(self, name: str) -> int: ...
    def start(self, template_id: int, environment: dict, fields: dict) -> int: ...
    def status(self, task_id: int) -> str: ...
    def output(self, task_id: int) -> list[str]: ...


class SemaphoreAPI:
    """Semaphore's REST API with the executor token (Task Runner on the dedicated aiops project only)."""

    def __init__(self, base: str, token: str, project: int | None = None, timeout: float = 20.0, project_name: str = "aiops"):
        self.base, self.token, self.project, self.timeout, self.project_name = base.rstrip("/"), token, project, timeout, project_name
        self._tpl: dict[str, int] = {}

    def _pid(self) -> int:
        """The project id, found by name on first use (the executor's user is a member of exactly one project, so
        GET /projects returns just that one; the id is unknown until terraform/semaphore has been applied)."""
        if self.project is None:
            for pr in self._call("GET", "/projects") or []:
                if pr.get("name") == self.project_name:
                    self.project = int(pr["id"])
                    break
            else:
                raise Refused(404, f"the executor's Semaphore user is not a member of a project named {self.project_name!r}")
        return self.project

    def _call(self, method: str, path: str, body=None):
        req = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + self.token, "Accept": "application/json",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise Refused(502, f"semaphore answered HTTP {e.code} for {method} {path.split('?')[0]}")
        except (OSError, ValueError) as e:
            raise Refused(502, f"semaphore unreachable: {type(e).__name__}")

    def template_id(self, name: str) -> int:
        if name not in self._tpl:
            for t in self._call("GET", f"/project/{self._pid()}/templates") or []:
                self._tpl[t["name"]] = t["id"]
        if name not in self._tpl:
            raise Refused(404, f"Semaphore has no template named {name!r} in project {self.project_name!r}")
        return self._tpl[name]

    def start(self, template_id: int, environment: dict, fields: dict) -> int:
        body = {"template_id": template_id, "debug": False, "dry_run": False, "diff": False,
                "environment": json.dumps(environment, sort_keys=True)}
        if fields.get("limit"):
            body["limit"] = str(fields["limit"])
        if fields.get("arguments"):
            body["arguments"] = json.dumps(list(fields["arguments"]))
        res = self._call("POST", f"/project/{self._pid()}/tasks", body)
        return int(res["id"])

    def status(self, task_id: int) -> str:
        return str((self._call("GET", f"/project/{self._pid()}/tasks/{task_id}") or {}).get("status", "unknown"))

    def output(self, task_id: int) -> list[str]:
        return [_ANSI.sub("", o.get("output", "")) for o in (self._call("GET", f"/project/{self._pid()}/tasks/{task_id}/output") or [])]


def parse_output(lines: list[str], target_host: str | None, result_from: str = "aiops_result") -> dict:
    """The machine-readable outcome of one task: every AIOPS_RESULT line merged (later wins), plus, for
    `result_from: recap` actions (replay-role-check / replay-role, whose playbooks cannot see their own stats), ok
    and changed taken from the target host's PLAY RECAP line."""
    merged: dict = {}
    for line in lines:
        m = _RESULT.search(line)
        if not m:
            continue
        raw = m.group(1)
        for cand in (raw, raw.replace('\\"', '"'), raw.replace('\\"', '"').replace("\\\\", "\\")):
            try:
                d = json.loads(cand.rstrip('"'))
            except ValueError:
                continue
            if isinstance(d, dict):
                merged.update(d)
                break
    if result_from == "recap":
        recap = None
        for line in lines:
            m = _RECAP.match(line)
            if m and (target_host is None or m.group(1) == target_host):
                recap = m
        if recap is None:
            merged.update(ok=False, recap_missing=True)
        else:
            changed, unreachable, failed = int(recap.group(3)), int(recap.group(4)), int(recap.group(5))
            merged.update(changed=changed, unreachable=unreachable, failed=failed, ok=(unreachable == 0 and failed == 0))
    return merged


def evaluate(expect: dict, result: dict) -> list[str]:
    """Registry `verify.expect` against a result. 'True'/'true' strings and bools compare equal."""
    def norm(v):
        return str(v).lower() if isinstance(v, (bool, str)) else v

    return [f"{k}: expected {v!r}, got {result.get(k)!r}" for k, v in expect.items() if norm(result.get(k)) != norm(v)]


def redact(text: str) -> str:
    return _SECRETISH.sub("<redacted>", text)


# ---- engine ----------------------------------------------------------------------------------------------

@dataclass
class ActionConfig:
    operators: frozenset = frozenset()   # Discord user ids allowed to decide (the Toolbelt double-checks the bot)
    proposal_ttl: int = 1800             # seconds a proposal may wait for a decision
    max_pending_per_incident: int = 3
    max_proposals_per_day: int = 30
    max_running: int = 2
    task_timeout: int = 600              # one Semaphore task
    poll_seconds: float = 2.0
    stale_approval_seconds: int = 300    # an approval older than this when the Toolbelt (re)starts is not run
    semaphore: Semaphore | None = None
    reader: Callable[[str, dict], dict] | None = None  # read-only Toolbelt tool (kube.get) for the autonomy prechecks
    sleep: Callable[[float], None] = time.sleep


class Engine:
    def __init__(self, db, lock: threading.Lock, clock: Callable[[], float], audit: Callable[..., None], registry: Registry,
                 cfg: ActionConfig, bump: Callable[[str], int], counter: Callable[[str], int]):
        if not hasattr(lock, "_is_owned"):
            raise TypeError("Engine needs the Toolbelt's re-entrant lock (threading.RLock)")
        self.db, self.lock, self.clock, self.audit, self.reg, self.cfg = db, lock, clock, audit, registry, cfg
        self._bump, self._counter = bump, counter
        self._run_slots = threading.Semaphore(cfg.max_running)
        self.db.executescript(SCHEMA)
        self._recover()

    def now(self) -> int:
        return int(self.clock())

    # -- flags ---------------------------------------------------------------------------------------------
    def flag(self, name: str) -> bool:
        with self.lock:
            row = self.db.execute("SELECT value FROM flags WHERE name=?", (name,)).fetchone()
        return bool(row["value"]) if row else False

    def flags(self) -> dict:
        out = {n: {"value": False, "set_by": None, "set_at": None, "reason": None} for n in FLAGS}
        with self.lock:
            for r in self.db.execute("SELECT * FROM flags").fetchall():
                out[r["name"]] = {"value": bool(r["value"]), "set_by": r["set_by"], "set_at": r["set_at"], "reason": r["reason"]}
        return out

    def set_flag(self, name: str, value: bool, by: str, reason: str = "", system: bool = False) -> dict:
        if name not in FLAGS:
            raise Refused(400, f"unknown flag {name!r}")
        if name == "autonomy_breaker" and value and not system:
            raise Refused(403, "only the system trips the circuit breaker")
        if not system:
            self._check_operator(by)
        with self.lock:
            self.db.execute("INSERT INTO flags(name, value, set_by, set_at, reason) VALUES (?, ?, ?, ?, ?) "
                            "ON CONFLICT(name) DO UPDATE SET value=excluded.value, set_by=excluded.set_by, "
                            "set_at=excluded.set_at, reason=excluded.reason", (name, int(value), by, self.now(), reason[:200]))
            if name == "kill_switch" and value:
                # nothing waiting to run may start; a running task is left to finish (stopping a half-done converge is worse)
                for r in self.db.execute("SELECT id FROM proposals WHERE state='approved'").fetchall():
                    self._move(r["id"], "cancelled", "kill-switch", {"by": by})
        self.audit("flag", flag=name, value=value, by=by, reason=reason[:80])
        return self.flags()[name]

    def _check_operator(self, by: object) -> None:
        if not isinstance(by, str) or by not in self.cfg.operators:
            self.audit("decision_denied", reason="not-an-operator", by=str(by)[:30])
            raise Refused(403, "not an operator")

    # -- lifecycle plumbing --------------------------------------------------------------------------------
    def _event(self, pid: int, kind: str, data: dict | None = None) -> None:
        self.db.execute("INSERT INTO proposal_events(proposal_id, ts, kind, data_json) VALUES (?, ?, ?, ?)",
                        (pid, self.now(), kind, json.dumps(data or {}, sort_keys=True)))

    def _move(self, pid: int, state: str, kind: str, data: dict | None = None, **cols) -> None:
        """Caller holds the lock. A state may only move along _NEXT."""
        cur = self.db.execute("SELECT state FROM proposals WHERE id=?", (pid,)).fetchone()
        if cur is None:
            raise Refused(404, f"no proposal {pid}")
        if state not in _NEXT.get(cur["state"], set()):
            raise Refused(409, f"proposal {pid} cannot move from {cur['state']} to {state}")
        sets = ["state=?"] + [f"{c}=?" for c in cols]
        self.db.execute(f"UPDATE proposals SET {', '.join(sets)} WHERE id=?", (state, *cols.values(), pid))
        self._event(pid, kind, {"state": state, **(data or {})})

    def _row(self, pid: int):
        row = self.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise Refused(404, f"no proposal {pid}")
        return row

    def _recover(self) -> None:
        """After a restart: a proposal that was `running` is failed (we cannot know how far it got) and an old approval is
        not run (it was approved for a situation that may have passed)."""
        now = self.now()
        with self.lock:
            for r in self.db.execute("SELECT id, state, decided_at FROM proposals WHERE state IN ('running','approved')").fetchall():
                if r["state"] == "running":
                    self._move(r["id"], "failed", "recovered", {"reason": "the Toolbelt restarted mid-run"}, finished_at=now)
                elif now - (r["decided_at"] or 0) > self.cfg.stale_approval_seconds:
                    self._move(r["id"], "cancelled", "recovered", {"reason": "approval went stale across a restart"})

    def sweep(self) -> int:
        """Expire proposals nobody decided in time."""
        n = 0
        with self.lock:
            for r in self.db.execute("SELECT id FROM proposals WHERE state='pending' AND expires_at <= ?", (self.now(),)).fetchall():
                self._move(r["id"], "expired", "expired")
                n += 1
        if n:
            self.audit("proposals_expired", n=n)
        return n

    # -- propose -------------------------------------------------------------------------------------------
    def propose(self, *, action_id: str, params: object, reason: object, source: str, incident_id: int | None = None,
                conversation_id: int | None = None, thread_id: str | None = None, replay: bool = False) -> dict:
        self.sweep()
        if not isinstance(reason, str) or not 3 <= len(reason) <= 300 or _CTRL.search(reason) or _SECRETISH.search(reason):
            raise Refused(400, "reason must be 3..300 plain characters and contain nothing secret-shaped")
        clean, problems = self.reg.validate_params(action_id, params)
        if not problems:
            problems = self.reg.guard(action_id, clean)
        if problems:
            self.audit("proposal_rejected", action=action_id, source=source, problems=len(problems))
            raise Refused(422, "proposal failed validation", {"problems": problems})
        a = self.reg.get(action_id)
        h = params_hash(action_id, clean)
        with self.lock:
            dup = self.db.execute("SELECT id FROM proposals WHERE action_id=? AND params_hash=? AND state IN ('pending','approved','running') "
                                  "AND replay=?", (action_id, h, int(replay))).fetchone()
            if dup is not None:
                return self._view(dup["id"], duplicate=True)
            if incident_id is not None and self.db.execute(
                    "SELECT COUNT(*) FROM proposals WHERE incident_id=? AND state='pending'", (incident_id,)).fetchone()[0] >= self.cfg.max_pending_per_incident:
                self.audit("proposal_denied", reason="per-incident-cap", incident=incident_id)
                raise Refused(429, "too many pending proposals for this incident")
            if self._counter("proposals") >= self.cfg.max_proposals_per_day:
                self.audit("proposal_denied", reason="daily-cap")
                raise Refused(429, "daily proposal cap reached")
            self._bump("proposals")
            now = self.now()
            cur = self.db.execute(
                "INSERT INTO proposals(incident_id, conversation_id, thread_id, source, action_id, params_json, params_hash, tier, target, "
                "reason, state, replay, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                (incident_id, conversation_id, thread_id, source, action_id, json.dumps(clean, sort_keys=True), h, a["tier"],
                 self.reg.target_of(action_id, clean), reason, int(replay), now, now + self.cfg.proposal_ttl))
            pid = cur.lastrowid
            self._event(pid, "created", {"action": action_id, "target": self.reg.target_of(action_id, clean), "source": source})
        self.audit("proposal_created", proposal=pid, action=action_id, tier=a["tier"], source=source, incident=incident_id, replay=replay)
        return self._view(pid)

    # -- decide --------------------------------------------------------------------------------------------
    def decide(self, pid: int, decision: str, *, by: str, ref: str = "", params_hash_seen: str = "", run: bool = True) -> dict:
        """Approve or reject. `params_hash_seen` is the hash printed on the card the human clicked: a mismatch (the proposal
        changed under them, which it cannot, or a forged request) refuses the decision."""
        if decision not in ("approve", "reject"):
            raise Refused(400, "decision must be approve or reject")
        self._check_operator(by)
        self.sweep()
        with self.lock:
            row = self._row(pid)
            if row["replay"]:
                raise Refused(409, "a replay proposal can never be decided or executed")
            if row["state"] != "pending":
                raise Refused(409, f"proposal {pid} is {row['state']}, not pending")
            if params_hash_seen and params_hash_seen != row["params_hash"]:
                self.audit("decision_denied", reason="params-hash-mismatch", proposal=pid, by=by)
                raise Refused(409, "the proposal does not match what was approved")
            if decision == "approve" and self.flag("kill_switch"):
                raise Refused(409, "the kill switch is engaged: nothing may be approved or run")
            now = self.now()
            if decision == "approve":
                self._move(pid, "approved", "approved", {"by": by, "ref": ref}, decided_at=now, decided_by=by, decision_ref=ref[:80])
            else:
                self._move(pid, "rejected", "rejected", {"by": by, "ref": ref}, decided_at=now, decided_by=by, decision_ref=ref[:80])
        self.audit("proposal_" + ("approved" if decision == "approve" else "rejected"), proposal=pid, by=by, action=row["action_id"], ref=ref[:40])
        out = self._view(pid)  # the state AT the decision, taken before the executor can move it on
        if decision == "approve" and run:
            threading.Thread(target=self.execute, args=(pid,), daemon=True, name=f"exec-{pid}").start()
        return out

    # -- execute -------------------------------------------------------------------------------------------
    def _sem(self) -> Semaphore:
        if self.cfg.semaphore is None:
            raise Refused(501, "the executor is not configured (no Semaphore credential)")
        return self.cfg.semaphore

    def _run_task(self, pid: int, action_id: str, clean: dict, step: str) -> tuple[str, dict, list[str]]:
        """One Semaphore task from the registry. Returns (status, parsed result, redacted output tail)."""
        sem = self._sem()
        template, env, fields = self.reg.semaphore_task(action_id, clean)
        task_id = sem.start(sem.template_id(template), env, fields)
        with self.lock:
            self._event(pid, "step_started", {"step": step, "action": action_id, "task": task_id})
        deadline = self.now() + self.cfg.task_timeout
        status = "waiting"
        while True:
            status = sem.status(task_id)
            if status in ("success", "error", "stopped", "rejected"):
                break
            if self.now() >= deadline:
                status = "timeout"
                break
            self.cfg.sleep(self.cfg.poll_seconds)
        lines = sem.output(task_id) if status != "timeout" else []
        result_from = self.reg.get(action_id)["semaphore"].get("result_from", "aiops_result")
        result = parse_output(lines, clean.get("target_host"), result_from)
        for _ in range(3 if status == "success" and not result else 0):
            # Semaphore marks a task finished slightly before its last log rows are readable (found live in 10f: a verify
            # task that ran fine was read as an empty result, so a restart that worked ended verify_failed). Re-read.
            self.cfg.sleep(2)
            lines = sem.output(task_id)
            result = parse_output(lines, clean.get("target_host"), result_from)
            if result:
                break
        tail = [redact(ln)[:300] for ln in lines[-12:]]
        with self.lock:
            self._event(pid, "step_finished", {"step": step, "action": action_id, "task": task_id, "status": status, "result": result})
        self.audit("step", proposal=pid, step=step, action=action_id, task=task_id, status=status, ok=result.get("ok"))
        return status, result, tail

    def execute(self, pid: int) -> dict:
        """Run an approved proposal: guard again, optional prior step, the action, then the registry verify. Never raises
        out of a worker thread: every failure lands in the proposal's state and the audit log."""
        with self._run_slots:
            try:
                self._execute(pid)
            except Refused as e:
                self._fail(pid, "failed", f"{e.message}")
            except Exception as e:  # noqa: BLE001 - the worker must report, not die silently
                self._fail(pid, "failed", f"internal error: {type(e).__name__}")
            try:
                self._finish_auto(pid)
            except Exception as e:  # noqa: BLE001 - bookkeeping must never take the worker down
                self.audit("error", where="finish_auto", error=type(e).__name__)
            return self._view(pid)

    def _fail(self, pid: int, state: str, why: str, extra: dict | None = None) -> None:
        with self.lock:
            cur = self._row(pid)["state"]
            if cur in _NEXT and state in _NEXT[cur]:
                self._move(pid, state, state, {"why": why, **(extra or {})}, finished_at=self.now(),
                           result_json=json.dumps({"why": why, **(extra or {})}, sort_keys=True))
        self.audit("proposal_" + state, proposal=pid, why=why[:120])

    def _execute(self, pid: int) -> dict:
        with self.lock:
            row = self._row(pid)
            if row["state"] != "approved":
                return self._view(pid)
            if self.flag("kill_switch"):
                self._move(pid, "cancelled", "cancelled", {"reason": "kill-switch"})
                return self._view(pid)
            busy = self.db.execute("SELECT id FROM proposals WHERE target=? AND state='running' AND id!=?", (row["target"], pid)).fetchone()
            if busy is not None:
                self._move(pid, "cancelled", "cancelled", {"reason": f"proposal {busy['id']} is already running on {row['target']}"})
                return self._view(pid)
            self._move(pid, "running", "started", {}, started_at=self.now())
        action_id, clean = row["action_id"], json.loads(row["params_json"])
        # re-validate: the registry may have changed between propose and approve
        problems = self.reg.guard(action_id, self.reg.validate_params(action_id, clean)[0])
        if problems:
            self._fail(pid, "failed", "guard refused at execution time", {"problems": problems})
            return self._view(pid)
        a = self.reg.get(action_id)
        steps: list[dict] = []
        pol = self._auto_policy(row)
        if pol is not None and pol.precheck in ("unit-not-active", "helmrelease-stalled"):
            verdict, why = self._precheck(pid, pol, clean, steps)  # the Toolbelt's OWN read of reality, never the model's say-so
            if verdict != "go":
                return self._end_precheck(pid, verdict, why, steps)
        prior = a.get("guard", {}).get("requires_prior")
        if prior:
            pa = self.reg.get(prior)
            pclean = {k: v for k, v in clean.items() if k in pa.get("extra_vars", {})}
            status, res, tail = self._run_task(pid, prior, pclean, f"prior:{prior}")
            steps.append({"step": f"prior:{prior}", "status": status, "result": res})
            if status != "success" or not res.get("ok"):
                self._fail(pid, "failed", f"the required {prior} step did not pass", {"steps": steps, "tail": tail})
                return self._view(pid)
            if pol is not None and pol.precheck == "drift-present":  # the diff-scope gate: a small, non-empty dry-run diff only
                verdict, why = autonomy.Autonomy.drift_verdict(pol, res.get("changed"))
                if verdict != "go":
                    return self._end_precheck(pid, verdict, why, steps)
        status, res, tail = self._run_task(pid, action_id, clean, "action")
        steps.append({"step": "action", "status": status, "result": res})
        if status != "success":
            self._fail(pid, "failed", f"Semaphore task ended {status}", {"steps": steps, "tail": tail})
            return self._view(pid)
        v = a["verify"]
        if v.get("self"):
            mism = evaluate(v["expect"], res)
        else:
            va = self.reg.get(v["action"])
            vclean = {k: _subst(x, clean) for k, x in v.get("vars", {}).items()}
            vstatus, vres, vtail = self._run_task(pid, v["action"], vclean, f"verify:{v['action']}")
            steps.append({"step": f"verify:{v['action']}", "status": vstatus, "result": vres})
            mism = evaluate(v["expect"], vres) if vstatus == "success" else [f"the verify task ended {vstatus}"]
        if mism:
            self._fail(pid, "verify_failed", "the post-condition did not hold: " + "; ".join(mism), {"steps": steps, "rollback": a.get("rollback", "")})
            return self._view(pid)
        with self.lock:
            self._move(pid, "succeeded", "succeeded", {"steps": [s["step"] for s in steps]}, finished_at=self.now(),
                       result_json=json.dumps({"steps": steps}, sort_keys=True))
        self.audit("proposal_succeeded", proposal=pid, action=action_id)
        return self._view(pid)

    # -- autonomy (10f) -------------------------------------------------------------------------------------
    def _auto_policy(self, row) -> "autonomy.Policy | None":
        by = row["decided_by"] or ""
        return self.reg.autonomy.policies.get(by[5:]) if by.startswith("auto:") and self.reg.autonomy else None

    def _auto_log(self, pid: int, policy: str, outcome: str, reason: str = "") -> None:
        with self.lock:
            self.db.execute("INSERT INTO auto_log(ts, proposal_id, policy, outcome, reason) VALUES (?, ?, ?, ?, ?)",
                            (self.now(), pid, policy, outcome, reason[:120]))

    def _auto_counts(self, target: str, policy: str, now: int) -> tuple[int, int]:
        """Autonomous runs on this target in the last hour, and of this policy in the last day (runs that started)."""
        q = ",".join("?" * len(AUTO_STATES))
        with self.lock:
            per_target = self.db.execute(
                f"SELECT COUNT(*) FROM proposals WHERE decided_by LIKE 'auto:%' AND target=? AND decided_at>? AND state IN ({q})",
                (target, now - 3600, *AUTO_STATES)).fetchone()[0]
            per_policy = self.db.execute(
                f"SELECT COUNT(*) FROM proposals WHERE decided_by=? AND decided_at>? AND state IN ({q})",
                ("auto:" + policy, now - 86400, *AUTO_STATES)).fetchone()[0]
        return per_target, per_policy

    def consider_auto(self, pid: int, diag: dict) -> dict:
        """May this fresh proposal run itself? Returns {"auto": bool, "reason"|"policy": ...}. Every refusal is recorded
        (auto_log + audit) so `/aiops report` can say why nothing ran. The proposal then simply waits for a human, as in 10e."""
        au = self.reg.autonomy
        if au is None:
            return {"auto": False, "reason": "no-autonomy-section"}
        p = self._view(pid)
        if p["state"] != "pending":
            return {"auto": False, "reason": "proposal-not-pending"}
        policy = au.policy_for(p["action_id"])
        reason = au.static_block(policy, p, diag)
        with self.lock:  # flags, limits and the approval are one atomic decision
            if reason is None:
                if not self.flag("autonomy"):
                    reason = "autonomy-off"
                elif self.flag("kill_switch"):
                    reason = "kill-switch"
                elif self.flag("maintenance"):
                    reason = "maintenance"
                elif self.flag("autonomy_breaker"):
                    reason = "breaker-open"
                else:
                    per_target, per_policy = self._auto_counts(p["target"], policy.name, self.now())
                    if per_target >= au.limits.per_target_per_hour:
                        reason = "target-rate-limit"
                    elif per_policy >= au.limits.per_policy_per_day:
                        reason = "policy-daily-limit"
            if reason is None and self._row(pid)["state"] != "pending":
                reason = "proposal-not-pending"
            if reason is None:
                self._move(pid, "approved", "approved", {"by": f"auto:{policy.name}", "ref": "policy"}, decided_at=self.now(),
                           decided_by=f"auto:{policy.name}", decision_ref="policy")
        if reason is not None:
            self._auto_log(pid, policy.name if policy else "", "skipped", reason)
            self.audit("auto_skipped", proposal=pid, reason=reason, action=p["action_id"], target=p["target"])
            return {"auto": False, "reason": reason}
        self._auto_log(pid, policy.name, "approved")
        self.audit("proposal_auto_approved", proposal=pid, policy=policy.name, action=p["action_id"], target=p["target"])
        threading.Thread(target=self.execute, args=(pid,), daemon=True, name=f"auto-{pid}").start()
        return {"auto": True, "policy": policy.name}

    def _precheck(self, pid: int, pol: "autonomy.Policy", clean: dict, steps: list) -> tuple[str, str]:
        """The Toolbelt's own look at the world before it acts on its own. ('go'|'skip'|'stop', why)."""
        if pol.precheck == "unit-not-active":
            status, res, _ = self._run_task(pid, "service-status", {"target_host": clean["target_host"], "unit": clean["unit"]}, "precheck:service-status")
            steps.append({"step": "precheck:service-status", "status": status, "result": res})
            if status != "success":
                return "stop", f"the precheck read ended {status}: not acting without it"
            return autonomy.Autonomy.unit_verdict(res.get("active_state"))
        if pol.precheck == "helmrelease-stalled":
            if self.cfg.reader is None:
                return "stop", "no read-only Kubernetes access is configured for the precheck"
            try:
                out = self.cfg.reader("kube.get", {"kind": "helmreleases", "namespace": clean["hr_namespace"], "name": clean["hr_name"]})
            except Exception as e:  # noqa: BLE001 - an unreadable cluster is "do not act", never "act anyway"
                return "stop", f"the Kubernetes read failed ({type(e).__name__}): not acting without it"
            steps.append({"step": "precheck:kube.get", "status": "success", "result": {"conditions": out.get("conditions")}})
            return autonomy.Autonomy.helmrelease_verdict(out.get("conditions"))
        return "stop", f"unknown precheck {pol.precheck!r}"

    def _end_precheck(self, pid: int, verdict: str, why: str, steps: list) -> dict:
        """The precheck said do not run: `skip` = the world already healed (no action needed); `stop` = inconclusive or out of scope."""
        result = json.dumps({"why": why, "steps": steps}, sort_keys=True)
        with self.lock:
            if verdict == "skip":
                self._move(pid, "skipped", "skipped", {"why": why}, finished_at=self.now(), result_json=result)
            else:
                self._move(pid, "cancelled", "cancelled", {"reason": why}, finished_at=self.now(), result_json=result)
        self.audit("proposal_" + ("skipped" if verdict == "skip" else "cancelled"), proposal=pid, why=why[:120])
        return self._view(pid)

    def _finish_auto(self, pid: int) -> None:
        """After an autonomous run ends: log it, and trip the circuit breaker if too many recent ones failed or did not verify."""
        row = self._row(pid)
        by = row["decided_by"] or ""
        if not by.startswith("auto:") or row["state"] not in TERMINAL:
            return
        self._auto_log(pid, by[5:], row["state"])
        au = self.reg.autonomy
        if au is None or row["state"] not in ("failed", "verify_failed"):
            return
        with self.lock:
            n = self.db.execute("SELECT COUNT(*) FROM proposals WHERE decided_by LIKE 'auto:%' AND state IN ('failed','verify_failed') AND finished_at>?",
                                (self.now() - au.limits.breaker_window_seconds,)).fetchone()[0]
            tripped = n >= au.limits.breaker_failures and not self.flag("autonomy_breaker")
        if tripped:
            self.set_flag("autonomy_breaker", True, by="system", reason=f"{n} autonomous failures within {au.limits.breaker_window_seconds}s", system=True)
            with self.lock:
                self._event(pid, "breaker_tripped", {"failures": n, "window_seconds": au.limits.breaker_window_seconds})
            self.audit("breaker_tripped", proposal=pid, failures=n)

    def report(self, days: int = 14) -> dict:
        """What autonomy did over the last `days`: by policy/outcome/target, why proposals were NOT run, breaker trips, flapping."""
        days = max(1, min(int(days), 90))
        since = self.now() - days * 86400
        with self.lock:
            rows = self.db.execute("SELECT id, action_id, target, state, decided_by, decided_at FROM proposals WHERE decided_by LIKE 'auto:%' AND decided_at>=? "
                                   "ORDER BY decided_at", (since,)).fetchall()
            skipped = self.db.execute("SELECT reason, COUNT(*) n FROM auto_log WHERE outcome='skipped' AND ts>=? GROUP BY reason", (since,)).fetchall()
            trips = self.db.execute("SELECT COUNT(*) FROM proposal_events WHERE kind='breaker_tripped' AND ts>=?", (since,)).fetchone()[0]
        by_policy: dict = {}
        times: dict = {}
        for r in rows:
            pol = (r["decided_by"] or "")[5:]
            by_policy.setdefault(pol, {}).setdefault(r["state"], 0)
            by_policy[pol][r["state"]] += 1
            times.setdefault(r["target"], []).append(r["decided_at"])
        flapping = sorted(t for t, ts in times.items() if any(ts[i + 2] - ts[i] <= 6 * 3600 for i in range(len(ts) - 2)))
        return {"days": days, "autonomous_runs": len(rows), "by_policy": by_policy,
                "by_target": {t: len(ts) for t, ts in sorted(times.items())},
                "skipped_reasons": {r["reason"]: r["n"] for r in skipped}, "breaker_trips": trips,
                "flapping_targets": flapping, "flags": {k: v["value"] for k, v in self.flags().items()}}

    # -- views ---------------------------------------------------------------------------------------------
    def _view(self, pid: int, duplicate: bool = False) -> dict:
        with self.lock:
            r = self._row(pid)
            # What a human reads in chat: 1, 2, 3 within the conversation (or incident) the proposal belongs to.
            # The primary key stays the identity everything else (buttons, audit, API) uses.
            col, val = (("conversation_id", r["conversation_id"]) if r["conversation_id"] is not None
                        else ("incident_id", r["incident_id"]) if r["incident_id"] is not None else (None, None))
            number = (self.db.execute(f"SELECT COUNT(*) FROM proposals WHERE {col}=? AND id<=?", (val, pid)).fetchone()[0]
                      if col else r["id"])
        a = self.reg.get(r["action_id"])
        out = {
            "id": r["id"], "number": number, "state": r["state"], "action_id": r["action_id"], "tier": r["tier"], "target": r["target"],
            "params": json.loads(r["params_json"]), "params_hash": r["params_hash"], "reason": r["reason"],
            "incident_id": r["incident_id"], "conversation_id": r["conversation_id"], "thread_id": r["thread_id"],
            "source": r["source"], "replay": bool(r["replay"]), "created_at": r["created_at"], "expires_at": r["expires_at"],
            "decided_by": r["decided_by"], "message_ref": r["message_ref"], "description": a["description"],
            "rollback": a.get("rollback", ""), "verify": a["verify"]["expect"],
            "requires_prior": a.get("guard", {}).get("requires_prior"),
            "result": json.loads(r["result_json"]) if r["result_json"] else None,
        }
        if duplicate:
            out["duplicate"] = True
        return out

    def get(self, pid: int) -> dict:
        self.sweep()
        return self._view(pid)

    def bind_thread(self, incident_id: int, thread_id: str) -> int:
        """The incident's Discord thread now exists: tell the bot where each still-pending proposal's card belongs."""
        n = 0
        with self.lock:
            for r in self.db.execute("SELECT id FROM proposals WHERE incident_id=? AND (state='pending' OR decided_by LIKE 'auto:%') AND replay=0 "
                                     "AND (thread_id IS NULL OR thread_id!=?)", (incident_id, thread_id)).fetchall():
                self.db.execute("UPDATE proposals SET thread_id=? WHERE id=?", (thread_id, r["id"]))
                self._event(r["id"], "thread_bound", {"thread_id": thread_id})
                n += 1
        return n

    def set_message(self, pid: int, message_ref: str) -> dict:
        with self.lock:
            self._row(pid)
            self.db.execute("UPDATE proposals SET message_ref=? WHERE id=?", (message_ref[:80], pid))
            self._event(pid, "message_set", {"ref": message_ref[:80]})
        return self._view(pid)

    def feed(self, after: int = 0, limit: int = 50) -> dict:
        """State changes for the bot to render: events after a cursor, each with the proposal as it is now."""
        self.sweep()
        with self.lock:
            rows = self.db.execute("SELECT * FROM proposal_events WHERE id>? ORDER BY id LIMIT ?", (int(after), min(int(limit), 200))).fetchall()
        events = [{"id": r["id"], "kind": r["kind"], "ts": r["ts"], "data": json.loads(r["data_json"]), "proposal": self._view(r["proposal_id"])}
                  for r in rows]
        return {"events": events, "next": events[-1]["id"] if events else int(after)}

    def list(self, states: tuple = ("pending", "approved", "running")) -> list[dict]:
        self.sweep()
        q = ",".join("?" * len(states))
        with self.lock:
            ids = [r["id"] for r in self.db.execute(f"SELECT id FROM proposals WHERE state IN ({q}) ORDER BY id", states).fetchall()]
        return [self._view(i) for i in ids]

    def summary(self) -> dict:
        self.sweep()
        with self.lock:
            counts = {r["state"]: r["n"] for r in self.db.execute("SELECT state, COUNT(*) n FROM proposals GROUP BY state").fetchall()}
        au = self.reg.autonomy
        return {"proposals": counts, "flags": {k: v["value"] for k, v in self.flags().items()},
                "proposals_today": self._counter("proposals"), "daily_proposal_cap": self.cfg.max_proposals_per_day,
                "autonomy": None if au is None else {
                    "hosts": sorted(au.hosts), "policies": {n: p.enabled for n, p in au.policies.items()},
                    "limits": {"per_target_per_hour": au.limits.per_target_per_hour, "per_policy_per_day": au.limits.per_policy_per_day,
                               "breaker_failures": au.limits.breaker_failures, "breaker_window_seconds": au.limits.breaker_window_seconds}}}
