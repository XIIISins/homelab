"""Forecast findings as rows the operator can act on (Phase 10h1): the Toolbelt's `forecasts` table, its event feed and its labels.

The forecast job (forecast_run.py, its own unit) writes every finding that holds RIGHT NOW to a JSON file each pass. This store
reads that file (`sync`) and keeps the history the bot needs: which findings are new, which got materially worse, which have
gone, and what the operator thought of them. It posts nothing itself and can never page: the bot turns `forecast_events` into
quiet cards (Useful / Noise buttons); a forecast is a heads-up, never an alert.

Rules (docs/operations/10h-predictive-change.md):
  * a finding is identified by its fingerprint (`forecast:<kind>:<metric>:<target>`); one row per fingerprint, `open` or `resolved`;
  * `created` on first sight (and again when a resolved one comes back); `escalated` when the ETA is at most half of what was last
    announced; `reposted` once a week while it stays open and unlabelled-noise; `resolved` after two passes without it;
  * a stale file (the job died) never resolves anything: absence is only evidence when the job is alive;
  * at most `max_new_per_sync` new findings are announced per sync, so a first run cannot flood the channel.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL, metric TEXT NOT NULL, target TEXT NOT NULL,
  state TEXT NOT NULL,
  confidence TEXT, days_to_full REAL, ratio REAL, evidence_json TEXT NOT NULL,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, missing INTEGER NOT NULL DEFAULT 0,
  posted_at INTEGER, posted_days REAL,
  label TEXT, labeled_by TEXT, labeled_at INTEGER,
  message_ref TEXT, thread_id TEXT
);
CREATE TABLE IF NOT EXISTS forecast_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, forecast_id INTEGER NOT NULL, ts INTEGER NOT NULL, kind TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}'
);
"""
LABELS = ("useful", "noise")
REPOST_AFTER = 7 * 86400


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message, self.detail = status, message, {}


@dataclass
class ForecastConfig:
    max_age_seconds: int = 6 * 3600     # a findings file older than this means the job is not running
    max_new_per_sync: int = 8
    resolve_after_missing: int = 2


class Forecasts:
    def __init__(self, db: sqlite3.Connection, lock: threading.RLock, clock, audit, cfg: ForecastConfig | None = None):
        self.db, self.lock, self.clock, self.audit, self.cfg = db, lock, clock, audit, cfg or ForecastConfig()
        self.db.executescript(SCHEMA)

    def now(self) -> int:
        return int(self.clock())

    # -- views --------------------------------------------------------------------------------------------------------
    def view(self, fid: int) -> dict:
        r = self.db.execute("SELECT * FROM forecasts WHERE id=?", (fid,)).fetchone()
        if r is None:
            raise Refused(404, f"no forecast {fid}")
        d = {k: r[k] for k in r.keys() if k != "evidence_json"}
        d["evidence"] = json.loads(r["evidence_json"])
        d["eta_at"] = int(r["last_seen"] + r["days_to_full"] * 86400) if r["days_to_full"] is not None else None
        return d

    def list(self, states: tuple = ("open",)) -> list[dict]:
        q = ",".join("?" * len(states))
        with self.lock:
            ids = [r["id"] for r in self.db.execute(f"SELECT id FROM forecasts WHERE state IN ({q}) ORDER BY COALESCE(days_to_full, 9999), id", states).fetchall()]
            return [self.view(i) for i in ids]

    def get(self, fid: int) -> dict:
        with self.lock:
            return self.view(fid)

    def feed(self, after: int = 0, limit: int = 50) -> dict:
        with self.lock:
            rows = self.db.execute("SELECT * FROM forecast_events WHERE id>? ORDER BY id LIMIT ?", (int(after), min(int(limit), 200))).fetchall()
            events = [{"id": r["id"], "kind": r["kind"], "ts": r["ts"], "data": json.loads(r["data_json"]), "forecast": self.view(r["forecast_id"])} for r in rows]
        return {"events": events, "next": events[-1]["id"] if events else int(after)}

    def summary(self) -> dict:
        with self.lock:
            rows = self.db.execute("SELECT state, COUNT(*) n FROM forecasts GROUP BY state").fetchall()
            lab = self.db.execute("SELECT label, COUNT(*) n FROM forecasts WHERE label IS NOT NULL GROUP BY label").fetchall()
        return {"by_state": {r["state"]: r["n"] for r in rows}, "labels": {r["label"]: r["n"] for r in lab}}

    # -- the bot's writes ---------------------------------------------------------------------------------------------
    def label(self, fid: int, label: str, by: str) -> dict:
        if label not in LABELS:
            raise Refused(400, f"label must be one of {', '.join(LABELS)}")
        with self.lock:
            self.view(fid)
            self.db.execute("UPDATE forecasts SET label=?, labeled_by=?, labeled_at=? WHERE id=?", (label, str(by)[:40], self.now(), fid))
            self._event(fid, "labeled", {"label": label})
        self.audit("forecast_labeled", forecast=fid, label=label)
        return self.get(fid)

    def set_message(self, fid: int, message_ref: str, thread_id: str = "") -> dict:
        with self.lock:
            self.view(fid)
            self.db.execute("UPDATE forecasts SET message_ref=?, thread_id=? WHERE id=?", (str(message_ref)[:40], str(thread_id)[:40] or None, fid))
        return self.get(fid)

    # -- ingest -------------------------------------------------------------------------------------------------------
    def _event(self, fid: int, kind: str, data: dict | None = None) -> None:
        self.db.execute("INSERT INTO forecast_events(forecast_id, ts, kind, data_json) VALUES (?,?,?,?)", (fid, self.now(), kind, json.dumps(data or {}, sort_keys=True)))

    def sync(self, current: dict) -> dict:
        """Apply one findings file ({ts, findings: [...], stats}). Returns counts for the audit line."""
        now = self.now()
        ts = float(current.get("ts") or 0)
        findings = [f for f in current.get("findings", []) if isinstance(f, dict) and f.get("fingerprint")]
        out = {"created": 0, "escalated": 0, "reposted": 0, "resolved": 0, "seen": len(findings), "stale": False, "deferred": 0}
        if now - ts > self.cfg.max_age_seconds:
            out["stale"] = True
            self.audit("forecast_sync_stale", age_hours=round((now - ts) / 3600, 1))
            return out
        new_budget = self.cfg.max_new_per_sync
        with self.lock:
            present = set()
            for f in findings:
                fp = f["fingerprint"]
                present.add(fp)
                row = self.db.execute("SELECT * FROM forecasts WHERE fingerprint=?", (fp,)).fetchone()
                days = f.get("days_to_full")
                ev = json.dumps({"evidence": f.get("evidence", {}), "ratio": f.get("ratio")}, sort_keys=True, default=str)
                if row is None or row["state"] == "resolved":
                    if new_budget <= 0:
                        out["deferred"] += 1  # announced on a later sync
                        continue
                    new_budget -= 1
                    if row is None:
                        cur = self.db.execute(
                            "INSERT INTO forecasts(fingerprint, kind, metric, target, state, confidence, days_to_full, ratio, evidence_json, first_seen, last_seen, posted_at, posted_days)"
                            " VALUES (?,?,?,?, 'open', ?,?,?,?,?,?,?,?)",
                            (fp, f.get("kind", "?"), f.get("metric", "?"), f.get("target", "?"), f.get("confidence"), days, f.get("ratio"), ev, now, now, now, days))
                        fid = cur.lastrowid
                    else:  # it came back: a new occurrence, with a clean slate for the card and the label
                        fid = row["id"]
                        self.db.execute("UPDATE forecasts SET state='open', confidence=?, days_to_full=?, ratio=?, evidence_json=?, first_seen=?, last_seen=?, missing=0, posted_at=?, posted_days=?,"
                                        " label=NULL, labeled_by=NULL, labeled_at=NULL, message_ref=NULL, thread_id=NULL WHERE id=?", (f.get("confidence"), days, f.get("ratio"), ev, now, now, now, days, fid))
                    self._event(fid, "created", {"days_to_full": days})
                    out["created"] += 1
                    continue
                fid = row["id"]
                self.db.execute("UPDATE forecasts SET confidence=?, days_to_full=?, ratio=?, evidence_json=?, last_seen=?, missing=0 WHERE id=?", (f.get("confidence"), days, f.get("ratio"), ev, now, fid))
                if days is not None and row["posted_days"] is not None and days <= row["posted_days"] / 2:
                    self.db.execute("UPDATE forecasts SET posted_at=?, posted_days=? WHERE id=?", (now, days, fid))
                    self._event(fid, "escalated", {"from_days": row["posted_days"], "days_to_full": days})
                    out["escalated"] += 1
                elif row["label"] != "noise" and now - (row["posted_at"] or 0) >= REPOST_AFTER:
                    self.db.execute("UPDATE forecasts SET posted_at=?, posted_days=? WHERE id=?", (now, days, fid))
                    self._event(fid, "reposted", {"days_to_full": days})
                    out["reposted"] += 1
            for r in self.db.execute("SELECT id, missing FROM forecasts WHERE state='open'").fetchall():
                if self._fp(r["id"]) in present:
                    continue
                if r["missing"] + 1 >= self.cfg.resolve_after_missing:
                    self.db.execute("UPDATE forecasts SET state='resolved', missing=0 WHERE id=?", (r["id"],))
                    self._event(r["id"], "resolved", {})
                    out["resolved"] += 1
                else:
                    self.db.execute("UPDATE forecasts SET missing=missing+1 WHERE id=?", (r["id"],))
        if any(out[k] for k in ("created", "escalated", "reposted", "resolved")):
            self.audit("forecast_sync", **{k: v for k, v in out.items() if k != "stale"})
        return out

    def _fp(self, fid: int) -> str:
        return self.db.execute("SELECT fingerprint FROM forecasts WHERE id=?", (fid,)).fetchone()["fingerprint"]
