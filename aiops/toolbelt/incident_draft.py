"""Incident draft generator (Phase 10h3): a markdown skeleton of an incident from the Toolbelt's own records.

The facts are MECHANICAL: the timeline, the tool calls and the actions come straight from the database, every line cites
its source (`[alert ab12cd34]`, `[proposal #7]`, `[tool call 41]`), and nothing here calls a model. The agent's own
diagnosis is quoted and labelled as model output, and the root cause is a HYPOTHESIS until the operator confirms it.
Everything passes through `redact` before it leaves (the repo is public). The operator edits the draft; it never edits
decisions.md, open-questions.md, build-sequence.md or CLAUDE.md, it only lists what the post-flight checklist would update.

`build_draft(db, incident_id)` is a pure read over a sqlite3 connection (rows as sqlite3.Row); it returns markdown.
"""
from __future__ import annotations

import datetime as _dt
import json
import re

BANNER = ("> **DRAFT: generated from the Toolbelt's records, operator to edit.** The timeline, evidence and actions are "
          "mechanical (each line cites its source); the agent's diagnosis is quoted model output; the root cause is a "
          "**hypothesis** until you confirm it.")

_SLUG = re.compile(r"[^a-z0-9]+")


def _t(ts) -> str:
    return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).strftime("%H:%M:%SZ") if ts else "?"


def _d(ts) -> str:
    return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).strftime("%Y-%m-%d") if ts else "unknown-date"


def _short(s: object, n: int = 160) -> str:
    s = re.sub(r"\s+", " ", str(s if s is not None else "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _load(text) -> dict:
    try:
        v = json.loads(text) if text else {}
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _dur(a, b) -> str:
    if not a or not b:
        return "open"
    s = max(0, int(b) - int(a))
    return f"{s // 3600}h {s % 3600 // 60}m" if s >= 3600 else f"{s // 60}m {s % 60}s"


def slug_for(db, incident_id: int) -> str:
    """`YYYY-MM-DD-<host>-<what>` for the file name docs/incidents/ expects."""
    inc = db.execute("SELECT opened_at FROM incidents WHERE id=?", (incident_id,)).fetchone()
    al = db.execute("SELECT host, alert_json FROM alerts WHERE incident_id=? ORDER BY first_seen LIMIT 1", (incident_id,)).fetchone()
    what = _short(_load(al["alert_json"]).get("check", "incident"), 40) if al else "incident"
    host = al["host"] if al else "fleet"
    return f"{_d(inc['opened_at']) if inc else 'unknown-date'}-{_SLUG.sub('-', (host + '-' + what).lower()).strip('-')}"[:80]


def build_draft(db, incident_id: int, redact=lambda s: s) -> str:
    inc = db.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if inc is None:
        raise KeyError(f"no incident {incident_id}")
    alerts = db.execute("SELECT * FROM alerts WHERE incident_id=? ORDER BY first_seen", (incident_id,)).fetchall()
    calls = db.execute("SELECT * FROM tool_calls WHERE incident_id=? ORDER BY ts, id", (incident_id,)).fetchall()
    diag_row = db.execute("SELECT * FROM diagnoses WHERE incident_id=?", (incident_id,)).fetchone()
    try:
        props = db.execute("SELECT * FROM proposals WHERE incident_id=? ORDER BY id", (incident_id,)).fetchall()
    except Exception:  # actions disabled: the table may not exist
        props = []

    events: list[tuple[int, str]] = []
    if inc["opened_at"]:
        events.append((inc["opened_at"], f"Incident #{incident_id} opened (group `{inc['group_key']}`) [incident {incident_id}]"))
    for a in alerts:
        aj = _load(a["alert_json"])
        events.append((a["first_seen"], f"Alert firing on `{a['host']}`: {_short(aj.get('summary') or aj.get('check') or 'alert')} "
                                        f"(severity {a['severity']}, runbook {aj.get('runbook_id', '-')}) [alert {a['fingerprint'][:8]}]"))
        if a["resolved_at"]:
            events.append((a["resolved_at"], f"Alert on `{a['host']}` resolved [alert {a['fingerprint'][:8]}]"))
    if inc["running_at"]:
        events.append((inc["running_at"], f"Diagnosis agent started [incident {incident_id}]"))
    if diag_row:
        events.append((diag_row["created_at"], f"Diagnosis accepted by the Toolbelt (model {diag_row['model']}) [diagnosis]"))
    if inc["posted_at"]:
        events.append((inc["posted_at"], f"Diagnosis posted to the thread [incident {incident_id}]"))
    for p in props:
        who = p["decided_by"] or "undecided"
        who = "the Toolbelt under policy " + who[5:] if who.startswith("auto:") else (f"operator {who}" if who != "undecided" else who)
        events.append((p["created_at"], f"Proposal #{p['id']} `{p['action_id']}` on `{p['target']}` created ({p['source']}) [proposal #{p['id']}]"))
        if p["decided_at"]:
            events.append((p["decided_at"], f"Proposal #{p['id']} approved by {who} [proposal #{p['id']}]"))
        if p["finished_at"]:
            events.append((p["finished_at"], f"Proposal #{p['id']} ended **{p['state']}** [proposal #{p['id']}]"))
        try:
            for e in db.execute("SELECT * FROM proposal_events WHERE proposal_id=? AND kind IN ('breaker_tripped','thread_bound') ORDER BY id", (p["id"],)):
                events.append((e["ts"], f"{e['kind'].replace('_', ' ').capitalize()} [proposal #{p['id']}]"))
        except Exception:
            pass
    if inc["resolved_at"]:
        events.append((inc["resolved_at"], f"Incident #{incident_id} resolved [incident {incident_id}]"))
    events.sort(key=lambda e: e[0])

    hosts = sorted({a["host"] for a in alerts})
    first, last = (alerts[0]["first_seen"] if alerts else inc["opened_at"]), inc["resolved_at"]
    out = [BANNER, "", f"# Incident #{incident_id}: {', '.join(hosts) or 'fleet'} ({_d(inc['opened_at'])})", "",
           f"- **State:** {inc['state']}   **Opened:** {_d(inc['opened_at'])} {_t(first)}   **Resolved:** {_t(last) if last else 'not yet'}   **Duration:** {_dur(first, last)}",
           f"- **Alerts:** {len(alerts)} ({', '.join(sorted({a['severity'] for a in alerts})) or '-'}); **tool calls served:** {sum(1 for c in calls if c['outcome'] == 'served')}; "
           f"**proposals:** {len(props)}",
           "", "## Timeline (UTC, mechanical)", ""]
    out += [f"- {_t(ts)}  {text}" for ts, text in events] or ["- (no recorded events)"]

    out += ["", "## Evidence the agent gathered (tool calls actually served)", ""]
    if calls:
        out += ["| time | tool | arguments | outcome |", "|---|---|---|---|"]
        for c in calls:
            out.append(f"| {_t(c['ts'])} | `{c['tool']}` | `{_short(c['args_json'] or '', 100)}` | {c['outcome']} [tool call {c['id']}] |")
    else:
        out.append("(none recorded)")

    out += ["", "## Agent diagnosis (model output, validated against the calls above)", ""]
    if diag_row:
        d = _load(diag_row["diagnosis_json"])
        out += [f"- **Layer:** {d.get('layer', '?')}   **Confidence:** {d.get('confidence', '?')}   **Runbook:** {d.get('runbook_id', '-')}",
                f"- **Summary:** {_short(d.get('summary'), 400)}"]
        if d.get("reasoning"):
            out.append(f"- **Reasoning:** {_short(d.get('reasoning'), 500)}")
        for ev in d.get("evidence", []) or []:
            out.append(f"  - evidence `{ev.get('tool')}`: {_short(ev.get('finding'), 200)}")
        out.append(f"- **Root cause (HYPOTHESIS, not confirmed):** {_short(d.get('summary'), 300)}")
        for n in d.get("next_checks", []) or []:
            out.append(f"- next check: {_short(n, 200)}")
    else:
        out.append("No diagnosis was recorded for this incident (the agent did not run, was refused, or was rejected).")

    out += ["", "## Actions", ""]
    if props:
        for p in props:
            r = _load(p["result_json"])
            who = p["decided_by"] or "not decided"
            who = f"auto ({who[5:]})" if who.startswith("auto:") else who
            out.append(f"- Proposal #{p['id']} `{p['action_id']}` on `{p['target']}`: **{p['state']}**, decided by {who}. "
                       f"{_short(r.get('why') or '', 240)} [proposal #{p['id']}]")
    else:
        out.append("No action was proposed.")

    out += ["", "## Follow-ups for the operator (the post-flight checklist; this draft edits none of them)", "",
            "- [ ] Confirm or correct the root-cause hypothesis above and write the real narrative.",
            "- [ ] Known-issue: add or update the entry in `docs/known-issues/<subject>.md` (rule, why, symptom/diagnostic, recovery); gotcha text never goes in CLAUDE.md.",
            "- [ ] `docs/incidents/README.md`: add this incident's row; `open-questions.md` / `decisions.md` / `build-sequence.md` if anything changed.",
            "- [ ] Check whether a runbook or the alert routing should change.", ""]
    return redact("\n".join(out))
