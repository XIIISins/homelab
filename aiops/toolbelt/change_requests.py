"""Phase 10h2: change requests, the only way a PR draft starts.

A change request (CR) is a row asking for ONE agent-authored pull request in a named class (aiops/author-classes.yml).
Who may do what:
  agent role (n8n)   create a CR (it lands `pending`); it can never approve one
  approver (bot)     create a CR from the operator's own command, list/feed, approve|reject|cancel (Discord user id)
  author (Frigg dispatcher)  claim the next approved CR, report its outcome, report an open PR as merged/closed
The Toolbelt never runs the author and never holds the GitHub token: the dispatcher on Frigg polls `claim`. That keeps
every cap here (kill switch, maintenance, concurrency, a daily budget, open-PR limit) enforceable in one place.

States: pending -> approved -> running -> pr-open -> merged|closed   (also rejected, cancelled, expired, failed, no-change)

A class marked `test_before_pr` (author-classes.yml) takes a detour: running -> testing -> pr-open. The dispatcher pushes the branch and reports
`testing`; the Toolbelt proposes the class's test (burst cluster or canary) for the branch head and, because the operator's Approve of the request
already said "draft this and test it", approves that ONE sha-pinned proposal on the operator's behalf. The dispatcher opens the PR when the test has
passed (with the result already in the description) or cannot run (the PR says "Not tested" and why); a FAILED test opens no PR at all: the request
fails with the summary and the branch is kept a few days for inspection.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "author"))
import scope  # noqa: E402  (aiops/author/scope.py: the same rules CI and the dispatcher use)

import actions  # noqa: E402
import pr_test
import tools  # noqa: E402
from actions import Refused  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS change_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  state TEXT NOT NULL,
  class TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, allowed_json TEXT NOT NULL,
  source TEXT NOT NULL, source_ref TEXT, created_by TEXT,
  decided_by TEXT, decided_at INTEGER, decision_ref TEXT,
  claimed_at INTEGER, finished_at INTEGER,
  branch TEXT, pr_url TEXT, summary TEXT, tests_json TEXT, error TEXT,
  message_ref TEXT, thread_id TEXT
);
CREATE INDEX IF NOT EXISTS cr_state ON change_requests(state);
CREATE TABLE IF NOT EXISTS change_request_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cr_id INTEGER NOT NULL, ts INTEGER NOT NULL, kind TEXT NOT NULL, data_json TEXT NOT NULL
);
"""

ACTIVE = ("pending", "approved", "running", "testing", "pr-open")
REPORT_STATES = ("pr-open", "failed", "no-change", "testing")
SOURCES = ("operator", "forecast", "incident", "chat", "drift")
_TITLE_MAX, _BODY_MAX, _SUMMARY_MAX = 120, 4000, 2000


@dataclass
class CRConfig:
    classes: dict = field(default_factory=dict)         # parsed aiops/author-classes.yml
    repo: str = "XIIISins/homelab"                      # owner/name a reported PR URL must belong to
    pending_ttl: int = 24 * 3600                        # a request nobody approved
    approved_ttl: int = 6 * 3600                        # approved but never claimed (dispatcher down)
    run_timeout: int = 40 * 60                          # the dispatcher's wall clock is 30 min; a longer claim is dead
    max_running: int = 2
    max_open_prs: int = 3
    fold_test_approval: bool = True                     # the operator's Approve of a `test_before_pr` request also approves its one sha-pinned test
    testing_timeout: int = 2 * 3600                     # a request stuck in `testing` (the dispatcher is down) fails after this; its branch stays
    max_started_per_day: int = 6                        # the author's daily budget (operator-tunable)
    max_created_per_day: int = 20
    max_pending: int = 8
    pr_stale_days: int = 14


def _was_transient(data: dict) -> bool:
    """A recorded test attempt that hit a temporary GitHub problem. Events written before the `transient` flag existed (the first
    live run, 2026-10-04) carry only the reason text, so a failure to READ from GitHub counts too."""
    return bool(data.get("transient")) or str(data.get("ineligible", "")).startswith("could not read")


class ChangeRequests:
    def __init__(self, engine: "actions.Engine", cfg: CRConfig):
        self.eng, self.cfg = engine, cfg
        self.on_merged = None   # optional fn(view dict): a request's PR was reported merged (10i5 starts the post-merge watch for rightsizing)
        self.db, self.lock, self.audit = engine.db, engine.lock, engine.audit
        self.db.executescript(SCHEMA)
        self._pr_rx = re.compile(rf"^https://github\.com/{re.escape(cfg.repo)}/pull/\d+$")

    # -- helpers -------------------------------------------------------------------------------------------
    def now(self) -> int:
        return self.eng.now()

    def _event(self, cid: int, kind: str, data: dict | None = None) -> None:
        self.db.execute("INSERT INTO change_request_events(cr_id, ts, kind, data_json) VALUES (?,?,?,?)",
                        (cid, self.now(), kind, json.dumps(data or {}, sort_keys=True)))

    def note(self, cid: int, kind: str, data: dict | None = None) -> None:
        """Record a free-form event on a request (10i5: the post-merge watch's verdict). It reaches the bot through the feed like any event."""
        with self.lock:
            self._row(cid)
            self._event(cid, kind, data)

    def _row(self, cid: int):
        r = self.db.execute("SELECT * FROM change_requests WHERE id=?", (cid,)).fetchone()
        if r is None:
            raise Refused(404, f"no change request {cid}")
        return r

    def _move(self, cid: int, state: str, kind: str, data: dict | None = None, **cols) -> None:
        sets = {"state": state, "updated_at": self.now(), **cols}
        self.db.execute(f"UPDATE change_requests SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), cid))
        self._event(cid, kind, data)

    def _count(self, *states: str) -> int:
        q = ",".join("?" * len(states))
        return self.db.execute(f"SELECT COUNT(*) n FROM change_requests WHERE state IN ({q})", states).fetchone()["n"]

    def _day_start(self) -> int:
        return self.now() - self.now() % 86400

    def get(self, cid: int) -> dict:
        self.sweep()
        return self.view(cid)

    def view(self, cid: int) -> dict:
        r = self._row(cid)
        d = {k: r[k] for k in r.keys() if k not in ("allowed_json", "tests_json")}
        d["allowed_paths"] = json.loads(r["allowed_json"])
        d["tests"] = json.loads(r["tests_json"]) if r["tests_json"] else None
        d["stale_pr"] = bool(r["state"] == "pr-open" and self.now() - r["updated_at"] > self.cfg.pr_stale_days * 86400)
        d["pr_test"] = self._pr_test(r) if r["state"] in ("testing", "pr-open", "merged", "closed") else None
        d["test_before_pr"] = self._test_before_pr(r["class"])
        d["blocked"] = None
        if r["state"] == "approved":  # waiting behind a cap: why, as of the last time the dispatcher asked
            ev = self.db.execute("SELECT ts, data_json FROM change_request_events WHERE cr_id=? AND kind='blocked' ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
            if ev:
                d["blocked"] = {**json.loads(ev["data_json"]), "since": ev["ts"]}
        return d

    def _pr_test(self, r) -> dict | None:
        """The canary-test status of this request's PR (None for a class that is not tested): from its proposal, else the reason it
        was not proposed."""
        if not self._tested(r["class"]):
            return None
        proposal = self.eng.pr_test_view(r["branch"]) if r["branch"] else None
        ev = self.db.execute("SELECT data_json FROM change_request_events WHERE cr_id=? AND kind='pr_test' ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
        data = json.loads(ev["data_json"]) if ev else {}
        reason = data.get("ineligible")
        out = pr_test.summarize(proposal, reason or ("the test has not been evaluated yet" if ev is None else None), _was_transient(data))
        if proposal is None and self._test_kind(r["class"]) == "burst":
            out["kind"] = "burst"   # a burst-tested class says so even before any test exists (the PR section's heading and wording)
        return out

    def _test_kind(self, cls: str) -> str | None:
        """How a class's PRs are proven: `canary` (roles on a canary), `burst` (k8s apps on a burst cluster) or None."""
        c = self.cfg.classes.get("classes", {}).get(cls, {})
        return "canary" if c.get("canary_test") else "burst" if c.get("burst_test") else None

    def _tested(self, cls: str) -> bool:
        return self._test_kind(cls) is not None

    def _test_before_pr(self, cls: str) -> bool:
        """The class is tested AND wants its result before a PR exists (a class flag; an untested class has nothing to wait for)."""
        return bool(self._tested(cls) and self.cfg.classes.get("classes", {}).get(cls, {}).get("test_before_pr"))

    # -- create --------------------------------------------------------------------------------------------
    def create(self, *, source: str, class_: str, title: str, body: str, allowed_paths=None, source_ref: str = "",
               created_by: str = "") -> dict:
        if source not in SOURCES:
            raise Refused(400, f"source must be one of {', '.join(SOURCES)}")
        cls = (self.cfg.classes.get("classes") or {}).get(class_)
        if cls is None:
            raise Refused(400, f"unknown class {class_!r}")
        if not cls.get("enabled"):
            raise Refused(409, f"class {class_!r} is not enabled")
        title, body = str(title or "").strip(), str(body or "").strip()
        if not title or len(title) > _TITLE_MAX:
            raise Refused(400, f"title is required and at most {_TITLE_MAX} characters")
        if not body or len(body) > _BODY_MAX:
            raise Refused(400, f"body is required and at most {_BODY_MAX} characters")
        allowed = list(allowed_paths) if allowed_paths else list(cls["allow"])
        if not isinstance(allowed, list) or len(allowed) > 20 or not all(isinstance(p, str) and 0 < len(p) <= 200 for p in allowed):
            raise Refused(400, "allowed_paths must be at most 20 path patterns")
        for p in allowed:
            # a request may narrow its class, never widen it: each pattern must sit inside the class and outside the deny list
            if scope.matches(p, self.cfg.classes["deny"]) or not scope.matches(p, cls["allow"]):
                raise Refused(400, f"allowed path {p!r} is outside class {class_!r}")
        with self.lock:
            self.sweep()
            if source_ref and self.db.execute(f"SELECT 1 FROM change_requests WHERE class=? AND source_ref=? AND state IN ({','.join('?' * len(ACTIVE))})",
                                              (class_, source_ref[:120], *ACTIVE)).fetchone():
                raise Refused(409, "an active change request already exists for that finding")
            if self._count("pending") >= self.cfg.max_pending:
                raise Refused(429, "too many change requests are waiting for a decision")
            made = self.db.execute("SELECT COUNT(*) n FROM change_requests WHERE created_at>=?", (self._day_start(),)).fetchone()["n"]
            if made >= self.cfg.max_created_per_day:
                raise Refused(429, "the daily change-request cap is reached")
            now = self.now()
            cur = self.db.execute(
                "INSERT INTO change_requests(created_at, updated_at, state, class, title, body, allowed_json, source, source_ref, created_by) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (now, now, "pending", class_, tools.redact(title), tools.redact(body), json.dumps(allowed), source,
                 source_ref[:120], str(created_by)[:80]))
            cid = cur.lastrowid
            self._event(cid, "created", {"class": class_, "source": source, "by": str(created_by)[:80]})
        self.audit("change_request_created", cr=cid, cls=class_, source=source)
        return self.view(cid)

    # -- decide --------------------------------------------------------------------------------------------
    def decide(self, cid: int, decision: str, *, by: str, ref: str = "") -> dict:
        if decision not in ("approve", "reject", "cancel"):
            raise Refused(400, "decision must be approve, reject or cancel")
        self.eng._check_operator(by)
        with self.lock:
            self.sweep()
            row = self._row(cid)
            st, now = row["state"], self.now()
            if decision == "cancel":
                if st not in ("pending", "approved", "running", "testing"):
                    raise Refused(409, f"change request {cid} is {st}, nothing to cancel")
                self._move(cid, "cancelled", "cancelled", {"by": by}, decided_by=by, decided_at=now, finished_at=now)
            else:
                if st != "pending":
                    raise Refused(409, f"change request {cid} is {st}, not pending")
                if decision == "approve":
                    if self.eng.flag("kill_switch"):
                        raise Refused(409, "the kill switch is engaged: nothing may be approved or run")
                    self._move(cid, "approved", "approved", {"by": by, "ref": ref[:80]}, decided_by=by, decided_at=now, decision_ref=ref[:80])
                else:
                    self._move(cid, "rejected", "rejected", {"by": by, "ref": ref[:80]}, decided_by=by, decided_at=now, finished_at=now)
        self.audit("change_request_" + {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[decision], cr=cid, by=by)
        return self.view(cid)

    def set_message(self, cid: int, message_ref: str, thread_id: str = "") -> dict:
        with self.lock:
            self._row(cid)
            self.db.execute("UPDATE change_requests SET message_ref=?, thread_id=COALESCE(NULLIF(?, ''), thread_id) WHERE id=?",
                            (message_ref[:80], thread_id[:80], cid))
            self._event(cid, "message_set", {"ref": message_ref[:80]})
        return self.view(cid)

    # -- the dispatcher's side -----------------------------------------------------------------------------
    def claim(self, by: str = "dispatcher") -> dict:
        """The oldest approved request, now `running`; {"change_request": None, "why": ...} when nothing may start."""
        with self.lock:
            self.sweep()
            if self.eng.flag("kill_switch"):
                return self._blocked("kill-switch", "the kill switch is engaged (`/aiops resume` lifts it)")
            if self.eng.flag("maintenance"):
                return self._blocked("maintenance", "maintenance mode is on")
            if self._count("running") >= self.cfg.max_running:
                return self._blocked("max-running", f"{self.cfg.max_running} drafting sessions are already running")
            if self._count("pr-open", "testing") >= self.cfg.max_open_prs:
                return self._blocked("open-pr-limit", f"{self.cfg.max_open_prs} agent PRs are already open: merge or close one")
            started = self.db.execute("SELECT COUNT(*) n FROM change_requests WHERE claimed_at>=?", (self._day_start(),)).fetchone()["n"]
            if started >= self.cfg.max_started_per_day:
                return self._blocked("daily-budget", f"the daily budget is used up ({started} of {self.cfg.max_started_per_day} drafts started today; it resets at 00:00 UTC)")
            row = self.db.execute("SELECT id FROM change_requests WHERE state='approved' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return {"change_request": None, "why": "none-approved"}
            cid = row["id"]
            self._move(cid, "running", "claimed", {"by": by}, claimed_at=self.now())
        self.audit("change_request_claimed", cr=cid, by=by)
        return {"change_request": self.view(cid), "why": "ok"}

    def _blocked(self, why: str, detail: str) -> dict:
        """A cap refused the claim. If an approved request is waiting, SAY SO on that request (once per reason): an operator who
        approved a card should never have to read journals to learn it is queued behind a limit. Caller holds the lock."""
        row = self.db.execute("SELECT id FROM change_requests WHERE state='approved' ORDER BY id LIMIT 1").fetchone()
        if row is not None:
            last = self.db.execute("SELECT data_json FROM change_request_events WHERE cr_id=? AND kind='blocked' ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
            if last is None or json.loads(last["data_json"]).get("why") != why:
                self._event(row["id"], "blocked", {"why": why, "detail": detail})
                self.audit("change_request_blocked", cr=row["id"], why=why)
        return {"change_request": None, "why": why, "detail": detail}

    def report(self, cid: int, *, state: str, pr_url: str = "", branch: str = "", summary: str = "", tests=None, error: str = "") -> dict:
        if state not in REPORT_STATES + ("merged", "closed"):
            raise Refused(400, f"state must be one of {', '.join(REPORT_STATES + ('merged', 'closed'))}")
        with self.lock:
            row = self._row(cid)
            cur, now = row["state"], self.now()
            if state in ("merged", "closed"):
                if cur != "pr-open":
                    raise Refused(409, f"change request {cid} is {cur}, not pr-open")
                self._move(cid, state, state, {}, finished_at=now)
            elif cur == "testing":   # the detour of a `test_before_pr` class: the PR opens, or the request ends (a failed test opens no PR)
                if state not in ("pr-open", "failed"):
                    raise Refused(409, f"change request {cid} is testing: it can only open its PR or fail")
                if state == "pr-open" and not self._pr_rx.match(pr_url or ""):
                    raise Refused(400, f"pr_url must be a pull request of {self.cfg.repo}")
                self._move(cid, state, "reported", {"state": state, "after": "testing"}, finished_at=now if state != "pr-open" else None,
                           pr_url=pr_url or row["pr_url"] or None, summary=tools.redact(str(summary))[:_SUMMARY_MAX] or row["summary"],
                           tests_json=json.dumps(tests) if tests is not None else row["tests_json"], error=tools.redact(str(error))[:500] or None)
            else:
                if cur != "running":
                    raise Refused(409, f"change request {cid} is {cur}, not running (cancelled or timed out?)")
                if state == "testing" and not self._test_before_pr(row["class"]):
                    raise Refused(409, f"class {row['class']!r} does not test before its PR")
                if state == "testing" and not branch:
                    raise Refused(400, "testing needs the pushed branch")
                if state == "pr-open" and not self._pr_rx.match(pr_url or ""):
                    raise Refused(400, f"pr_url must be a pull request of {self.cfg.repo}")
                self._move(cid, state, "reported", {"state": state}, finished_at=now if state not in ("pr-open", "testing") else None,
                           pr_url=pr_url or None, branch=str(branch)[:120] or None, summary=tools.redact(str(summary))[:_SUMMARY_MAX] or None,
                           tests_json=json.dumps(tests) if tests is not None else None, error=tools.redact(str(error))[:500] or None)
        self.audit("change_request_reported", cr=cid, state=state)
        if state == "testing":
            self._start_test(cid)
        if state == "pr-open" and not (cur == "testing" or self._test_before_pr(row["class"])):
            self._after_pr_open(cid)
        if state == "merged" and self.on_merged is not None:
            try:
                self.on_merged(self.view(cid))
            except Exception as e:  # noqa: BLE001 - the merge is recorded either way
                self.audit("error", where="on_merged", error=type(e).__name__)
        return self.view(cid)

    def retest(self, cid: int) -> dict:
        """Ask again for a test proposal for an open PR whose first attempt hit a TEMPORARY GitHub problem (HTTP 0/403/429/5xx).
        Only when no test exists for the branch and the last attempt was transient and at least two minutes old; the dispatcher
        calls this from its reconcile loop."""
        with self.lock:
            r = self._row(cid)
            ev = self.db.execute("SELECT ts, data_json FROM change_request_events WHERE cr_id=? AND kind='pr_test' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        if r["state"] not in ("pr-open", "testing") or not self._tested(r["class"]):
            raise Refused(409, "only an open PR (or a branch being tested) of a tested class (canary or burst) can be retested")
        if ev is None or not _was_transient(json.loads(ev["data_json"])) or self.eng.pr_test_view(r["branch"] or "") is not None:
            raise Refused(409, "there is nothing to retry")
        if self.now() - ev["ts"] < 120:
            raise Refused(429, "retried too recently")
        self._after_pr_open(cid)
        return self.view(cid)

    def _start_test(self, cid: int) -> None:
        """A `test_before_pr` request's branch is pushed: propose the class's test for the branch head and, as configured, approve that one proposal
        on behalf of the operator who approved the request. Never raises: whatever happens is an event the dispatcher reads (`pr_test`), and a test that
        cannot start means the PR opens saying why."""
        try:
            with self.lock:
                r = self._row(cid)
            kind = self._test_kind(r["class"])
            res = (self.eng.propose_pr_burst_test if kind == "burst" else self.eng.propose_pr_test)(r["branch"] or "", r["thread_id"], cid)
            if "proposal" in res and self.cfg.fold_test_approval and r["decided_by"]:
                try:
                    self.eng.decide(res["proposal"], "approve", by=r["decided_by"], ref=f"change-request-{cid}")
                    res = {**res, "approved_with_request": True}
                except Refused as e:   # the kill switch is engaged, the operator list changed...: the PR still opens, and says why it was not tested
                    res = {"ineligible": f"the test could not be approved with the request: {e.message}"[:200]}
        except Exception as e:  # noqa: BLE001
            res = {"ineligible": f"the test could not be proposed ({type(e).__name__})"}
        with self.lock:
            self._event(cid, "pr_test", res)
        self.audit("change_request_test_started", cr=cid, **{k: v for k, v in res.items() if k in ("proposal", "ineligible", "approved_with_request")})

    def _after_pr_open(self, cid: int) -> None:
        """10h2: a PR of a class that is proven on a canary gets a test proposal from the Toolbelt itself (the operator approves it on a
        card), or a recorded reason why it cannot be tested. Never raises: the PR is open either way."""
        try:
            with self.lock:
                r = self._row(cid)
            kind = self._test_kind(r["class"])
            if kind is None:
                return
            res = (self.eng.propose_pr_burst_test if kind == "burst" else self.eng.propose_pr_test)(r["branch"] or "", r["thread_id"], cid)
        except Exception as e:  # noqa: BLE001
            res = {"ineligible": f"the test could not be proposed ({type(e).__name__})"}
        with self.lock:
            self._event(cid, "pr_test", res)

    # -- housekeeping + reads ------------------------------------------------------------------------------
    def sweep(self) -> None:
        with self.lock:
            now = self.now()
            for r in self.db.execute("SELECT id, state, updated_at, claimed_at FROM change_requests WHERE state IN ('pending','approved','running')").fetchall():
                if r["state"] == "pending" and now - r["updated_at"] > self.cfg.pending_ttl:
                    self._move(r["id"], "expired", "expired", {"was": "pending"}, finished_at=now)
                elif r["state"] == "approved" and now - r["updated_at"] > self.cfg.approved_ttl:
                    self._move(r["id"], "expired", "expired", {"was": "approved"}, finished_at=now)
                elif r["state"] == "running" and now - (r["claimed_at"] or r["updated_at"]) > self.cfg.run_timeout:
                    self._move(r["id"], "failed", "timed-out", {}, finished_at=now, error="the author session did not report in time")
            for r in self.db.execute("SELECT id, updated_at FROM change_requests WHERE state='testing'").fetchall():
                if now - r["updated_at"] > self.cfg.testing_timeout:   # the dispatcher normally ends a test within an hour; the branch is left in place
                    self._move(r["id"], "failed", "timed-out", {"was": "testing"}, finished_at=now, error="the test stage did not finish (is the dispatcher running?); the branch was left in place")

    def list(self, states: tuple = ("pending", "approved", "running", "testing", "pr-open")) -> list[dict]:
        self.sweep()
        q = ",".join("?" * len(states))
        with self.lock:
            ids = [r["id"] for r in self.db.execute(f"SELECT id FROM change_requests WHERE state IN ({q}) ORDER BY id", states).fetchall()]
        return [self.view(i) for i in ids]

    def feed(self, after: int = 0, limit: int = 50) -> dict:
        self.sweep()
        with self.lock:
            rows = self.db.execute("SELECT * FROM change_request_events WHERE id>? ORDER BY id LIMIT ?", (int(after), min(int(limit), 200))).fetchall()
        events = [{"id": r["id"], "kind": r["kind"], "ts": r["ts"], "data": json.loads(r["data_json"]), "change_request": self.view(r["cr_id"])} for r in rows]
        return {"events": events, "next": events[-1]["id"] if events else int(after)}

    def summary(self) -> dict:
        with self.lock:
            counts = {r["state"]: r["n"] for r in self.db.execute("SELECT state, COUNT(*) n FROM change_requests GROUP BY state").fetchall()}
            started = self.db.execute("SELECT COUNT(*) n FROM change_requests WHERE claimed_at>=?", (self._day_start(),)).fetchone()["n"]
        return {"states": counts, "started_today": started, "daily_budget": self.cfg.max_started_per_day,
                "enabled_classes": sorted(n for n, c in (self.cfg.classes.get("classes") or {}).items() if c.get("enabled"))}
