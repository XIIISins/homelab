"""Rebuild loop execution support for the action engine (Phase 10g2).

Plan: docs/operations/10g-rebuild-loop.md. `actions.Engine` stays small: an action that declares `steps:` in the registry
(aiops/actions.yml) is handed to this module, which implements the multi-step flow

    plan (runner) -> apply (runner, bound to the plan) -> converge (Semaphore) -> verify (checklist)

with a `backend` per step: `runner` (the Frigg rebuild runner, JSON over a unix socket) or `semaphore` (the existing 10e
executor path, the default). Everything that touches the world is injectable, so every rule is unit-tested with fakes:

  RunnerClient(transport)    the runner protocol; FakeRunner in the tests is just a transport function
  FactsProvider              the Toolbelt's OWN read of reality for eligibility (PVE guest and node status, reach.tcp
                             probes, Zabbix agent silence); ReaderFacts is the real one over the read-tool allow-list
  VerifyProvider             the class post-conditions; SemaphoreVerify runs the `rebuild-verify` template, and
                             run_checklist (pure) turns its per-condition answers into a verdict

State lives in two small tables next to the proposals (rebuild_runs, rebuild_probes). A run records its plan id, the plan's
expiry and which step it reached, so a Toolbelt restart in the middle RESUMES from state (it asks the runner what happened,
re-attaches to the Semaphore task it started) and never re-sends an apply blindly.

A diagnosis only ever supplies a proposal. Whether a rebuild is eligible (and, for an unattended one, whether the
`guest-dead` / `guest-broken` precheck holds) is decided here from facts read by the Toolbelt, never from what the model said.
"""
from __future__ import annotations

import json
import re
import socket
import uuid
from typing import Callable, Protocol

import rebuild as rb

PROTOCOL_V = 1
DEFAULT_SOCKET = "/run/aiops-rebuild/runner.sock"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_SAFE = re.compile(r"[^A-Za-z0-9._:/ -]")
PRE_APPLY_CODES = ("plan-unknown", "plan-expired", "origin-moved", "denied", "busy")  # the runner refused: nothing was changed
# which guest kinds each rebuild-scope action may touch (the registry guard enforces it before anything runs)
ACTION_KINDS = {"rebuild-guest": ("lxc", "droplet"), "rebuild-worker": ("k8s-worker",), "start-guest": ("lxc",)}
IN_FLIGHT = ("approved", "running")

SCHEMA = """
CREATE TABLE IF NOT EXISTS rebuild_runs (
  proposal_id INTEGER PRIMARY KEY,
  target TEXT NOT NULL,
  guest_class TEXT NOT NULL,
  mode TEXT NOT NULL,
  plan_id TEXT NOT NULL UNIQUE,
  address TEXT,
  plan_json TEXT NOT NULL DEFAULT '{}',
  origin_main TEXT,
  plan_created_at INTEGER NOT NULL,
  plan_expires_at INTEGER NOT NULL,
  stage TEXT NOT NULL,
  done_json TEXT NOT NULL DEFAULT '{}',
  task_id INTEGER,
  apply_started_at INTEGER,
  started_at INTEGER,
  finished_at INTEGER,
  outcome TEXT,
  backup_age_hours REAL,
  manifest_text TEXT
);
CREATE INDEX IF NOT EXISTS rebuild_runs_target ON rebuild_runs(target, apply_started_at);
CREATE TABLE IF NOT EXISTS rebuild_probes (
  target TEXT NOT NULL, ts INTEGER NOT NULL, ok INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS rebuild_probes_t ON rebuild_probes(target, ts);
"""


def _eng():
    import actions  # lazy: actions imports this module, so the names are only needed at call time

    return actions


# ---- the runner client ------------------------------------------------------------------------------------------

class RunnerError(Exception):
    """The runner refused, failed or could not be reached. `code` is one of the protocol codes or a client-side one
    (runner-unreachable, bad-response, plan-rejected)."""

    def __init__(self, code: str, message: str = "", problems: list | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.problems = code, message, list(problems or [])


def unix_transport(path: str = DEFAULT_SOCKET) -> Callable[[dict, float], dict]:
    """One request line out, one response line back, over the runner's unix stream socket."""

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
                    raise RunnerError("bad-response", "the response is too large")
        except OSError as e:
            raise RunnerError("runner-unreachable", type(e).__name__)
        finally:
            s.close()
        try:
            d = json.loads(buf.split(b"\n", 1)[0])
        except ValueError:
            raise RunnerError("bad-response", "not JSON")
        return d

    return call


class RunnerClient:
    """The rebuild runner protocol (v1). The runner never receives free-form commands: only class, target and plan_id."""

    def __init__(self, socket_path: str = DEFAULT_SOCKET, transport: Callable[[dict, float], dict] | None = None,
                 plan_timeout: float = 180.0, apply_timeout: float = 1200.0, status_timeout: float = 10.0):
        self.transport = transport or unix_transport(socket_path)
        self.plan_timeout, self.apply_timeout, self.status_timeout = plan_timeout, apply_timeout, status_timeout

    def _call(self, op: str, payload: dict, timeout: float, with_id: bool = True) -> dict:
        req = {"v": PROTOCOL_V, "op": op, **payload}
        if with_id:
            req["request_id"] = str(uuid.uuid4())
        resp = self.transport(req, timeout)
        if not isinstance(resp, dict) or not isinstance(resp.get("ok"), bool):
            raise RunnerError("bad-response", "no ok field")
        if with_id and resp.get("request_id") not in (None, req["request_id"]):
            raise RunnerError("bad-response", "request_id mismatch")
        if not resp["ok"]:
            err = str(resp.get("error") or "")
            code, _, text = err.partition(":")
            if not _:
                code, text = (err.strip() or ("plan-rejected" if resp.get("problems") else "runner-refused")), ""
            raise RunnerError(code.strip()[:40], text.strip()[:200], resp.get("problems") if isinstance(resp.get("problems"), list) else [])
        return resp

    def plan(self, cls: str, target: str) -> dict:
        return self._call("plan", {"class": cls, "target": target}, self.plan_timeout)

    def apply(self, plan_id: str) -> dict:
        return self._call("apply", {"plan_id": plan_id}, self.apply_timeout)

    def status(self) -> dict:
        return self._call("status", {}, self.status_timeout, with_id=False)


def validate_plan(resp: dict, cls_name: str, target: str, policy: rb.RebuildPolicy, now: int) -> list[str]:
    """The Toolbelt re-checks the runner's answer against the registry (defence in depth; the runner checked it too)."""
    p: list[str] = []
    if not isinstance(resp.get("plan_id"), str) or not _HEX64.fullmatch(resp["plan_id"]):
        p.append("plan_id is not a sha256 hex digest")
    s = resp.get("summary")
    if not isinstance(s, dict):
        return p + ["the plan has no summary"]
    if s.get("action") not in ("replace", "create"):
        p.append(f"plan action {_t(s.get('action'))} is not replace or create")
    if s.get("changes") != 1:
        p.append(f"the plan has {_t(s.get('changes'))} changes, expected exactly 1")
    ident = s.get("identity") if isinstance(s.get("identity"), dict) else {}
    cls = policy.class_of(target)
    want = cls.hosts.get(target) if cls else None
    if cls is None or cls.name != cls_name or want is None:
        p.append("target is not in the class the plan names")
    else:
        if ident.get("name") != target:
            p.append(f"plan identity name {_t(ident.get('name'))} is not {target}")
        if ident.get("vmid") != want["vmid"]:
            p.append(f"plan identity vmid {_t(ident.get('vmid'))} is not {want['vmid']}")
        if ident.get("node") != want["node"]:
            p.append(f"plan identity node {_t(ident.get('node'))} is not {want['node']}")
    ea = resp.get("expires_at")
    if not isinstance(ea, int) or isinstance(ea, bool) or ea <= now:
        p.append("the plan carries no future expiry")
    if not isinstance(resp.get("origin_main"), str) or not resp["origin_main"]:
        p.append("the plan does not name the origin/main commit it was made from")
    if resp.get("problems"):
        p.append("the runner reports problems with the plan")
    return p


def _t(v: object, n: int = 40) -> str:
    return _SAFE.sub("?", str(v))[:n]


# ---- facts + verification providers ---------------------------------------------------------------------------

class FactsProvider(Protocol):
    def facts(self, target: str, cls: rb.GuestClass) -> dict:
        """Reality as the Toolbelt reads it. Keys (see rebuild.py): guest_state, node_online, node_guests_ok, probe_ok
        (one fresh reach.tcp result, bool or None), agent_silent, last_backup_age_hours, peers_healthy, is_leader,
        drift_changed, replay_converges, state_bearing, quorum_member, agent_host."""

    def worker_manifest(self, target: str) -> dict | None:
        """rebuild.worker_data_manifest(...) for a worker, or None when it cannot be read."""


class ReaderFacts:
    """The real FactsProvider: the read-only Toolbelt tools (the same allow-list the agent is held to). Deliberately
    conservative: anything unreadable is None/unknown, which the eligibility rules treat as 'do not act'."""

    def __init__(self, reader: Callable[[str, dict], dict]):
        self.reader = reader

    def _try(self, name: str, args: dict) -> dict | None:
        try:
            return self.reader(name, args)
        except Exception:  # noqa: BLE001 - an unreadable source is "unknown", never "healthy"
            return None

    def facts(self, target: str, cls: rb.GuestClass) -> dict:
        info = cls.hosts[target]
        out: dict = {"guest_state": "unknown", "node_online": False, "node_guests_ok": False, "probe_ok": None, "agent_silent": None,
                     "last_backup_age_hours": None,  # no PBS read tool yet: a class that needs a backup stops on backup-stale
                     "peers_healthy": None, "is_leader": bool(cls.leader_aware),  # unknown master is treated as the master
                     "drift_changed": None, "replay_converges": None}
        ns = self._try("pve.node_status", {"node": info["node"]})
        if not ns or (ns.get("cluster_view") or {}).get("status") != "online":
            return out
        out["node_online"] = True
        gs = self._try("pve.guests", {"node": info["node"]})
        if gs is None:
            return out
        guests = [g for g in gs.get("guests", []) if isinstance(g, dict)]
        mine = next((g for g in guests if g.get("vmid") == info["vmid"]), None)
        out["guest_state"] = ("missing" if mine is None else
                              {"running": "running", "stopped": "stopped"}.get(str(mine.get("status")), "unknown"))
        others = [g for g in guests if g.get("vmid") != info["vmid"] and not g.get("template")]
        out["node_guests_ok"] = (not others) or any(g.get("status") == "running" for g in others)  # a coarse proxy
        if out["guest_state"] == "running":
            r = self._try("reach.tcp", {"host": target, "port": 22})
            out["probe_ok"] = bool(r.get("open")) if r is not None else None
            z = self._try("zabbix.host", {"host": target})
            if z is not None:
                ifaces = (z.get("host") or {}).get("interfaces") or []
                out["agent_silent"] = not any(str(i.get("type")) == "1" and str(i.get("available")) == "1" for i in ifaces)
        if cls.neighbours:
            probes = [self._try("reach.tcp", {"host": n, "port": 53}) for n in cls.neighbours if n != target]
            out["peers_healthy"] = all(p is not None and p.get("open") is True for p in probes)
        return out

    def worker_manifest(self, target: str) -> dict | None:
        return None  # kubectl get pv is not on the read-tool allow-list yet: a worker rebuild stays blocked


def run_checklist(conditions, results: object) -> dict:
    """Pure: every class post-condition must be present and true. An absent answer is a failure, never a pass, and an
    empty condition list is a failure (nothing was checked)."""
    res = results if isinstance(results, dict) else {}

    def yes(v: object) -> bool:
        return v is True or (isinstance(v, str) and v.strip().lower() == "true")

    conds = list(conditions)
    missing = [c for c in conds if c not in res]
    failed = [c for c in conds if not yes(res.get(c))]
    return {"ok": bool(conds) and not failed, "checked": len(conds), "failed": failed, "missing": missing}


class VerifyProvider(Protocol):
    def check(self, pid: int, target: str, class_name: str, conditions: tuple) -> dict:
        """{"status": "success"|..., "checks": {condition: bool}}"""


def checks_from_result(res: dict, conditions: tuple) -> dict:
    """Map the verify playbook's flat AIOPS_RESULT fields (`ssh_as_ansible`, `vlagent_active`, ...) onto the registry's
    hyphenated condition names. An explicit `checks` dict wins; `agents-active` is the AND of the two agent-unit fields.
    A condition the playbook did not report is simply absent, so the checklist counts it as missing (never as passed)."""
    if isinstance(res.get("checks"), dict):
        return dict(res["checks"])

    def yes(v: object) -> bool:
        return v is True or (isinstance(v, str) and v.strip().lower() == "true")

    out: dict = {}
    for c in conditions:
        key = c.replace("-", "_")
        if key in res:
            out[c] = res[key]
        elif c == "agents-active" and "vlagent_active" in res and "zabbix_agent2_active" in res:
            out[c] = yes(res["vlagent_active"]) and yes(res["zabbix_agent2_active"])
    return out


class SemaphoreVerify:
    """The real VerifyProvider: the registry's `rebuild-verify` template, which reports `checks` in its AIOPS_RESULT."""

    def __init__(self, engine):
        self.eng = engine

    def check(self, pid: int, target: str, class_name: str, conditions: tuple) -> dict:
        status, res, _tail = self.eng._run_task(pid, "rebuild-verify", {"target": target}, "verify")
        return {"status": status, "checks": checks_from_result(res, conditions)}


# ---- state helpers ----------------------------------------------------------------------------------------------

def get_run(eng, pid: int):
    with eng.lock:
        return eng.db.execute("SELECT * FROM rebuild_runs WHERE proposal_id=?", (pid,)).fetchone()


def _set(eng, pid: int, **cols) -> None:
    with eng.lock:
        eng.db.execute(f"UPDATE rebuild_runs SET {', '.join(f'{c}=?' for c in cols)} WHERE proposal_id=?", (*cols.values(), pid))


def _stage(eng, pid: int, stage: str, **cols) -> None:
    with eng.lock:
        _set(eng, pid, stage=stage, **cols)
        eng._event(pid, "rebuild_stage", {"stage": stage})


def active(eng) -> list[dict]:
    """Rebuilds in flight now (state `rebuilding` in /aiops status): approved or running with a non-terminal run."""
    with eng.lock:
        rows = eng.db.execute(
            "SELECT r.proposal_id, r.target, r.stage, r.started_at, r.guest_class FROM rebuild_runs r JOIN proposals p ON p.id=r.proposal_id "
            "WHERE p.state IN ('approved','running') ORDER BY r.proposal_id").fetchall()
    return [{"proposal": r["proposal_id"], "target": r["target"], "class": r["guest_class"], "stage": r["stage"], "since": r["started_at"]} for r in rows]


def describe(eng, pid: int) -> dict | None:
    """What a human approving or reading sees (the card data): from the stored plan, never from a model."""
    r = get_run(eng, pid)
    if r is None:
        return None
    plan = json.loads(r["plan_json"])
    s = plan.get("summary") or {}
    ident = s.get("identity") or {}
    name, vmid, node, ip = (_t(ident.get(k), 40) for k in ("name", "vmid", "node", "ip"))
    if s.get("action") == "create":
        destroys = f"Nothing is destroyed: {name} (vmid {vmid} on {node}) is missing and is created from the repo, then converged."
    else:
        destroys = (f"DESTROYS the existing {name} (vmid {vmid} on {node}, {ip}) and recreates it empty from the repo; "
                    "everything on its disk is lost, then Ansible converges it.")
    return {"class": r["guest_class"], "stage": r["stage"], "mode": r["mode"], "plan_id": r["plan_id"], "plan_action": _t(s.get("action")),
            "plan_changes": s.get("changes"), "identity": {"name": name, "vmid": vmid, "node": node, "ip": ip},
            "origin_main": _t(r["origin_main"], 12), "plan_expires_at": r["plan_expires_at"], "destroys": destroys,
            "backup_age_hours": r["backup_age_hours"], "manifest": r["manifest_text"], "done": sorted(json.loads(r["done_json"]))}


# ---- gathering facts + eligibility ----------------------------------------------------------------------------

def _facts_provider(eng) -> FactsProvider:
    if eng.cfg.facts is None:
        raise _eng().Refused(501, "the rebuild loop has no facts source configured (needs the read tools)")
    return eng.cfg.facts


def history(eng, target: str, cls: rb.GuestClass, exclude_pid: int | None = None) -> dict:
    now = eng.now()
    ex = -1 if exclude_pid is None else exclude_pid
    with eng.lock:
        def n(where: str, args: tuple) -> int:
            return eng.db.execute(f"SELECT COUNT(*) FROM rebuild_runs WHERE apply_started_at>? AND proposal_id!=? AND {where}",
                                  (now - 86400, ex, *args)).fetchone()[0]

        peers = [h for h in cls.hosts if h != target] + [x for x in cls.neighbours if x != target]
        q = ",".join("?" * len(peers)) or "''"
        h = {"target_day": n("target=?", (target,)),
             "class_day": n("guest_class=?", (cls.name,)), "fleet_day": n("1=1", ()),
             "peer_rebuilt_24h": bool(peers) and n(f"target IN ({q})", tuple(peers)) > 0,
             "breaker_open": eng.flag("autonomy_rebuild_breaker"),
             "clean_rebuilds": eng.db.execute("SELECT COUNT(*) FROM rebuild_runs WHERE target=? AND mode='approval' AND outcome='succeeded'",
                                              (target,)).fetchone()[0]}
        h["target_week"] = eng.db.execute("SELECT COUNT(*) FROM rebuild_runs WHERE apply_started_at>? AND proposal_id!=? AND target=?",
                                          (now - 7 * 86400, ex, target)).fetchone()[0]
    return h


def gather(eng, target: str, mode: str, exclude_pid: int | None = None) -> dict:
    """The full eligibility facts: the provider's reading of the world plus what the Toolbelt knows itself (its own
    proposals, runs, flags). Raises Refused(502) when the provider cannot read."""
    pol = eng.reg.rebuild
    cls = pol.class_of(target) if pol else None
    if cls is None:
        return {"target": target, "mode": mode}
    try:
        raw = dict(_facts_provider(eng).facts(target, cls))
    except _eng().Refused:
        raise
    except Exception as e:  # noqa: BLE001 - a failed read is "do not act"
        raise _eng().Refused(502, f"could not read the guest's state ({type(e).__name__})")
    now = eng.now()
    with eng.lock:
        if raw.get("probe_ok") is not None:
            eng.db.execute("INSERT INTO rebuild_probes(target, ts, ok) VALUES (?, ?, ?)", (target, now, int(bool(raw["probe_ok"]))))
        eng.db.execute("DELETE FROM rebuild_probes WHERE ts<?", (now - 6 * 3600,))
        probes = [{"t": r["ts"], "ok": bool(r["ok"])} for r in
                  eng.db.execute("SELECT ts, ok FROM rebuild_probes WHERE target=? AND ts>=? ORDER BY ts", (target, now - 6 * 3600)).fetchall()]
        starts = eng.db.execute("SELECT state FROM proposals WHERE action_id='start-guest' AND target=? AND replay=0 AND created_at>? "
                                "AND state IN ('succeeded','failed','verify_failed') ORDER BY id DESC LIMIT 1", (target, now - 2 * 3600)).fetchone()
        restart_tripped = eng.db.execute(
            "SELECT 1 FROM proposal_events e JOIN proposals p ON p.id=e.proposal_id WHERE e.kind='breaker_tripped' AND e.ts>? AND p.target=? "
            "AND p.action_id NOT IN ('rebuild-guest','rebuild-worker') LIMIT 1", (now - 86400, target)).fetchone()
        inflight = eng.db.execute("SELECT COUNT(*) FROM rebuild_runs r JOIN proposals p ON p.id=r.proposal_id WHERE p.state IN ('approved','running') "
                                  "AND r.proposal_id!=?", (-1 if exclude_pid is None else exclude_pid,)).fetchone()[0]
    raw.pop("probe_ok", None)
    raw.update({"target": target, "mode": mode, "probes": probes, "inflight": inflight, "start_attempted": starts is not None,
                "start_failed": starts is not None and starts["state"] != "succeeded", "restart_breaker_tripped": restart_tripped is not None,
                "flags": {k: eng.flag(k) for k in ("maintenance", "kill_switch", "autonomy_rebuild")},
                "history": history(eng, target, cls, exclude_pid)})
    return raw


def check_eligible(eng, target: str, mode: str, exclude_pid: int | None = None) -> tuple[rb.Verdict, dict]:
    facts = gather(eng, target, mode, exclude_pid)
    return rb.eligible(facts, eng.reg.rebuild), facts


# ---- propose: eligibility + a plan bound to the proposal -------------------------------------------------------

def _recorded_plan(eng, target: str, plan_hash: str) -> dict | None:
    """A plan a succeeded `rebuild-plan` proposal produced for this target (the operator's plan-then-rebuild path)."""
    with eng.lock:
        rows = eng.db.execute("SELECT result_json, finished_at FROM proposals WHERE action_id='rebuild-plan' AND state='succeeded' AND target=? "
                              "ORDER BY id DESC LIMIT 20", (target,)).fetchall()
    for r in rows:
        try:
            for s in json.loads(r["result_json"]).get("steps", []):
                res = s.get("result") if isinstance(s, dict) else None
                if isinstance(res, dict) and res.get("plan_id") == plan_hash and res.get("plan_ok") is True:
                    return {**res, "created_at": r["finished_at"] or eng.now()}
        except ValueError:
            continue
    return None


def prepare(eng, action_id: str, clean: dict, mode: str = "approval") -> dict | None:
    """Called by Engine.propose for an action with `steps`. For rebuild-guest/worker: eligibility from reality, then a plan
    from the runner (or a recorded one the caller named), bound to the proposal. Returns None for plain actions."""
    a = eng.reg.get(action_id)
    if not a.get("steps") or action_id not in ("rebuild-guest", "rebuild-worker"):
        return None
    E = _eng()
    pol, target = eng.reg.rebuild, clean["target"]
    cls = pol.class_of(target) if pol else None
    if cls is None:
        raise E.Refused(422, "proposal failed validation", {"problems": [f"{target!r} is not in a rebuild class"]})
    with eng.lock:
        dup = eng.db.execute("SELECT id FROM proposals WHERE action_id IN ('rebuild-guest','rebuild-worker') AND target=? AND replay=0 "
                             "AND state IN ('pending','approved','running')", (target,)).fetchone()
    if dup is not None:
        return {"duplicate": dup["id"]}
    verdict, facts = check_eligible(eng, target, mode)
    if verdict.verdict != "go":
        eng.audit("rebuild_not_eligible", target=target, verdict=verdict.verdict, reason=verdict.reason)
        raise E.Refused(409 if verdict.verdict == "skip" else 422, f"{target} is not eligible for a rebuild now: {verdict.reason}",
                        {"problems": [f"{verdict.verdict}: {verdict.reason}"], "eligibility": verdict.as_dict()})
    manifest = None
    if cls.kind == "k8s-worker":
        try:
            manifest = _facts_provider(eng).worker_manifest(target)
        except E.Refused:
            raise
        except Exception:  # noqa: BLE001
            manifest = None
        if not isinstance(manifest, dict):
            raise E.Refused(422, "the worker data manifest could not be read: not proposing a worker rebuild without it")
        if manifest.get("blocks"):
            raise E.Refused(422, "the worker data manifest blocks this rebuild", {"problems": list(manifest.get("reasons", []))[:5]})
    now = eng.now()
    supplied = clean.get("plan_hash")
    if supplied:
        plan = _recorded_plan(eng, target, supplied)
        if plan is None:
            raise E.Refused(422, "proposal failed validation", {"problems": ["no recorded rebuild-plan for this target has that plan_hash"]})
    else:
        if eng.cfg.runner is None:
            raise E.Refused(501, "the rebuild runner is not configured")
        try:
            plan = {**eng.cfg.runner.plan(cls.name, target), "created_at": now}
        except RunnerError as e:
            raise E.Refused(502 if e.code in ("runner-unreachable", "bad-response") else 422, f"the runner did not produce a plan: {e.code}",
                            {"problems": (e.problems or [e.message])[:5]})
    problems = validate_plan(plan, cls.name, target, pol, now)
    if problems:
        raise E.Refused(422, "the plan failed the Toolbelt's own check", {"problems": problems[:5]})
    with eng.lock:
        if eng.db.execute("SELECT 1 FROM rebuild_runs WHERE plan_id=?", (plan["plan_id"],)).fetchone():
            raise E.Refused(409, "that plan was already used by an earlier rebuild: plan again")
    expires = min(int(plan["expires_at"]), int(plan["created_at"]) + pol.limits.plan_max_age_seconds)
    return {"clean": {**clean, "plan_hash": plan["plan_id"]}, "expires_at": expires, "mode": mode, "plan": plan, "class": cls.name,
            "verdict": verdict.as_dict(), "backup_age_hours": facts.get("last_backup_age_hours"),
            "manifest_text": manifest.get("summary") if isinstance(manifest, dict) else None}


def record_run(eng, pid: int, prep: dict, target: str) -> None:
    plan = prep["plan"]
    with eng.lock:
        eng.db.execute(
            "INSERT INTO rebuild_runs(proposal_id, target, guest_class, mode, plan_id, address, plan_json, origin_main, plan_created_at, "
            "plan_expires_at, stage, backup_age_hours, manifest_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)",
            (pid, target, prep["class"], prep["mode"], plan["plan_id"], str(plan.get("address", ""))[:200],
             json.dumps({"summary": plan.get("summary"), "problems": plan.get("problems", [])}, sort_keys=True),
             str(plan.get("origin_main", ""))[:64], int(plan["created_at"]), prep["expires_at"], prep.get("backup_age_hours"), prep.get("manifest_text")))
        eng._event(pid, "plan_bound", {"plan_id": plan["plan_id"], "expires_at": prep["expires_at"], "origin_main": str(plan.get("origin_main", ""))[:12]})


# ---- execution ----------------------------------------------------------------------------------------------

def busy_reason(eng, pid: int) -> str | None:
    """Queue length 1, fleet-wide: another rebuild already running blocks this one (caller holds the lock)."""
    other = eng.db.execute("SELECT r.proposal_id, r.target FROM rebuild_runs r JOIN proposals p ON p.id=r.proposal_id "
                           "WHERE p.state='running' AND r.proposal_id!=? LIMIT 1", (pid,)).fetchone()
    return f"proposal {other['proposal_id']} is already rebuilding {other['target']}: one rebuild at a time" if other else None


def _steps_view(done: dict) -> list[dict]:
    return [{"step": k, "status": v.get("status", "success"), "seconds": v.get("seconds")} for k, v in done.items()]


def _trip_breaker(eng, pid: int, why: str) -> None:
    if eng.flag("autonomy_rebuild_breaker"):
        return
    eng.set_flag("autonomy_rebuild_breaker", True, by="system", reason=why[:150], system=True)
    with eng.lock:
        eng._event(pid, "breaker_tripped", {"kind": "rebuild", "failures": 1, "why": why[:150]})
    eng.audit("breaker_tripped", proposal=pid, kind="rebuild", why=why[:120])


def _fail(eng, pid: int, state: str, why: str, done: dict, stage: str, a: dict, *, half_built: bool) -> None:
    """End the proposal. `half_built` = the apply was attempted, so something may exist that a human must look at: that (and
    only that) trips the rebuild breaker. A refusal before the apply changed nothing and costs no budget."""
    extra = {"steps": _steps_view(done), "stage": stage, "rollback": a.get("rollback", ""), "nothing_changed": not half_built}
    eng._fail(pid, state, why, extra)
    _set(eng, pid, outcome=state, finished_at=eng.now())
    if not half_built:
        _set(eng, pid, apply_started_at=None)  # a refused apply never counts against the rate limits
    else:
        _trip_breaker(eng, pid, f"rebuild of {get_run(eng, pid)['target']} ended {state} at {stage}: {why[:90]}")


def execute(eng, pid: int, row, clean: dict, a: dict, resume: bool = False) -> None:
    """Run (or resume) the registry's steps for an approved proposal. Always leaves the proposal terminal (or, when resuming
    cannot decide, failed with the breaker tripped); never raises for an expected refusal."""
    if get_run(eng, pid) is None:
        return _run_plan_only(eng, pid, clean, a)
    run = get_run(eng, pid)
    cls = eng.reg.rebuild.classes[run["guest_class"]]
    done: dict = json.loads(run["done_json"])
    target = run["target"]
    mode = "auto" if (row["decided_by"] or "").startswith("auto:") else "approval"

    if not resume:
        verdict, _f = check_eligible(eng, target, mode, exclude_pid=pid)  # reality may have changed since the proposal
        if verdict.verdict != "go":
            eng._end_precheck(pid, verdict.verdict, f"{verdict.reason} (re-read at execution time)", [])
            _set(eng, pid, outcome=verdict.verdict, finished_at=eng.now())
            return
        _set(eng, pid, started_at=eng.now(), mode=mode)  # who actually pressed the button (a policy or an operator)

    for sd in a["steps"]:
        name = sd["name"]
        if name in done or name == "plan":  # the plan was made and bound when the proposal was created
            continue
        before = eng.now()
        if name == "apply":
            if not _step_apply(eng, pid, a, done, resume):
                return
        elif name == "verify":
            _stage(eng, pid, name)
            if _step_verify(eng, pid, a, cls, target, done) is not True:
                return
        elif not _step_semaphore(eng, pid, row["action_id"], a, sd, clean, cls, run, done):
            return
        done[name].setdefault("seconds", eng.now() - before)
        _set(eng, pid, done_json=json.dumps(done, sort_keys=True))
    total = eng.now() - (get_run(eng, pid)["started_at"] or eng.now())
    plan = json.loads(run["plan_json"]).get("summary") or {}
    result = {"steps": _steps_view(done), "timings": {**{k: v.get("seconds") for k, v in done.items()}, "total": total},
              "plan_id": run["plan_id"], "origin_main": run["origin_main"], "identity": plan.get("identity")}
    with eng.lock:
        eng._move(pid, "succeeded", "succeeded", {"steps": [s["step"] for s in result["steps"]]}, finished_at=eng.now(),
                  result_json=json.dumps(result, sort_keys=True))
    _stage(eng, pid, "done", outcome="succeeded", finished_at=eng.now())
    eng.audit("proposal_succeeded", proposal=pid, action=row["action_id"], seconds=total)


def _run_plan_only(eng, pid: int, clean: dict, a: dict) -> None:
    """`rebuild-plan` (T0): ask the runner for a plan and report its summary. Changes nothing."""
    E = _eng()
    target = clean["target"]
    cls = eng.reg.rebuild.class_of(target)
    if eng.cfg.runner is None:
        eng._fail(pid, "failed", "the rebuild runner is not configured")
        return
    try:
        resp = {**eng.cfg.runner.plan(cls.name, target), "created_at": eng.now()}
        problems = validate_plan(resp, cls.name, target, eng.reg.rebuild, eng.now())
    except RunnerError as e:
        resp, problems = {"problems": e.problems or [e.message]}, [f"{e.code}: {e.message}"]
        if e.code in ("runner-unreachable", "bad-response"):
            eng._fail(pid, "failed", f"the runner could not be reached: {e.code}")
            return
    res = {"ok": True, "plan_ok": not problems, "plan_id": resp.get("plan_id"), "address": _t(resp.get("address"), 120),
           "summary": resp.get("summary"), "origin_main": resp.get("origin_main"), "expires_at": resp.get("expires_at"),
           "problems": [_t(p, 120) for p in problems][:5]}
    with eng.lock:
        eng._event(pid, "step_finished", {"step": "action", "action": "rebuild-plan", "status": "success", "plan_ok": res["plan_ok"]})
    mism = E.evaluate(a["verify"]["expect"], res)
    steps = [{"step": "action", "status": "success", "result": res}]
    if mism:
        eng._fail(pid, "verify_failed", "the plan did not pass: " + "; ".join(problems or mism), {"steps": steps, "rollback": a.get("rollback", "")})
        return
    with eng.lock:
        eng._move(pid, "succeeded", "succeeded", {"steps": ["action"]}, finished_at=eng.now(), result_json=json.dumps({"steps": steps}, sort_keys=True))
    eng.audit("proposal_succeeded", proposal=pid, action="rebuild-plan")


def _step_apply(eng, pid: int, a: dict, done: dict, resume: bool) -> bool:
    run = get_run(eng, pid)
    now = eng.now()
    params = json.loads(eng._row(pid)["params_json"])
    pol = eng.reg.rebuild
    if run["apply_started_at"] is not None and resume:
        return _resume_apply(eng, pid, a, done, run)
    if params.get("plan_hash") != run["plan_id"]:
        _fail(eng, pid, "failed", "the plan hash differs from the one this proposal was bound to: refusing to apply", done, "apply", a, half_built=False)
        return False
    if now >= run["plan_expires_at"] or now - run["plan_created_at"] > pol.limits.plan_max_age_seconds:
        _fail(eng, pid, "failed", "plan-expired: the plan is too old to apply, propose again for a fresh one", done, "apply", a, half_built=False)
        return False
    if eng.cfg.runner is None:
        _fail(eng, pid, "failed", "the rebuild runner is not configured", done, "apply", a, half_built=False)
        return False
    if eng.flag("kill_switch"):
        with eng.lock:
            eng._move(pid, "cancelled", "cancelled", {"reason": "kill-switch"}, finished_at=now)
        _set(eng, pid, outcome="cancelled", finished_at=now)
        return False
    _stage(eng, pid, "apply", apply_started_at=now)  # persisted BEFORE the call: a restart knows an apply may have begun
    try:
        resp = eng.cfg.runner.apply(run["plan_id"])
    except RunnerError as e:
        half = e.code not in PRE_APPLY_CODES  # terraform-failed, an unreachable runner, a garbled answer: we cannot say nothing changed
        _fail(eng, pid, "failed", f"the runner refused or failed the apply: {e.code}: {e.message}"[:200], done, "apply", a, half_built=half)
        return False
    res = resp.get("result") if isinstance(resp.get("result"), dict) else {}
    if resp.get("origin_main") and run["origin_main"] and resp["origin_main"] != run["origin_main"]:
        eng.audit("rebuild_origin_note", proposal=pid, planned=str(run["origin_main"])[:12], applied=str(resp["origin_main"])[:12])
    done["apply"] = {"status": "success", "seconds": resp.get("seconds"), "applied": res.get("applied"), "resources": res.get("resources")}
    with eng.lock:
        eng._event(pid, "step_finished", {"step": "apply", "status": "success", "seconds": resp.get("seconds")})
    eng.audit("step", proposal=pid, step="apply", status="success")
    return True


def _resume_apply(eng, pid: int, a: dict, done: dict, run) -> bool:
    """A restart interrupted an apply. Ask the runner; never send it again blindly."""
    st: dict = {}
    try:
        for _ in range(60):  # bounded: a running apply is waited for, an unknowable one is escalated
            st = eng.cfg.runner.status() if eng.cfg.runner is not None else {}
            if not st.get("busy"):
                break
            eng.cfg.sleep(eng.cfg.poll_seconds)
    except RunnerError as e:
        _fail(eng, pid, "failed", f"after a restart the runner could not say how the apply ended ({e.code}): a human must look", done, "apply", a, half_built=True)
        return False
    last = st.get("last") if isinstance(st.get("last"), dict) else {}
    if st.get("busy") is False and last.get("plan_id") == run["plan_id"] and last.get("ok") is True:
        done["apply"] = {"status": "success", "resumed": True, "seconds": last.get("seconds")}
        with eng.lock:
            eng._event(pid, "step_finished", {"step": "apply", "status": "success", "resumed": True})
        return True
    _fail(eng, pid, "failed", "after a restart the runner does not confirm that this plan was applied: a human must look (the guest may be half-built)",
          done, "apply", a, half_built=True)
    return False


def _step_semaphore(eng, pid: int, aid: str, a: dict, sd: dict, clean: dict, cls: rb.GuestClass, run, done: dict) -> bool:
    name = sd["name"]
    raw = (eng.reg.rebuild_raw.get("classes") or {}).get(cls.name, {})
    extra = {"class": cls.name, "converge": raw.get("converge", ""), **(sd.get("vars") or {})}
    same = run["stage"] == name  # a restart landed inside this very step: re-attach to its Semaphore task, do not start another
    _stage(eng, pid, name, **({} if same else {"task_id": None}))
    resume_task = run["task_id"] if same and run["task_id"] else None
    before = eng.now()
    try:
        status, res, tail = eng._run_task(pid, sd.get("action", aid), clean if not sd.get("action") else {"target": clean["target"]}, name,
                                          resume_task=resume_task, extra_env=extra,
                                          on_start=lambda t: _set(eng, pid, task_id=t))
    except _eng().Refused as e:
        _fail(eng, pid, "failed", f"the {name} step could not run: {e.message}", done, name, a, half_built=True)
        return False
    if status != "success" or str(res.get("ok")).lower() != "true":
        _fail(eng, pid, "failed", f"the {name} step did not pass (Semaphore {status})", done, name, a, half_built=True)
        return False
    done[name] = {"status": "success", "seconds": eng.now() - before}
    return True


def _step_verify(eng, pid: int, a: dict, cls: rb.GuestClass, target: str, done: dict) -> bool | None:
    before = eng.now()
    try:
        out = eng.verifier.check(pid, target, cls.name, cls.post_conditions)
    except _eng().Refused as e:
        _fail(eng, pid, "failed", f"the verify step could not run: {e.message}", done, "verify", a, half_built=True)
        return None
    if out.get("status") != "success":
        _fail(eng, pid, "verify_failed", f"the verify task ended {_t(out.get('status'))}", done, "verify", a, half_built=True)
        return None
    cl = run_checklist(cls.post_conditions, out.get("checks"))
    done["verify"] = {"status": "success" if cl["ok"] else "failed", "seconds": eng.now() - before, "checked": cl["checked"]}
    if not cl["ok"]:
        _fail(eng, pid, "verify_failed", "the post-conditions did not hold: " + ", ".join(cl["failed"][:6]), done, "verify", a, half_built=True)
        return None
    return True


# ---- recovery -----------------------------------------------------------------------------------------------

def recover(eng, now: int) -> list[int]:
    """Called from Engine._recover for `running` rebuild proposals. Returns the ids that can RESUME (an apply had begun, so
    state says where to pick up); the others had changed nothing and are simply failed (re-propose for a fresh plan)."""
    resumable: list[int] = []
    with eng.lock:
        rows = eng.db.execute("SELECT r.proposal_id, r.apply_started_at FROM rebuild_runs r JOIN proposals p ON p.id=r.proposal_id "
                              "WHERE p.state='running'").fetchall()
        for r in rows:
            pid = r["proposal_id"]
            if r["apply_started_at"] is None:
                eng._move(pid, "failed", "recovered", {"reason": "the Toolbelt restarted before the apply: nothing was changed, propose again"}, finished_at=now,
                          result_json=json.dumps({"why": "the Toolbelt restarted before the apply: nothing was changed, propose again", "nothing_changed": True}))
                eng.db.execute("UPDATE rebuild_runs SET outcome='failed', finished_at=?, stage='failed' WHERE proposal_id=?", (now, pid))
            else:
                eng._event(pid, "recovered", {"reason": "the Toolbelt restarted mid-rebuild: resuming from state", "resumable": True})
                resumable.append(pid)
    return resumable


# ---- autonomy (the guest-dead / guest-broken prechecks) ---------------------------------------------------------

def start_verdict(facts: dict) -> tuple[str, str]:
    """`guest-dead` for the start-guest rung: only a stopped or hung guest on a healthy node is worth starting."""
    if facts.get("node_online") is not True or facts.get("node_guests_ok") is not True:
        return "stop", "the host node is not healthy: a node-level fault is diagnosis-only"
    st = facts.get("guest_state")
    if st in ("stopped", "hung"):
        return "go", f"the guest is {st}"
    if st == "running":
        return "skip", "the guest is running: nothing to start"
    return "stop", f"the guest state is {_t(st)}: a start cannot help"


def _diag_block(raw: dict, diag: dict) -> str | None:
    rank = {"low": 0, "medium": 1, "high": 2}
    if diag.get("layer") not in raw.get("layers", []):
        return "layer"
    if rank.get(diag.get("confidence"), -1) < rank.get(raw.get("min_confidence", "high"), 2):
        return "confidence"
    if diag.get("runbook_id") != raw.get("runbook"):
        return "runbook-mismatch"
    return None


def consider_auto(eng, pid: int, diag: dict) -> dict:
    """May this fresh rebuild-section proposal run itself? Same shape as Engine.consider_auto: every refusal is logged."""
    pol = eng.reg.rebuild
    p = eng._view(pid)
    if pol is None:
        return {"auto": False, "reason": "no-rebuild-section"}
    if p["state"] != "pending":
        return {"auto": False, "reason": "proposal-not-pending"}
    cls = pol.class_of(p["target"])
    cands = [x for x in pol.policies.values() if cls and x.enabled and x.cls == cls.name and x.action == p["action_id"]]
    pe = next((x for x in cands if x.runbook == diag.get("runbook_id")), cands[0] if cands else None)  # the one the diagnosis names
    raw = (eng.reg.rebuild_raw.get("policies") or {}).get(pe.name, {}) if pe else {}
    reason: str | None = None
    if p["replay"]:
        reason = "replay"
    elif p["source"] != "diagnosis":
        reason = "not-a-diagnosis"
    elif pe is None:
        reason = "no-policy"
    else:
        reason = _diag_block(raw, diag)
    if reason is None:
        if p["action_id"] == "start-guest":
            reason = _start_gate(eng, p)
        else:
            try:
                verdict, _f = check_eligible(eng, p["target"], "auto", exclude_pid=pid)
            except _eng().Refused as e:
                verdict, reason = None, f"facts-unreadable: {e.message}"[:60]
            if verdict is not None:
                if verdict.verdict != "go":
                    reason = verdict.reason
                elif (verdict.detail.get("kind") == "dead") != (pe.precheck == "guest-dead"):
                    reason = "precheck-mismatch"
    with eng.lock:
        if reason is None:
            if not eng.flag("autonomy_rebuild"):
                reason = "autonomy-rebuild-off"
            elif eng.flag("kill_switch"):
                reason = "kill-switch"
            elif eng.flag("maintenance"):
                reason = "maintenance"
            elif eng.flag("autonomy_rebuild_breaker"):
                reason = "breaker-open"
            elif eng._row(pid)["state"] != "pending":
                reason = "proposal-not-pending"
        if reason is None:
            eng._move(pid, "approved", "approved", {"by": f"auto:{pe.name}", "ref": "policy"}, decided_at=eng.now(),
                      decided_by=f"auto:{pe.name}", decision_ref="policy")
    if reason is not None:
        eng._auto_log(pid, pe.name if pe else "", "skipped", reason)
        eng.audit("auto_skipped", proposal=pid, reason=reason, action=p["action_id"], target=p["target"])
        return {"auto": False, "reason": reason}
    eng._auto_log(pid, pe.name, "approved")
    eng.audit("proposal_auto_approved", proposal=pid, policy=pe.name, action=p["action_id"], target=p["target"])
    eng.spawn(pid, "auto")
    return {"auto": True, "policy": pe.name}


def _start_gate(eng, p: dict) -> str | None:
    try:
        facts = gather(eng, p["target"], "auto", exclude_pid=p["id"])
    except _eng().Refused as e:
        return f"facts-unreadable: {e.message}"[:60]
    verdict, why = start_verdict(facts)
    return None if verdict == "go" else f"precheck-{verdict}"


def rebuild_summary(eng) -> dict:
    pol = eng.reg.rebuild
    return {"rebuilding": active(eng), "autonomy_rebuild": eng.flag("autonomy_rebuild"),
            "breaker": eng.flag("autonomy_rebuild_breaker"),
            "policies": {n: pe.enabled for n, pe in (pol.policies if pol else {}).items()},
            "limits": None if pol is None else {"per_target_per_day": pol.limits.per_target_per_day, "fleet_per_day": pol.limits.fleet_per_day,
                                                "plan_max_age_seconds": pol.limits.plan_max_age_seconds}}


def rebuild_report(eng, since: int) -> dict:
    with eng.lock:
        rows = eng.db.execute("SELECT target, guest_class, mode, outcome, started_at, finished_at FROM rebuild_runs WHERE apply_started_at>=? ORDER BY apply_started_at",
                              (since,)).fetchall()
        trips = eng.db.execute("SELECT COUNT(*) FROM proposal_events WHERE kind='breaker_tripped' AND ts>=? AND data_json LIKE '%\"kind\": \"rebuild\"%'",
                               (since,)).fetchone()[0]
    by_outcome: dict = {}
    for r in rows:
        by_outcome[r["outcome"] or "in-flight"] = by_outcome.get(r["outcome"] or "in-flight", 0) + 1
    secs = [r["finished_at"] - r["started_at"] for r in rows if r["outcome"] == "succeeded" and r["finished_at"] and r["started_at"]]
    return {"runs": len(rows), "by_outcome": by_outcome, "by_target": {t: sum(1 for r in rows if r["target"] == t) for t in sorted({r["target"] for r in rows})},
            "unattended": sum(1 for r in rows if r["mode"] == "auto"), "breaker_trips": trips, "rebuilding": active(eng),
            "median_seconds": sorted(secs)[len(secs) // 2] if secs else None}
