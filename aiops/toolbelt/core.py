"""Toolbelt API core (Phase 10d2): ingest, dedupe, correlation grouping, circuit breakers.

Pure logic over SQLite with an injectable clock, so every rule is unit-tested without a
socket. server.py is the thin HTTP shell around this.

What /ingest decides for one alert (the answer n8n acts on, `action`):

  leader           first alert of a new incident: n8n waits `wait_seconds`, then fetches /group/<id>
  member           joined an open incident inside its correlation window: n8n does nothing more
  duplicate        same fingerprint seen again inside the cooldown: count/last-seen updated, no new run
  escalated        duplicate whose severity rose: the thread is updated, no new run
  reopened         fired again inside the cooldown after its RESOLVED: same incident, thread updated
  resolved         the recovery half for a known fingerprint: thread is closed/updated
  orphan_resolved  recovery with no known problem (the PROBLEM was lost, e.g. n8n was down): not an error
  ignored          not alertable (a severity the agent does not analyse)
  dropped          circuit breaker: queue full; counted and audited, never silent

Phase 10e adds proposals (actions.py: the agent proposes, only an operator decides, the executor runs registry
templates) and chat conversations (a human talks to the agent in Discord; every turn and tool call is recorded).

Incident states: received -> grouped (window closed) -> running -> posted -> resolved.
A step may only move forward, except running -> posted/resolved and posted -> resolved.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "tools"))

import normalize  # noqa: E402
import zabbix_event  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools  # noqa: E402
import diagnosis  # noqa: E402
import actions  # noqa: E402
import incident_draft  # noqa: E402
import change_requests  # noqa: E402

SEV_RANK = {"info": 0, "alert": 1, "critical": 2}
STATES = ("received", "grouped", "running", "posted", "resolved")
_FORWARD = {
    "received": {"grouped", "running"},
    "grouped": {"running"},
    "running": {"posted", "resolved"},
    "posted": {"resolved"},
    "resolved": set(),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_key TEXT NOT NULL,
  state TEXT NOT NULL,
  opened_at INTEGER NOT NULL,
  window_ends_at INTEGER NOT NULL,
  running_at INTEGER,
  posted_at INTEGER,
  resolved_at INTEGER,
  thread_id TEXT,
  replay TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
  fingerprint TEXT PRIMARY KEY,
  incident_id INTEGER NOT NULL REFERENCES incidents(id),
  status TEXT NOT NULL,
  severity TEXT NOT NULL,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  resolved_at INTEGER,
  count INTEGER NOT NULL,
  host TEXT NOT NULL,
  alert_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_incident ON alerts(incident_id);
CREATE INDEX IF NOT EXISTS incidents_state ON incidents(state, group_key);
CREATE TABLE IF NOT EXISTS counters (name TEXT NOT NULL, day TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (name, day));
CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER NOT NULL, tool TEXT NOT NULL,
  args_hash TEXT NOT NULL, replayed INTEGER NOT NULL, ts INTEGER NOT NULL,
  args_json TEXT, outcome TEXT NOT NULL DEFAULT 'served'
);
CREATE INDEX IF NOT EXISTS tool_calls_incident ON tool_calls(incident_id, tool, args_hash);
CREATE TABLE IF NOT EXISTS diagnoses (
  incident_id INTEGER PRIMARY KEY, diagnosis_json TEXT NOT NULL, model TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, incident_id INTEGER,
  created_at INTEGER NOT NULL, last_at INTEGER NOT NULL, turns INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chat_turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, ts INTEGER NOT NULL,
  role TEXT NOT NULL, author TEXT NOT NULL, content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_turns_conv ON chat_turns(conversation_id, id);
"""


class Rejected(Exception):
    """A request the API refuses (maps to an HTTP status in server.py)."""

    def __init__(self, status: int, message: str, detail: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail or {}


@dataclass
class Config:
    db_path: str = ":memory:"
    window_seconds: int = 90          # correlation window: alerts inside it become one incident
    cooldown_seconds: int = 1800      # same fingerprint inside this updates the thread, no new run
    daily_run_cap: int = 40           # incidents that may start an agent run per UTC day
    max_open_incidents: int = 20      # queue depth: received/grouped/running incidents
    run_wall_clock_seconds: int = 600  # an incident `running` longer than this is reported as timed out
    placement: dict[str, str] = field(default_factory=dict)  # host -> hypervisor node (from NetBox)
    placement_file: Path | None = None  # JSON map written by placement_sync.py; hot-reloaded when its mtime changes
    max_tool_calls_per_incident: int = 40  # live tool calls one incident may make (replay is not counted)
    replay_dir: Path | None = None  # aiops/replays: recorded tool responses for acceptance scenarios
    live: tools.LiveConfig | None = None  # credential-free live tools (registry, repo history, reach)
    actions: actions.ActionConfig | None = None  # 10e: None = proposals/approval/execution are not enabled
    change_requests: change_requests.CRConfig | None = None  # 10h2: None = no agent-authored PR requests
    chat_daily_turn_cap: int = 100          # human messages the agent will answer per UTC day
    chat_author_hourly_cap: int = 10        # per Discord user, so anyone in the channel can ask but not run up the bill
    chat_conversation_turn_cap: int = 30    # user messages in one thread
    chat_tool_calls_per_turn: int = 15      # live tool calls one answer may make
    chat_history_turns: int = 12            # turns handed back as context


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_of(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


class Toolbelt:
    def __init__(self, cfg: Config, routes: list[dict], known_runbooks: set[str] | None,
                 clock: Callable[[], float] = time.time, audit: Callable[[dict], None] | None = None,
                 action_ids: set[str] | None = None, registry: "actions.Registry | None" = None):
        self.cfg = cfg
        self.action_ids = action_ids if action_ids is not None else (set(registry.actions) if registry else None)
        self._placement_mtime: float | None = None
        self.routes = routes
        self.known_runbooks = known_runbooks
        self.clock = clock
        self._audit = audit or (lambda rec: print(json.dumps(rec, sort_keys=True), file=sys.stdout, flush=True))
        # Re-entrant: one SQLite connection is shared by the HTTP threads and the action executor thread, and Python's
        # sqlite3 does not serialise it for us, so EVERY read and write holds this lock.
        self._lock = threading.RLock()
        self.db = sqlite3.connect(cfg.db_path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._migrate()
        self.engine: actions.Engine | None = None
        if registry is not None and cfg.actions is not None:
            if cfg.actions.reader is None:
                cfg.actions.reader = self._read_tool
            self.engine = actions.Engine(self.db, self._lock, self.clock, self.audit, registry, cfg.actions, self._bump, self._counter)
        self.cr: change_requests.ChangeRequests | None = None
        if self.engine is not None and cfg.change_requests is not None:
            self.cr = change_requests.ChangeRequests(self.engine, cfg.change_requests)

    def _migrate(self) -> None:
        """Additive schema changes for databases created by an older version (CREATE TABLE IF NOT EXISTS never alters)."""
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(incidents)")}
        if "replay" not in cols:
            self.db.execute("ALTER TABLE incidents ADD COLUMN replay TEXT")
        tcols = {r["name"] for r in self.db.execute("PRAGMA table_info(tool_calls)")}
        if "args_json" not in tcols:
            self.db.execute("ALTER TABLE tool_calls ADD COLUMN args_json TEXT")
        if "outcome" not in tcols:
            self.db.execute("ALTER TABLE tool_calls ADD COLUMN outcome TEXT NOT NULL DEFAULT 'served'")
        if "conversation_id" not in tcols:
            self.db.execute("ALTER TABLE tool_calls ADD COLUMN conversation_id INTEGER")
            self.db.execute("ALTER TABLE tool_calls ADD COLUMN turn_id INTEGER")

    # ---- helpers -----------------------------------------------------------------------------
    def now(self) -> int:
        return int(self.clock())

    def audit(self, event: str, **kw) -> None:
        self._audit({"ts": iso(self.now()), "component": "aiops-toolbelt", "event": event, **kw})

    def _bump(self, name: str) -> int:
        day = day_of(self.now())
        self.db.execute(
            "INSERT INTO counters(name, day, n) VALUES (?, ?, 1) ON CONFLICT(name, day) DO UPDATE SET n = n + 1",
            (name, day))
        return self._counter(name)

    def _counter(self, name: str) -> int:
        row = self.db.execute("SELECT n FROM counters WHERE name=? AND day=?", (name, day_of(self.now()))).fetchone()
        return row["n"] if row else 0

    def _placement(self) -> dict[str, str]:
        """The guest -> hypervisor map, hot-reloaded from the sync job's file. A missing, unreadable or malformed file
        keeps the last good map (grouping never breaks because the sync job did)."""
        f = self.cfg.placement_file
        if f is not None:
            try:
                mtime = f.stat().st_mtime
                if mtime != self._placement_mtime:
                    data = json.loads(f.read_text())
                    if isinstance(data, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
                        self.cfg.placement = data
                        self._placement_mtime = mtime
                        self.audit("placement_loaded", guests=len(data))
            except (OSError, ValueError):
                pass
        return self.cfg.placement

    def _group_key(self, host: str) -> str:
        node = self._placement().get(host)
        return f"node:{node}" if node else "unplaced"

    def _open_incidents(self) -> int:
        """Incidents occupying a queue slot. A run that has outlived the wall-clock cap stopped being work in flight
        (its workflow died or hung without reporting back), so it no longer holds a slot: otherwise a few stuck runs
        would fill the queue and silently stop all new analysis."""
        return self.db.execute(
            "SELECT COUNT(*) FROM incidents WHERE state IN ('received','grouped') "
            "OR (state='running' AND (running_at IS NULL OR running_at > ?))",
            (self.now() - self.cfg.run_wall_clock_seconds,)).fetchone()[0]

    # ---- /ingest/zabbix ----------------------------------------------------------------------
    def ingest_zabbix(self, event: dict, replay: str | None = None) -> dict:
        problems = validate_zabbix_event(event)
        if problems:
            self.audit("rejected", source="zabbix", reason="invalid-event", problems=problems)
            raise Rejected(400, "invalid event: " + "; ".join(problems))
        now = self.now()
        alerts = zabbix_event.from_zabbix_event(event, iso(now), self.routes, self.known_runbooks)
        if not alerts:
            self.audit("ignored", source="zabbix", host=event.get("host"), severity=event.get("severity"))
            return {"action": "ignored"}
        with self._lock:
            return self._ingest_alert(alerts[0], now, replay)

    def _ingest_alert(self, alert: dict, now: int, replay: str | None = None) -> dict:
        scenario = None
        if replay:
            # An acceptance run is ISOLATED from real alerts in both directions: its fingerprint is unique per run (so it
            # is never deduped against, and never suppresses, a genuine alert) and it correlates only with itself.
            if (not tools.REPLAY_NAME.match(replay) or self.cfg.replay_dir is None
                    or not tools.scenario_file(self.cfg.replay_dir, replay).is_file()):
                self.audit("rejected", source="zabbix", reason="unknown-replay-scenario", scenario=replay[:60])
                raise Rejected(400, "unknown replay scenario")
            scenario = replay
            alert = dict(alert, fingerprint=f"{alert['fingerprint']}~{replay}~{now}")
        fp = alert["fingerprint"]
        sev = alert["severity"]
        row = self.db.execute("SELECT * FROM alerts WHERE fingerprint=?", (fp,)).fetchone()

        if alert["status"] == "resolved":
            if row is None or row["status"] == "resolved":
                self._bump("orphan_resolved")
                self.audit("orphan_resolved", fingerprint=fp, host=alert["host"])
                return {"action": "orphan_resolved", "fingerprint": fp, "alert": alert}
            self.db.execute("UPDATE alerts SET status='resolved', resolved_at=?, last_seen=? WHERE fingerprint=?",
                            (now, now, fp))
            inc = self._incident(row["incident_id"])
            self._maybe_resolve_incident(row["incident_id"], now)
            self.audit("resolved", fingerprint=fp, incident=row["incident_id"], host=alert["host"])
            return {"action": "resolved", "fingerprint": fp, "incident_id": row["incident_id"],
                    "thread_id": inc["thread_id"], "alert": alert}

        # status firing (or one-shot `event`, treated as firing with no recovery half)
        if row is not None:
            age = now - (row["resolved_at"] if row["status"] == "resolved" else row["last_seen"])
            if age < self.cfg.cooldown_seconds:
                escalated = SEV_RANK[sev] > SEV_RANK[row["severity"]]
                reopened = row["status"] == "resolved"
                self.db.execute(
                    "UPDATE alerts SET status='firing', resolved_at=NULL, severity=?, last_seen=?, count=count+1, "
                    "alert_json=? WHERE fingerprint=?",
                    (sev if escalated else row["severity"], now, json.dumps(alert, sort_keys=True), fp))
                if reopened:
                    self.db.execute("UPDATE incidents SET resolved_at=NULL, state=CASE WHEN state='resolved' THEN 'posted' "
                                    "ELSE state END WHERE id=?", (row["incident_id"],))
                action = "reopened" if reopened else ("escalated" if escalated else "duplicate")
                inc = self._incident(row["incident_id"])
                self.audit(action, fingerprint=fp, incident=row["incident_id"], count=row["count"] + 1)
                return {"action": action, "fingerprint": fp, "incident_id": row["incident_id"],
                        "thread_id": inc["thread_id"], "count": row["count"] + 1, "alert": alert}

        # a genuinely new problem (or one whose cooldown expired): group it
        key = f"replay:{scenario}" if scenario else self._group_key(alert["host"])
        open_inc = self.db.execute(
            "SELECT * FROM incidents WHERE state='received' AND group_key=? AND window_ends_at > ? "
            "ORDER BY id LIMIT 1", (key, now)).fetchone()
        if open_inc is not None:
            inc_id, action = open_inc["id"], "member"
        else:
            if self._open_incidents() >= self.cfg.max_open_incidents:
                n = self._bump("dropped_queue_full")
                self.audit("dropped", reason="queue-full", fingerprint=fp, host=alert["host"], dropped_today=n)
                return {"action": "dropped", "reason": "queue-full", "fingerprint": fp,
                        "first_drop_today": n == 1}
            # an acceptance run remembers its scenario so every later tool call is answered from recordings
            cur = self.db.execute(
                "INSERT INTO incidents(group_key, state, opened_at, window_ends_at, replay) VALUES (?, 'received', ?, ?, ?)",
                (key, now, now + self.cfg.window_seconds, scenario))
            inc_id, action = cur.lastrowid, "leader"
        self.db.execute(
            "INSERT INTO alerts(fingerprint, incident_id, status, severity, first_seen, last_seen, count, host, alert_json) "
            "VALUES (?, ?, 'firing', ?, ?, ?, 1, ?, ?) ON CONFLICT(fingerprint) DO UPDATE SET incident_id=excluded.incident_id, "
            "status='firing', resolved_at=NULL, severity=excluded.severity, first_seen=excluded.first_seen, "
            "last_seen=excluded.last_seen, count=1, alert_json=excluded.alert_json",
            (fp, inc_id, sev, now, now, alert["host"], json.dumps(alert, sort_keys=True)))
        self.audit(action, fingerprint=fp, incident=inc_id, group_key=key, host=alert["host"], severity=sev)
        out = {"action": action, "fingerprint": fp, "incident_id": inc_id, "alert": alert}
        if action == "leader":
            out["wait_seconds"] = self.cfg.window_seconds
        return out

    def _incident(self, inc_id: int) -> sqlite3.Row:
        with self._lock:
            row = self.db.execute("SELECT * FROM incidents WHERE id=?", (inc_id,)).fetchone()
        if row is None:
            raise Rejected(404, f"no incident {inc_id}")
        return row

    def _maybe_resolve_incident(self, inc_id: int, now: int) -> None:
        left = self.db.execute("SELECT COUNT(*) FROM alerts WHERE incident_id=? AND status!='resolved'",
                               (inc_id,)).fetchone()[0]
        if left == 0:
            self.db.execute("UPDATE incidents SET resolved_at=?, state=CASE WHEN state IN ('received','grouped') "
                            "THEN state ELSE 'resolved' END WHERE id=?", (now, inc_id))

    # ---- /group/<id> -------------------------------------------------------------------------
    def group(self, inc_id: int) -> dict:
        with self._lock:
            now = self.now()
            inc = self._incident(inc_id)
            if inc["state"] == "received" and now >= inc["window_ends_at"]:
                self.db.execute("UPDATE incidents SET state='grouped' WHERE id=?", (inc_id,))
                inc = self._incident(inc_id)
            rows = self.db.execute("SELECT * FROM alerts WHERE incident_id=? ORDER BY first_seen, fingerprint",
                                   (inc_id,)).fetchall()
            alerts = []
            for r in rows:
                a = json.loads(r["alert_json"])
                a["_state"] = {"status": r["status"], "count": r["count"], "first_seen": iso(r["first_seen"]),
                               "last_seen": iso(r["last_seen"]), "severity": r["severity"]}
                alerts.append(a)
            worst = max((r["severity"] for r in rows), key=lambda s: SEV_RANK[s], default="info")
            placement = self._placement()
            nodes = sorted({placement[r["host"]] for r in rows if r["host"] in placement})
            timed_out = (inc["state"] == "running" and inc["running_at"] is not None
                         and now - inc["running_at"] > self.cfg.run_wall_clock_seconds)
            return {
                "incident_id": inc_id, "state": inc["state"], "group_key": inc["group_key"],
                "window_open": inc["state"] == "received" and now < inc["window_ends_at"],
                "opened_at": iso(inc["opened_at"]), "thread_id": inc["thread_id"],
                "severity": worst,
                # canary-only incidents are analysed, at lower priority (decision D-e)
                "priority": "low" if worst == "info" else "normal",
                "alert_count": len(alerts), "hypervisors": nodes,
                "model_hint": "opus" if (len(alerts) >= 3 or (worst == "critical" and not nodes and len(alerts) > 1)) else "sonnet",
                "timed_out": timed_out, "replay": inc["replay"], "alerts": alerts,
            }

    def set_state(self, inc_id: int, state: str, thread_id: str | None = None) -> dict:
        if state not in STATES:
            raise Rejected(400, f"unknown state {state!r}")
        with self._lock:
            now = self.now()
            inc = self._incident(inc_id)
            after_resolved = False
            if inc["state"] == "resolved" and state in ("posted", "resolved"):
                # The alert recovered while the run was still going (running -> resolved by the recovery event). The run
                # then finishes and posts its thread: record the thread and keep the incident resolved, never a 409.
                if state == "posted":
                    self.db.execute("UPDATE incidents SET posted_at=COALESCE(posted_at, ?), thread_id=COALESCE(?, thread_id) WHERE id=?",
                                    (now, thread_id, inc_id))
                    self.audit("state", incident=inc_id, state="posted-after-resolved", thread_id=thread_id)
                after_resolved = True
            elif state not in _FORWARD[inc["state"]]:
                raise Rejected(409, f"cannot move incident {inc_id} from {inc['state']} to {state}")
            if after_resolved:
                pass  # handled above
            elif state == "running":
                if self._counter("runs") >= self.cfg.daily_run_cap:
                    first = self._bump("budget_exhausted") == 1
                    self.audit("budget_exhausted", incident=inc_id, cap=self.cfg.daily_run_cap)
                    raise Rejected(429, "daily-run-cap" + (" first" if first else ""))
                self._bump("runs")
                self.db.execute("UPDATE incidents SET state='running', running_at=? WHERE id=?", (now, inc_id))
            elif state == "posted":
                self.db.execute("UPDATE incidents SET state='posted', posted_at=?, thread_id=COALESCE(?, thread_id) "
                                "WHERE id=?", (now, thread_id, inc_id))
            elif state == "resolved":
                self.db.execute("UPDATE incidents SET state='resolved', resolved_at=? WHERE id=?", (now, inc_id))
            else:
                self.db.execute("UPDATE incidents SET state=? WHERE id=?", (state, inc_id))
            self.audit("state", incident=inc_id, state=state, thread_id=thread_id)
        if thread_id and state == "posted" and self.engine is not None:
            self.engine.bind_thread(inc_id, thread_id)
        return self.group(inc_id)

    def _record_call(self, incident_id: object, name: str, h: str, replayed: bool, args: dict | None = None,
                     outcome: str = "served", conversation_id: int | None = None, turn_id: int | None = None) -> None:
        """The audit trail a diagnosis is checked against. Only `served` rows count as evidence; a NO_RECORDING
        is kept (the harness reports it) but can never ground a claim."""
        if isinstance(incident_id, int) and not isinstance(incident_id, bool):
            with self._lock:
                self.db.execute("INSERT INTO tool_calls(incident_id, tool, args_hash, replayed, ts, args_json, outcome, conversation_id, turn_id) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (incident_id, name, h, int(replayed), self.now(), json.dumps(args, sort_keys=True), outcome,
                                 conversation_id, turn_id))

    # ---- /tool/<name> --------------------------------------------------------------------------
    def call_tool(self, name: str, args: object, incident_id: object = None, replay: str | None = None,
                  conversation_id: object = None, turn_id: object = None) -> dict:
        """One read-only tool call. Unknown or write-shaped names never reach a handler. A chat answer passes
        conversation_id + turn_id instead of an incident (live only, capped per turn)."""
        if conversation_id is not None:
            return self._chat_tool(name, args, conversation_id, turn_id)
        try:
            clean = tools.validate(name, args)
        except tools.ToolError as e:
            self.audit("tool_denied", tool=name, reason=e.message[:120], incident=incident_id)
            raise Rejected(e.status, e.message)
        h = tools.args_hash(name, clean)
        t0 = time.monotonic()
        if not replay and isinstance(incident_id, int) and not isinstance(incident_id, bool):
            with self._lock:
                row = self.db.execute("SELECT replay FROM incidents WHERE id=?", (incident_id,)).fetchone()
            replay = row["replay"] if row else None
        if replay:
            if self.cfg.replay_dir is None:
                raise Rejected(501, "replay is not configured")
            try:
                out = tools.replay(self.cfg.replay_dir, replay, name, clean)
            except tools.ToolError as e:
                if e.message == "NO_RECORDING":
                    self._record_call(incident_id, name, h, True, clean, "no_recording")
                    with self._lock:
                        n = self._bump("no_recording")
                    self.audit("tool_no_recording", tool=name, args=h, scenario=replay, count_today=n)
                raise Rejected(e.status, e.message)
            self._record_call(incident_id, name, h, True, clean)
            self.audit("tool_call", tool=name, args=h, replayed=True, scenario=replay, incident=incident_id)
            return {"tool": name, "replayed": True, "result": out}
        if isinstance(incident_id, bool) or not isinstance(incident_id, int):
            raise Rejected(400, "incident_id (integer) is required for live tool calls")
        with self._lock:
            self._incident(incident_id)  # 404 if unknown
            n = self._bump(f"tools:{incident_id}")
            if n > self.cfg.max_tool_calls_per_incident:
                self.audit("tool_denied", tool=name, reason="per-incident-cap", incident=incident_id)
                raise Rejected(429, "per-incident tool-call cap reached")
        if self.cfg.live is None:
            raise Rejected(501, "live tools are not configured")
        try:
            out = tools.live(self.cfg.live, name, clean)
        except tools.ToolError as e:
            self.audit("tool_error", tool=name, args=h, status=e.status, incident=incident_id)
            raise Rejected(e.status, e.message)
        self._record_call(incident_id, name, h, False, clean)
        self.audit("tool_call", tool=name, args=h, replayed=False, incident=incident_id,
                   ms=int((time.monotonic() - t0) * 1000))
        return {"tool": name, "replayed": False, "result": out}

    # ---- GET /replay/<scenario>/latest (acceptance harness, read-only) -------------------------
    def replay_latest(self, scenario: str) -> dict:
        """The most recent incident ingested for a replay scenario: its state, the stored diagnosis and every tool
        call the agent made (with arguments and whether it was served), so a harness can judge the run."""
        if not tools.REPLAY_NAME.match(scenario or ""):
            raise Rejected(400, "bad replay scenario name")
        with self._lock:
            inc = self.db.execute("SELECT * FROM incidents WHERE replay=? ORDER BY id DESC LIMIT 1", (scenario,)).fetchone()
            if inc is None:
                raise Rejected(404, "no incident has been ingested for this scenario")
            d = self.db.execute("SELECT diagnosis_json, model FROM diagnoses WHERE incident_id=?", (inc["id"],)).fetchone()
            calls = [{"tool": r["tool"], "args": json.loads(r["args_json"] or "{}"), "outcome": r["outcome"]}
                     for r in self.db.execute("SELECT tool, args_json, outcome FROM tool_calls WHERE incident_id=? ORDER BY id", (inc["id"],))]
            alerts = [r["host"] for r in self.db.execute("SELECT host FROM alerts WHERE incident_id=? ORDER BY first_seen", (inc["id"],))]
            return {"incident_id": inc["id"], "state": inc["state"], "thread_id": inc["thread_id"], "alerts": alerts,
                    "diagnosis": json.loads(d["diagnosis_json"]) if d else None, "model": d["model"] if d else None,
                    "calls": calls, "no_recording": sum(1 for c in calls if c["outcome"] == "no_recording")}

    # ---- GET /watchdog (the n8n watchdog workflow polls this) ------------------------------------
    def watchdog(self, limit: int = 10) -> list[dict]:
        """Incidents whose run died without posting: `running` for longer than the wall-clock cap and still no thread.
        Each comes with the plain fallback post (thread name + content) so the caller needs no logic. Posting it and
        moving the incident to `posted` (POST /group/<id>/state) removes it from this list; if the post fails, the next
        poll offers it again, so an alert is never silently left without a thread."""
        with self._lock:
            now = self.now()
            rows = self.db.execute(
                "SELECT id FROM incidents WHERE state='running' AND thread_id IS NULL AND running_at IS NOT NULL AND running_at <= ? "
                "ORDER BY id LIMIT ?", (now - self.cfg.run_wall_clock_seconds, limit)).fetchall()
        out = []
        for r in rows:
            g = self.group(r["id"])
            first = g["alerts"][0] if g["alerts"] else {"host": "?", "check": "alert", "severity": "?"}
            suffix = (f" (+{g['alert_count'] - 1} more)" if g["alert_count"] > 1 else "") + (" [replay]" if g["replay"] else "")
            hosts = ", ".join(a["host"] + ": " + a["summary"] for a in g["alerts"][:10])
            out.append({
                "incident_id": g["incident_id"],
                "thread_name": f"[{first['severity']}] {first['host']} - {first['check']}{suffix} (no analysis)"[:95],
                "content": ("**Analysis unavailable** (the diagnosis run did not finish); the alert itself is in the usual channel.\n"
                            f"incident #{g['incident_id']} - {g['alert_count']} alert(s) - priority {g['priority']}\n- " + hosts.replace(", ", "\n- "))[:1900],
            })
        if out:
            self.audit("watchdog_offered", incidents=[o["incident_id"] for o in out])
        return out

    # ---- /diagnosis/<incident> -----------------------------------------------------------------
    def diagnose(self, incident_id: int, body: object) -> dict:
        """Validate a diagnosis against structure AND the audit trail; return the Discord rendering.

        422 with `problems` when it fails: the workflow may retry once, then posts a plain
        "no analysis" message. A diagnosis is stored (and replaced on re-post) only if it passes.
        """
        if not isinstance(body, dict) or "diagnosis" not in body:
            raise Rejected(400, "need {\"diagnosis\": {...}}")
        diag = body["diagnosis"]
        model = str(body.get("model", ""))[:60]
        with self._lock:
            self._incident(incident_id)  # 404
            problems = diagnosis.structure(diag)
            if not problems:
                def served(tool: str, args: dict) -> bool:
                    try:
                        clean = tools.validate(tool, args)
                    except tools.ToolError:
                        return False
                    h = tools.args_hash(tool, clean)
                    return self.db.execute("SELECT 1 FROM tool_calls WHERE incident_id=? AND tool=? AND args_hash=? AND outcome='served'",
                                           (incident_id, tool, h)).fetchone() is not None
                problems = diagnosis.grounding(
                    diag, incident_id=incident_id, served=served, known_runbooks=self.known_runbooks,
                    action_ids=self.action_ids, repo_dir=self.cfg.live.repo_dir if self.cfg.live else None)
            if problems:
                self.audit("diagnosis_rejected", incident=incident_id, problems=len(problems))
                raise Rejected(422, "diagnosis failed validation", {"problems": problems[:20]})
            n_calls = self.db.execute("SELECT COUNT(*) FROM tool_calls WHERE incident_id=?", (incident_id,)).fetchone()[0]
            n_alerts = self.db.execute("SELECT COUNT(*) FROM alerts WHERE incident_id=?", (incident_id,)).fetchone()[0]
            self.db.execute(
                "INSERT INTO diagnoses(incident_id, diagnosis_json, model, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(incident_id) DO UPDATE SET diagnosis_json=excluded.diagnosis_json, model=excluded.model, "
                "created_at=excluded.created_at", (incident_id, json.dumps(diag, sort_keys=True), model, self.now()))
            self.audit("diagnosis_accepted", incident=incident_id, layer=diag["layer"], confidence=diag["confidence"],
                       evidence=len(diag["evidence"]), tool_calls=n_calls)
            inc = self._incident(incident_id)
        proposals, refused = self._propose_from(diag, inc)
        autos: list = []
        for p in proposals:  # autonomy is the Toolbelt's own decision from the registry; a replay can never qualify
            autos.append(self.engine.consider_auto(p["id"], diag) if (p and self.engine is not None and not inc["replay"]) else None)
        return {"ok": True, "content": diagnosis.render(diag, alert_count=n_alerts, model=model, tool_calls=n_calls,
                                                        proposals=proposals, refused=refused, autos=autos),
                "layer": diag["layer"], "confidence": diag["confidence"], "needs_human": diag["needs_human"],
                "proposals": [p["id"] for p in proposals if p], "proposals_refused": refused,
                "auto": [a for a in autos if a]}

    def _read_tool(self, name: str, args: dict) -> dict:
        """One read-only tool call for the engine's autonomy prechecks (the same allow-list the agent is held to)."""
        if self.cfg.live is None:
            raise tools.ToolError(501, "live tools are not configured")
        return tools.live(self.cfg.live, name, tools.validate(name, args))

    def _propose_from(self, diag: dict, inc) -> tuple[list, list]:
        """Turn a validated diagnosis's proposed_actions into stored proposals (never executions). One that fails the
        registry guard is reported back, not silently dropped. Replay incidents get replay proposals: visible to the
        acceptance harness, impossible to decide."""
        if self.engine is None or not diag.get("proposed_actions"):
            return [None] * len(diag.get("proposed_actions", [])), []
        out, refused = [], []
        for a in diag["proposed_actions"]:
            try:
                out.append(self.engine.propose(action_id=a["action_id"], params=a.get("params", {}), reason=a["reason"], source="diagnosis",
                                               incident_id=inc["id"], thread_id=inc["thread_id"], replay=bool(inc["replay"])))
            except actions.Refused as e:
                out.append(None)
                refused.append({"action_id": a["action_id"], "why": e.message, "problems": e.detail.get("problems", [])[:5]})
        return out, refused

    def stats(self) -> dict:
        with self._lock:
            return {"open_incidents": self._open_incidents(), "runs_today": self._counter("runs"),
                    "daily_run_cap": self.cfg.daily_run_cap, "dropped_today": self._counter("dropped_queue_full"),
                    "orphan_resolved_today": self._counter("orphan_resolved"),
                    "no_recording_today": self._counter("no_recording"),
                    "chat_turns_today": self._counter("chat_turns")}

    # ---- proposals + status (Phase 10e) ---------------------------------------------------------------
    def propose(self, body: dict) -> dict:
        """POST /proposals (agent role): a PENDING proposal for a registry action, from a chat turn or an incident. The
        engine validates it against the registry; nothing is executed, and only the approver role can decide it."""
        if self.engine is None:
            raise Rejected(501, "actions are not enabled on this Toolbelt")
        extra = set(body) - {"action_id", "params", "reason", "conversation_id", "incident_id"}
        if extra:
            raise Rejected(400, f"unexpected field(s): {sorted(extra)}")
        action_id = body.get("action_id")
        if not isinstance(action_id, str):
            raise Rejected(400, "action_id (string) is required")
        conv_id, inc_id, thread_id = body.get("conversation_id"), body.get("incident_id"), None
        for name, v in (("conversation_id", conv_id), ("incident_id", inc_id)):
            if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
                raise Rejected(400, f"{name} must be an integer")
        if conv_id is not None:
            with self._lock:
                conv = self.db.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
            if conv is None:
                raise Rejected(404, f"no conversation {conv_id}")
            thread_id, inc_id = conv["thread_id"], conv["incident_id"]
        replay = False
        if inc_id is not None:
            inc = self._incident(inc_id)
            replay, thread_id = bool(inc["replay"]), thread_id or inc["thread_id"]
        return self.engine.propose(action_id=action_id, params=body.get("params", {}), reason=body.get("reason"),
                                   source="chat" if conv_id is not None else "diagnosis", incident_id=inc_id,
                                   conversation_id=conv_id, thread_id=thread_id, replay=replay)

    def incident_draft(self, incident_id: object) -> dict:
        """GET /incident/<id>/draft (approver role): the mechanical incident write-up (Phase 10h3), scrubbed. Read-only."""
        if not isinstance(incident_id, int) or isinstance(incident_id, bool):
            raise Rejected(400, "incident id must be an integer")
        with self._lock:
            try:
                text = incident_draft.build_draft(self.db, incident_id, redact=tools.redact)
            except KeyError:
                raise Rejected(404, f"no incident {incident_id}")
            slug = incident_draft.slug_for(self.db, incident_id)
        self.audit("incident_draft", incident=incident_id, chars=len(text))
        return {"incident_id": incident_id, "slug": slug, "filename": f"{slug}.md", "markdown": text[:60000]}

    def status(self) -> dict:
        """GET /status (approver role): what /aiops status shows."""
        out = self.stats()
        if self.engine is not None:
            out["actions"] = self.engine.summary()
            out["open_proposals"] = self.engine.list(("pending", "approved", "running"))
        if self.cr is not None:
            out["change_requests"] = self.cr.summary()
        return out

    def report(self, days: int = 14) -> dict:
        """GET /report (approver role): what autonomy did, why it did not, breaker trips, flapping (the soak's evidence)."""
        if self.engine is None:
            raise Rejected(501, "actions are not enabled on this Toolbelt")
        return self.engine.report(days)

    # ---- chat (Phase 10e): a human talks to the agent in Discord ---------------------------------------
    def chat_turn(self, thread_id: object, author: object, content: object) -> dict:
        """Record one human message and hand back what the agent needs to answer it: the conversation, the recent
        history, and (in an incident thread) the incident and its diagnosis. Refused (429) past the daily budget, a
        per-author hourly rate or the conversation's turn cap, so anyone in the channel may ask but nobody can run up the bill."""
        if not (isinstance(thread_id, str) and thread_id.isdigit() and 17 <= len(thread_id) <= 20):
            raise Rejected(400, "thread_id must be a Discord id")
        if not (isinstance(author, str) and author.isdigit() and 17 <= len(author) <= 20):
            raise Rejected(400, "author must be a Discord user id")
        if not (isinstance(content, str) and content.strip()) or len(content) > 2000:
            raise Rejected(400, "content must be 1..2000 characters")
        with self._lock:
            now = self.now()
            if self._counter("chat_turns") >= self.cfg.chat_daily_turn_cap:
                self.audit("chat_denied", reason="daily-cap")
                raise Rejected(429, "daily-chat-cap")
            hour = self.db.execute("SELECT COUNT(*) FROM chat_turns WHERE role='user' AND author=? AND ts>?", (author, now - 3600)).fetchone()[0]
            if hour >= self.cfg.chat_author_hourly_cap:
                self.audit("chat_denied", reason="author-rate", author=author[-4:])
                raise Rejected(429, "author-rate")
            conv = self.db.execute("SELECT * FROM conversations WHERE thread_id=?", (thread_id,)).fetchone()
            if conv is None:
                inc = self.db.execute("SELECT id FROM incidents WHERE thread_id=?", (thread_id,)).fetchone()
                cur = self.db.execute("INSERT INTO conversations(thread_id, kind, incident_id, created_at, last_at) VALUES (?, ?, ?, ?, ?)",
                                      (thread_id, "incident" if inc else "chat", inc["id"] if inc else None, now, now))
                conv = self.db.execute("SELECT * FROM conversations WHERE id=?", (cur.lastrowid,)).fetchone()
            if conv["turns"] >= self.cfg.chat_conversation_turn_cap:
                self.audit("chat_denied", reason="conversation-cap", conversation=conv["id"])
                raise Rejected(429, "conversation-cap")
            history = [{"role": r["role"], "author": r["author"], "content": r["content"]} for r in reversed(self.db.execute(
                "SELECT role, author, content FROM chat_turns WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                (conv["id"], self.cfg.chat_history_turns)).fetchall())]
            turn = self.db.execute("INSERT INTO chat_turns(conversation_id, ts, role, author, content) VALUES (?, ?, 'user', ?, ?)",
                                   (conv["id"], now, author, content)).lastrowid
            self.db.execute("UPDATE conversations SET turns=turns+1, last_at=? WHERE id=?", (now, conv["id"]))
            self._bump("chat_turns")
            incident_id = conv["incident_id"]
        self.audit("chat_turn", conversation=conv["id"], turn=turn, kind=conv["kind"], incident=incident_id, author=author[-4:])
        ctx = {}
        if incident_id is not None:
            ctx["incident"] = self.group(incident_id)
            with self._lock:
                d = self.db.execute("SELECT diagnosis_json FROM diagnoses WHERE incident_id=?", (incident_id,)).fetchone()
            ctx["diagnosis"] = json.loads(d["diagnosis_json"]) if d else None
            if self.engine is not None:
                ctx["proposals"] = [p for p in self.engine.list(("pending", "approved", "running", "succeeded", "failed", "verify_failed", "rejected", "expired", "cancelled"))
                                    if p["incident_id"] == incident_id]
        return {"conversation_id": conv["id"], "turn_id": turn, "kind": conv["kind"], "incident_id": incident_id,
                "history": history, "context": ctx,
                "limits": {"tool_calls_per_turn": self.cfg.chat_tool_calls_per_turn, "reply_max_chars": 1900}}

    def chat_reply(self, conversation_id: object, turn_id: object, content: object) -> dict:
        """Store the agent's answer and return it scrubbed and Discord-sized. Anything secret-shaped is replaced, never posted."""
        if not (isinstance(conversation_id, int) and isinstance(turn_id, int) and isinstance(content, str) and content.strip()):
            raise Rejected(400, "need conversation_id, turn_id and content")
        with self._lock:
            if self.db.execute("SELECT 1 FROM chat_turns WHERE id=? AND conversation_id=?", (turn_id, conversation_id)).fetchone() is None:
                raise Rejected(404, "no such turn in that conversation")
        text = content
        redacted = 0
        for pat, _what in diagnosis.SECRETISH:
            text, n = pat.subn("<redacted>", text)
            redacted += n
        text = text.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
        if len(text) > 1900:
            text = text[:1880].rstrip() + "\n...(truncated)"
        with self._lock:
            self.db.execute("INSERT INTO chat_turns(conversation_id, ts, role, author, content) VALUES (?, ?, 'assistant', 'agent', ?)",
                            (conversation_id, self.now(), text))
        self.audit("chat_reply", conversation=conversation_id, turn=turn_id, chars=len(text), redacted=redacted)
        return {"content": text, "redacted": redacted}

    def _chat_tool(self, name: str, args: object, conversation_id: object, turn_id: object) -> dict:
        if not (isinstance(conversation_id, int) and not isinstance(conversation_id, bool) and isinstance(turn_id, int) and not isinstance(turn_id, bool)):
            raise Rejected(400, "conversation_id and turn_id must be integers")
        try:
            clean = tools.validate(name, args)
        except tools.ToolError as e:
            self.audit("tool_denied", tool=name, reason=e.message[:120], conversation=conversation_id)
            raise Rejected(e.status, e.message)
        with self._lock:
            conv = self.db.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if conv is None or self.db.execute("SELECT 1 FROM chat_turns WHERE id=? AND conversation_id=? AND role='user'", (turn_id, conversation_id)).fetchone() is None:
                raise Rejected(404, "no such conversation turn")
            n = self._bump(f"ctools:{turn_id}")
            if n > self.cfg.chat_tool_calls_per_turn:
                self.audit("tool_denied", tool=name, reason="per-turn-cap", conversation=conversation_id)
                raise Rejected(429, "per-turn tool-call cap reached")
        if self.cfg.live is None:
            raise Rejected(501, "live tools are not configured")
        h = tools.args_hash(name, clean)
        t0 = time.monotonic()
        try:
            out = tools.live(self.cfg.live, name, clean)
        except tools.ToolError as e:
            self.audit("tool_error", tool=name, args=h, status=e.status, conversation=conversation_id)
            raise Rejected(e.status, e.message)
        self._record_call(conv["incident_id"] or 0, name, h, False, clean, "served", conversation_id, turn_id)
        self.audit("tool_call", tool=name, args=h, replayed=False, conversation=conversation_id, turn=turn_id, ms=int((time.monotonic() - t0) * 1000))
        return {"tool": name, "replayed": False, "result": out}


# ---- input validation (the schema file is the contract; this is the dependency-free gate) ----------
_REQUIRED = {
    "schema_version": str, "source": str, "status": str, "event_id": str, "severity": str, "host": str,
    "trigger_id": str, "trigger_name": str, "tags": list, "items": list, "fired_at": str,
}


def validate_zabbix_event(event: object) -> list[str]:
    if not isinstance(event, dict):
        return ["body is not a JSON object"]
    problems = []
    for key, typ in _REQUIRED.items():
        if key not in event:
            problems.append(f"missing {key}")
        elif not isinstance(event[key], typ):
            problems.append(f"{key} must be {typ.__name__}")
    if event.get("schema_version") != "aiops.zabbix-event/v1":
        problems.append("schema_version must be aiops.zabbix-event/v1")
    if event.get("source") != "zabbix":
        problems.append("source must be zabbix")
    if event.get("status") not in ("PROBLEM", "RESOLVED"):
        problems.append("status must be PROBLEM or RESOLVED")
    return problems
