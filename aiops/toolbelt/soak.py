"""Phase 10f soak: scheduled fault injection on the canaries (the Toolbelt's own job, never a model's).

The 14-day autonomy soak needs faults to heal, and nothing makes them on its own. This scheduler stops ONE allow-listed unit on ONE
canary every `interval_seconds` (registry `soak:` section), then waits for the loop under test, `restart-failed-unit`, to heal it:

    injecting -> waiting -> healed                       (autonomy, or anything else, restarted the unit before the deadline)
                         \\-> restoring -> restored      (nothing healed it by the deadline: the scheduler puts the unit back itself = a MISS)
                 \\-> failed                              (the injection or its restore did not complete)

It acts through the same executor as everything else: proposals of the internal action `canary-fault` (source `soak`, which no agent can
use), approved by the scheduler itself as `soak` (not `auto:*`, so an injected fault never counts against the autonomy limits or the
breaker, and the bot never posts a card for it: no thread). Gates, every pass: the registry scope and its end date, `autonomy` ON,
kill switch / maintenance / breaker clear, the Semaphore template applied, no open injection, the interval and the daily cap, and a
target that is quiet (no firing alert, nothing pending or running on it). Anything off simply waits; it never injects "anyway".

Evidence lands in `soak_injections` and in `/aiops report` (`soak` block): injections, outcomes, who healed, the median time to heal.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
import threading
import time
from dataclasses import dataclass

import actions

ACTION = "canary-fault"
OPEN = ("injecting", "waiting", "restoring")
HEALERS = ("restart-unit",)  # the registry action that heals the injected fault

SCHEMA = """
CREATE TABLE IF NOT EXISTS soak_injections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  host TEXT NOT NULL, unit TEXT NOT NULL,
  state TEXT NOT NULL,
  injected_proposal INTEGER, restore_proposal INTEGER, heal_proposal INTEGER,
  created_at INTEGER NOT NULL, injected_at INTEGER, deadline_at INTEGER, finished_at INTEGER,
  outcome TEXT, healed_by TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS soak_state ON soak_injections(state, created_at);
"""


@dataclass(frozen=True)
class SoakConfig:
    hosts: tuple
    units: tuple
    interval_seconds: int
    max_per_day: int
    heal_deadline_seconds: int
    ends: dt.date

    @classmethod
    def from_registry(cls, data: dict) -> "SoakConfig | None":
        s = data.get("soak")
        if not s:
            return None
        return cls(tuple(s["hosts"]), tuple(s["units"]), int(s["interval_seconds"]), int(s["max_per_day"]),
                   int(s["heal_deadline_seconds"]), dt.date.fromisoformat(s["ends"]))


class Soak:
    def __init__(self, engine: "actions.Engine", cfg: SoakConfig):
        self.eng, self.cfg = engine, cfg
        self.db, self.lock = engine.db, engine.lock
        self.db.executescript(SCHEMA)
        self._last_skip: dict[str, int] = {}
        engine.soak = self  # report() reads it

    # -- loop ----------------------------------------------------------------------------------------------
    def start(self, poll_seconds: int = 60) -> None:
        threading.Thread(target=self._loop, args=(poll_seconds,), daemon=True, name="soak-scheduler").start()

    def _loop(self, poll: int) -> None:
        while True:
            time.sleep(poll)
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - the loop must survive anything
                self.eng.audit("error", where="soak.tick", error=type(e).__name__)

    # -- one pass ------------------------------------------------------------------------------------------
    def tick(self) -> dict:
        """Progress the open injection, else start one if every gate is open. Returns what happened (tests and the audit read it)."""
        with self.lock:
            row = self.db.execute("SELECT * FROM soak_injections WHERE state IN ('injecting','waiting','restoring') ORDER BY id LIMIT 1").fetchone()
        if row is not None:
            return self._progress(row)
        why = self._blocked()
        if why is not None:
            return self._skip(why)
        return self._start()

    def _blocked(self) -> str | None:
        e, now = self.eng, self.eng.now()
        if dt.datetime.fromtimestamp(now, dt.timezone.utc).date() > self.cfg.ends:
            return "ended"
        if not e.flag("autonomy"):
            return "autonomy-off"
        for flag, why in (("kill_switch", "kill-switch"), ("maintenance", "maintenance"), ("autonomy_breaker", "breaker-open")):
            if e.flag(flag):
                return why
        if e.cfg.semaphore is None:
            return "no-executor"
        if not e.reg.get(ACTION).get("semaphore", {}).get("applied", False):
            return "not-applied"
        with self.lock:
            last = self.db.execute("SELECT MAX(created_at) FROM soak_injections").fetchone()[0]
            day = now - now % 86400
            today = self.db.execute("SELECT COUNT(*) FROM soak_injections WHERE created_at>=?", (day,)).fetchone()[0]
        if last is not None and now - last < self.cfg.interval_seconds:
            return "interval"
        if today >= self.cfg.max_per_day:
            return "day-cap"
        return None

    def _skip(self, why: str) -> dict:
        now = self.eng.now()
        if why not in ("interval", "day-cap") and now - self._last_skip.get(why, 0) >= 3600:  # waiting for the next slot is not news
            self._last_skip[why] = now
            self.eng.audit("soak_skipped", reason=why)
        return {"did": "skipped", "reason": why}

    # -- choosing and starting -------------------------------------------------------------------------------
    def _quiet(self, host: str, now: int) -> bool:
        """No firing alert on the host, nothing pending/approved/running aimed at it."""
        with self.lock:
            busy = self.db.execute("SELECT 1 FROM proposals WHERE target=? AND state IN ('pending','approved','running') LIMIT 1", (host,)).fetchone()
            try:
                firing = self.db.execute("SELECT 1 FROM alerts WHERE host=? AND status='firing' AND last_seen>? LIMIT 1", (host, now - 6 * 3600)).fetchone()
            except Exception:  # noqa: BLE001 - no alerts table (the executor tests): nothing is firing
                firing = None
        return busy is None and firing is None

    def _pick(self, now: int) -> tuple[str, str] | None:
        with self.lock:
            last = {r["host"]: r["last"] for r in self.db.execute("SELECT host, MAX(created_at) last FROM soak_injections GROUP BY host")}
            count = {r["host"]: r["n"] for r in self.db.execute("SELECT host, COUNT(*) n FROM soak_injections GROUP BY host")}
        hosts = sorted((h for h in self.cfg.hosts if self._quiet(h, now)), key=lambda h: (last.get(h, 0), h))
        if not hosts:
            return None
        h = hosts[0]
        return h, self.cfg.units[count.get(h, 0) % len(self.cfg.units)]

    def _start(self) -> dict:
        e, now = self.eng, self.eng.now()
        pick = self._pick(now)
        if pick is None:
            return self._skip("no-quiet-canary")
        host, unit = pick
        params = {"target_host": host, "unit": unit, "mode": "stop"}
        try:
            p = e.propose(action_id=ACTION, params=params, source="soak", reason=f"scheduled soak injection: stop {unit} on {host}")
        except actions.Refused as ex:
            why = (ex.detail or {}).get("problems", [ex.message])[0]
            return self._skip("refused: " + str(why)[:100])
        with self.lock:
            rid = self.db.execute("INSERT INTO soak_injections(host, unit, state, injected_proposal, created_at) VALUES (?,?,'injecting',?,?)",
                                  (host, unit, p["id"], now)).lastrowid
        try:
            e.decide(p["id"], "approve", by="soak", ref="schedule", system=True)
        except actions.Refused as ex:  # the kill switch engaged between the gate and now: nothing ran
            self._finish(rid, "failed", "not-approved", note=ex.message[:100])
            return {"did": "failed", "injection": rid, "reason": ex.message}
        e.audit("soak_injection_started", injection=rid, proposal=p["id"], host=host, unit=unit)
        return {"did": "started", "injection": rid, "host": host, "unit": unit, "proposal": p["id"]}

    # -- following an injection ----------------------------------------------------------------------------
    def _finish(self, rid: int, state: str, outcome: str, healed_by: str | None = None, heal_proposal: int | None = None, note: str = "") -> None:
        now = self.eng.now()
        with self.lock:
            self.db.execute("UPDATE soak_injections SET state=?, outcome=?, healed_by=?, heal_proposal=?, finished_at=?, note=? WHERE id=?",
                            (state, outcome, healed_by, heal_proposal, now, note[:200], rid))
        self.eng.audit("soak_injection_finished", injection=rid, state=state, outcome=outcome, healed_by=healed_by)

    def _proposal(self, pid: int | None):
        with self.lock:
            return self.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone() if pid else None

    def _progress(self, row) -> dict:
        now = self.eng.now()
        if row["state"] == "injecting":
            p = self._proposal(row["injected_proposal"])
            st = p["state"] if p is not None else "missing"
            if st in ("pending", "approved", "running"):
                if now - row["created_at"] > 1800:  # nothing so slow is still an injection
                    self._finish(row["id"], "failed", "stuck", note=f"the stop proposal was still {st} after 30 minutes")
                    return {"did": "failed", "injection": row["id"], "reason": "stuck"}
                return {"did": "waiting", "injection": row["id"], "reason": f"stop is {st}"}
            if st == "succeeded":
                at = p["finished_at"] or now  # the moment the unit stopped, not when this pass noticed: a heal is measured from it
                with self.lock:
                    self.db.execute("UPDATE soak_injections SET state='waiting', injected_at=?, deadline_at=? WHERE id=?",
                                    (at, at + self.cfg.heal_deadline_seconds, row["id"]))
                return {"did": "injected", "injection": row["id"]}
            if st in ("failed", "verify_failed"):  # the unit may be half stopped: put it back
                return self._restore(row, f"the stop proposal ended {st}")
            self._finish(row["id"], "failed", st, note="the stop never ran")  # cancelled / rejected / expired / skipped: nothing to undo
            return {"did": "failed", "injection": row["id"], "reason": st}
        if row["state"] == "waiting":
            heal = self._healed(row)
            if heal is not None:
                # `skipped` = the precheck found the unit already active: the world healed itself, whoever proposed the restart
                by = "external" if heal["state"] == "skipped" else ("autonomy" if (heal["decided_by"] or "").startswith("auto:") else "operator")
                self._finish(row["id"], "healed", "healed", healed_by=by, heal_proposal=heal["id"])
                return {"did": "healed", "injection": row["id"], "by": by, "seconds": now - row["injected_at"]}
            if now >= row["deadline_at"]:
                return self._restore(row, "nothing healed it before the deadline")
            return {"did": "waiting", "injection": row["id"], "reason": "fault live"}
        # restoring
        p = self._proposal(row["restore_proposal"])
        st = p["state"] if p is not None else "missing"
        if st in ("pending", "approved", "running"):
            return {"did": "waiting", "injection": row["id"], "reason": f"restore is {st}"}
        if st == "succeeded":
            was = False
            try:
                steps = (json.loads(p["result_json"] or "{}").get("steps") or [])
                was = bool(steps and (steps[-1].get("result") or {}).get("was_in_state"))
            except (ValueError, TypeError, AttributeError):
                pass
            if (row["note"] or "").startswith("the stop proposal ended"):  # the injection itself broke: this restore is cleanup, not a verdict on autonomy
                self._finish(row["id"], "failed", "inject-failed", note=row["note"])
                return {"did": "failed", "injection": row["id"], "reason": "inject-failed"}
            if was:  # the unit was already active when we went to restore it: something healed it, we just did not see which proposal
                self._finish(row["id"], "healed", "healed", healed_by="external", heal_proposal=None, note="already active at the deadline")
                return {"did": "healed", "injection": row["id"], "by": "external"}
            self._finish(row["id"], "restored", "missed", note="autonomy did not heal it before the deadline; restored by the scheduler")
            return {"did": "restored", "injection": row["id"]}
        self._finish(row["id"], "failed", "restore-failed", note=f"the restore proposal ended {st}: the unit may still be stopped")
        self.eng.audit("soak_restore_failed", injection=row["id"], host=row["host"], unit=row["unit"], state=st)
        return {"did": "failed", "injection": row["id"], "reason": "restore-failed"}

    def _healed(self, row):
        """A restart of the injected unit's host that finished after the injection: the evidence the loop (or a human) healed it."""
        with self.lock:
            return self.db.execute(
                "SELECT * FROM proposals WHERE action_id IN (%s) AND target=? AND state IN ('succeeded','skipped') AND finished_at>=? AND replay=0 "
                "ORDER BY finished_at LIMIT 1" % ",".join("?" * len(HEALERS)), (*HEALERS, row["host"], row["injected_at"] or row["created_at"])).fetchone()

    def _restore(self, row, why: str) -> dict:
        e = self.eng
        params = {"target_host": row["host"], "unit": row["unit"], "mode": "restore"}
        try:
            p = e.propose(action_id=ACTION, params=params, source="soak", reason=f"scheduled soak: restore {row['unit']} on {row['host']} ({why})"[:300])
            e.decide(p["id"], "approve", by="soak", ref="schedule", system=True)
        except actions.Refused as ex:  # the kill switch is on, or the template went away: try again on the next pass
            self._skip("restore-refused: " + ex.message[:80])
            return {"did": "waiting", "injection": row["id"], "reason": "restore refused: " + ex.message}
        with self.lock:
            self.db.execute("UPDATE soak_injections SET state='restoring', restore_proposal=?, note=? WHERE id=?", (p["id"], why[:200], row["id"]))
        e.audit("soak_restore_started", injection=row["id"], proposal=p["id"], why=why[:100])
        return {"did": "restoring", "injection": row["id"], "proposal": p["id"]}

    # -- evidence ------------------------------------------------------------------------------------------
    def report(self, since: int) -> dict:
        with self.lock:
            rows = self.db.execute("SELECT * FROM soak_injections WHERE created_at>=? ORDER BY id", (since,)).fetchall()
        by_outcome: dict = {}
        healed_by: dict = {}
        secs = []
        for r in rows:
            by_outcome[r["outcome"] or r["state"]] = by_outcome.get(r["outcome"] or r["state"], 0) + 1
            if r["outcome"] == "healed":
                healed_by[r["healed_by"] or "?"] = healed_by.get(r["healed_by"] or "?", 0) + 1
                if r["injected_at"] and r["finished_at"]:
                    secs.append(r["finished_at"] - r["injected_at"])
        open_ = next((r for r in rows if r["state"] in OPEN), None)
        last = rows[-1] if rows else None
        return {"ends": self.cfg.ends.isoformat(), "interval_seconds": self.cfg.interval_seconds, "injections": len(rows),
                "by_outcome": by_outcome, "healed_by": healed_by,
                "median_heal_seconds": int(statistics.median(secs)) if secs else None,
                "open": {"host": open_["host"], "unit": open_["unit"], "state": open_["state"]} if open_ else None,
                "last": {"host": last["host"], "unit": last["unit"], "state": last["state"], "outcome": last["outcome"], "at": last["created_at"]} if last else None}
