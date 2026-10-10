"""Gná's rightsizing digest (Phase 10i3): one quiet, periodic read of where the pods' memory and CPU stand and what could be trimmed.

The daily findings pass (rightsizing.py, run by the forecast job) leaves two things in the current-findings file: quiet `rightsizing`
rows in the forecast store and a per-worker snapshot. This module turns them into a digest on the operator's cadence (weekly by
default, bi-weekly or monthly), stores it, and hands it to the bot to post. It never posts, drafts or changes anything itself.

  scoreboard   per worker: memory requested / limits / used and CPU requested, now against the previous digest and the first one (the
               baseline), with a vmui link per worker in place of a sparkline
  suggestions  at most five open findings, memory under-requests first, then by what they free; each is a forecast row, so the
               Useful / Noise labels and the Draft PR button work on it like on a forecast card. A suggestion called Noise is not repeated
               until its proposed number has moved by more than 30 %
  tuning       memory-creep findings as facts (the number comes with no proposal: a leak, an unbounded cache or a missing heap limit)
  results      what earlier rightsizing PRs freed and whether they held (10i5; empty until the first one merges)
  coverage     controllers without a VPA and the findings that were suppressed, with the reason

States: a digest is `unposted` until the bot sets its message; `tick` hands out the oldest unposted one before it builds a new one, so a
failed Discord post is retried and never skipped.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import urllib.parse
from typing import Callable

import forecast_store
import rightsizing

PERIOD_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}
NOISE_MOVE = 0.30
MAX_SUGGESTIONS = 5
MAX_TUNING = 5
STALE_PASS_SECONDS = 36 * 3600
VMUI = "https://metric.niflheim.xiiisins.com/vmui/#/?g0.range_input=30d&g0.expr="

SCHEMA = """
CREATE TABLE IF NOT EXISTS rightsizing_digests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER NOT NULL, trigger TEXT NOT NULL, cadence TEXT NOT NULL,
  data_json TEXT NOT NULL, message_ref TEXT, thread_id TEXT, posted_at INTEGER
);
CREATE TABLE IF NOT EXISTS rightsizing_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, set_by TEXT, set_at INTEGER);
"""


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _mib(x) -> str:
    return f"{x:g} MiB"


def _m(x) -> str:
    return f"{x:g}m"


def proposal(e: dict) -> dict | None:
    """What a finding proposes, as {"text", "score_mib", "draftable"}; None for a finding with no number (creep, an OOM with nothing to propose)."""
    f = e.get("finding")
    parts = []
    if f in ("memory-under-request", "memory-over-request"):
        if e.get("proposed_request_mib") is not None and e.get("proposed_request_mib") != e.get("request_mib"):
            parts.append(f"memory request {_mib(e['request_mib'])} → {_mib(e['proposed_request_mib'])}")
        if e.get("proposed_limit_mib") is not None:
            parts.append(f"memory limit {_mib(e['limit_mib'])} → {_mib(e['proposed_limit_mib'])}")
        score = (e.get("freed_bytes") or 0) / rightsizing.MIB if f == "memory-over-request" else -(e.get("added_bytes") or 0) / rightsizing.MIB
    elif f == "memory-over-limit":
        parts.append(f"memory limit {_mib(e['limit_mib'])} → {_mib(e['proposed_limit_mib'])}")
        score = (e.get("freed_bytes") or 0) / rightsizing.MIB
    elif f in ("cpu-under-request", "cpu-over-request"):
        parts.append(f"CPU request {_m(e['request_millicores'])} → {_m(e['proposed_request_millicores'])}")
        delta = (e.get("freed_millicores") if f == "cpu-over-request" else -(e["proposed_request_millicores"] - e["request_millicores"])) or 0
        score = delta   # 1m of CPU ranks like 1 MiB: the goal of 10i is memory, so a CPU trim only leads when no memory trim of that size exists
    else:
        return None
    if not parts:
        return None
    return {"text": ", ".join(parts), "score_mib": round(score, 1)}


def controller_of(target: str) -> str:
    return "/".join(target.split("/")[:3])


class Digests:
    def __init__(self, db: sqlite3.Connection, lock: threading.RLock, clock: Callable[[], float], audit: Callable[..., None],
                 fc: forecast_store.Forecasts, read_current: Callable[[], dict | None], load_cfg: Callable[[], dict],
                 results: Callable[[], list] | None = None):
        self.db, self.lock, self.clock, self.audit, self.fc = db, lock, clock, audit, fc
        self.read_current, self.load_cfg, self.results = read_current, load_cfg, results
        self.db.executescript(SCHEMA)

    def now(self) -> int:
        return int(self.clock())

    # -- cadence ------------------------------------------------------------------------------------------------------
    def cadence(self) -> str:
        with self.lock:
            r = self.db.execute("SELECT value FROM rightsizing_settings WHERE key='cadence'").fetchone()
        if r and r["value"] in PERIOD_DAYS:
            return r["value"]
        return self.load_cfg()["digest"]["cadence"]

    def set_cadence(self, value: str, by: str) -> dict:
        if value not in PERIOD_DAYS:
            raise Refused(400, f"cadence must be one of {', '.join(PERIOD_DAYS)}")
        with self.lock:
            self.db.execute("INSERT INTO rightsizing_settings(key, value, set_by, set_at) VALUES ('cadence', ?, ?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, set_by=excluded.set_by, set_at=excluded.set_at", (value, str(by)[:40], self.now()))
        self.audit("rightsizing_cadence", cadence=value, by=str(by)[-4:])
        return self.status()

    def status(self) -> dict:
        with self.lock:
            last = self.db.execute("SELECT id, created_at, posted_at FROM rightsizing_digests ORDER BY id DESC LIMIT 1").fetchone()
        cad = self.cadence()
        nxt = (last["created_at"] + PERIOD_DAYS[cad] * 86400) if last else None
        return {"cadence": cad, "period_days": PERIOD_DAYS[cad], "last_digest": dict(last) if last else None, "next_due_at": nxt}

    # -- build --------------------------------------------------------------------------------------------------------
    def _snapshot(self) -> tuple[dict, float]:
        cur = self.read_current() or {}
        block = cur.get("rightsizing") or {}
        snap = block.get("snapshot")
        if not snap:
            raise Refused(409, "no rightsizing pass has produced a snapshot yet (the forecast job runs it daily)")
        return snap, float(block.get("ts") or snap.get("as_of") or 0)

    def _suggestions(self, rows: list, cfg: dict) -> list:
        out = []
        for r in rows:
            e = (r.get("evidence") or {}).get("evidence") or {}
            p = proposal(e)
            if p is None:
                continue
            if r.get("label") == "noise":
                now_v, then_v = rightsizing.finding_value(e), r.get("label_value")
                if now_v is not None and then_v and abs(now_v - then_v) / then_v <= NOISE_MOVE:
                    continue
            under = e["finding"] in ("memory-under-request", "cpu-under-request")
            out.append({"forecast_id": r["id"], "target": r["target"], "finding": e["finding"], "confidence": r.get("confidence"), "summary": p["text"],
                        "score_mib": p["score_mib"], "under": under, "oomkilled": bool(e.get("oomkilled")), "draftable": bool(r.get("draftable")),
                        "pr": r.get("pr"), "label": r.get("label"),
                        "evidence": {k: v for k, v in e.items() if k != "finding"}})
        # under-requests first (a hazard before a saving), then by what the change frees, then by name for a stable order
        out.sort(key=lambda s: (0 if s["under"] else 1, -s["score_mib"], s["target"]))
        return out[:MAX_SUGGESTIONS]

    def _tuning(self, rows: list) -> list:
        out = []
        for r in rows:
            e = (r.get("evidence") or {}).get("evidence") or {}
            if e.get("finding") == "memory-under-request" and proposal(e) is None:   # an OOMKilled container with nothing to propose: look at it
                out.append({"forecast_id": r["id"], "target": r["target"], "label": r.get("label"), "text": str(e.get("note") or "OOMKilled; no number is proposed.")})
                continue
            if e.get("finding") != "memory-creep":
                continue
            out.append({"forecast_id": r["id"], "target": r["target"], "label": r.get("label"),
                        "text": f"The daily peak is rising about {e.get('slope_mib_per_day')} MiB a day ({e.get('rise_mib')} MiB over {e.get('days')} days, now "
                                f"{e.get('latest_daily_peak_mib')} MiB; request {e.get('request_mib')} MiB, limit {e.get('limit_mib') or 'none'}). "
                                "A leak, a cache without a bound or a missing heap limit are the usual causes."})
        return out[:MAX_TUNING]

    def _scoreboard(self, workers: list, prev: dict | None, base: dict | None) -> list:
        def pick(d: dict | None, node: str) -> dict:
            return next((w for w in (d or {}).get("scoreboard", []) if w["node"] == node), {})

        out = []
        for w in workers:
            row = {"node": w["node"], "memory_requested_mib": w.get("memory_requested_mib"), "memory_requested_pct": w.get("memory_requested_pct"),
                   "memory_limits_mib": w.get("memory_limits_mib"), "memory_used_mib": w.get("memory_used_mib"),
                   "cpu_requested_millicores": w.get("cpu_requested_millicores"), "cpu_requested_pct": w.get("cpu_requested_pct"),
                   "vmui": VMUI + urllib.parse.quote(f'sum(kube_pod_container_resource_requests{{node="{w["node"]}",resource="memory"}})', safe="")}
            for name, ref in (("vs_last", pick(prev, w["node"])), ("vs_baseline", pick(base, w["node"]))):
                row[name] = {k: round(row[k] - ref[k], 1) for k in ("memory_requested_mib", "memory_limits_mib", "memory_used_mib", "cpu_requested_millicores")
                             if isinstance(row.get(k), (int, float)) and isinstance(ref.get(k), (int, float))} or None
            out.append(row)
        return out

    def build(self, trigger: str, by: str = "") -> dict:
        cfg = self.load_cfg()
        snap, snap_ts = self._snapshot()
        with self.lock:
            rows = self.fc.list(("open",), kind="rightsizing")
            prev = self.db.execute("SELECT data_json FROM rightsizing_digests ORDER BY id DESC LIMIT 1").fetchone()
            base = self.db.execute("SELECT data_json FROM rightsizing_digests ORDER BY id LIMIT 1").fetchone()
            prev_d, base_d = (json.loads(prev["data_json"]) if prev else None), (json.loads(base["data_json"]) if base else None)
            cadence = self.cadence()
            data = {"trigger": trigger, "cadence": cadence, "period_days": PERIOD_DAYS[cadence], "baseline": base is None, "as_of": int(snap_ts),
                    "scoreboard": self._scoreboard(snap.get("workers", []), prev_d, base_d),
                    "suggestions": self._suggestions(rows, cfg), "tuning": self._tuning(rows),
                    "results": self.results() if self.results else [],
                    "coverage": {"controllers": snap.get("controllers"), "with_vpa": snap.get("controllers_with_vpa"),
                                 "without_vpa": snap.get("controllers_without_vpa", []), "suppressed": snap.get("suppressed", {}),
                                 "oldest_vpa_sample_days": snap.get("oldest_vpa_sample_days"), "open_findings": len(rows)}}
            cur = self.db.execute("INSERT INTO rightsizing_digests(created_at, trigger, cadence, data_json) VALUES (?,?,?,?)",
                                  (self.now(), trigger, cadence, json.dumps(data, sort_keys=True)))
            did = cur.lastrowid
        self.audit("rightsizing_digest", digest=did, trigger=trigger, suggestions=len(data["suggestions"]), by=str(by)[-4:])
        return self.get(did)

    # -- the bot's side -----------------------------------------------------------------------------------------------
    def get(self, did: int) -> dict:
        with self.lock:
            r = self.db.execute("SELECT * FROM rightsizing_digests WHERE id=?", (int(did),)).fetchone()
        if r is None:
            raise Refused(404, f"no digest {did}")
        return {"id": r["id"], "created_at": r["created_at"], "message_ref": r["message_ref"], "thread_id": r["thread_id"], "posted_at": r["posted_at"],
                **json.loads(r["data_json"])}

    def latest(self) -> dict:
        with self.lock:
            r = self.db.execute("SELECT id FROM rightsizing_digests ORDER BY id DESC LIMIT 1").fetchone()
        if r is None:
            raise Refused(404, "no digest has been built yet")
        return self.get(r["id"])

    def _unposted(self) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT id FROM rightsizing_digests WHERE message_ref IS NULL ORDER BY id LIMIT 1").fetchone()
        return self.get(r["id"]) if r else None

    def tick(self) -> dict:
        """What the bot asks every few minutes: an unposted digest (a retry), a new one when the cadence says it is due, else why not."""
        pending = self._unposted()
        if pending is not None:
            return {"digest": pending, "why": "unposted"}
        try:
            snap, snap_ts = self._snapshot()
        except Refused as e:
            return {"digest": None, "why": "no-snapshot", "detail": e.message}
        cfg = self.load_cfg()
        if self.now() - snap_ts > STALE_PASS_SECONDS:
            return {"digest": None, "why": "stale-pass", "detail": "the daily rightsizing pass has not run for over 36 hours"}
        oldest = snap.get("oldest_vpa_sample_days")
        if oldest is None or oldest < cfg["vpa"]["min_sample_age_days"]:
            return {"digest": None, "why": "vpa-immature", "detail": f"the VPA recommendations are {oldest} day(s) old; the first digest waits for {cfg['vpa']['min_sample_age_days']}"}
        with self.lock:
            last = self.db.execute("SELECT created_at FROM rightsizing_digests ORDER BY id DESC LIMIT 1").fetchone()
        period = PERIOD_DAYS[self.cadence()] * 86400
        if last is not None and self.now() - last["created_at"] < period - 3600:
            return {"digest": None, "why": "not-due", "next_due_at": last["created_at"] + period}
        return {"digest": self.build("schedule"), "why": "due"}

    def build_now(self, by: str) -> dict:
        """`/aiops rightsizing now`: one on demand, even when the VPA is still young (the digest says how old it is)."""
        return {"digest": self._unposted() or self.build("operator", by), "why": "operator"}

    def set_message(self, did: int, message_ref: str, thread_id: str = "") -> dict:
        with self.lock:
            self.get(did)
            self.db.execute("UPDATE rightsizing_digests SET message_ref=?, thread_id=?, posted_at=? WHERE id=?", (str(message_ref)[:40], str(thread_id)[:40] or None, self.now(), int(did)))
        return self.get(did)
