"""Phase 10h2 in Ratatoskr: change requests (a request for ONE agent-authored PR) as Discord cards.

No Discord imports here, so it is testable on its own. The operator files a request with `/aiops draft`; the Toolbelt keeps it
`pending` until the operator presses Approve on its card; a dispatcher on Frigg claims it and a PR appears. This module is the
client for the approver-role routes, the card text, and the plan that turns the Toolbelt's change-request feed into Discord
actions (post a card, edit it, announce a result as a reply). Free text from requests is sanitised like proposal reasons.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import logic

CUSTOM_ID = re.compile(r"^aiops:cr-(approve|reject|cancel):([0-9]+)$")
TERMINAL = {"rejected", "expired", "cancelled", "failed", "no-change", "merged", "closed"}
ANNOUNCE = {"pr-open", "failed", "no-change", "merged", "closed", "expired"}
COLOUR = {"pending": 0xFEE75C, "approved": 0x57F287, "running": 0x5865F2, "pr-open": 0x57F287, "merged": 0x57F287,
          "failed": 0xED4245, "rejected": 0x99AAB5, "cancelled": 0x99AAB5, "expired": 0x99AAB5, "closed": 0x99AAB5, "no-change": 0x99AAB5}


def custom_id(act: str, cid: int) -> str:
    return f"aiops:cr-{act}:{cid}"


def parse_custom_id(s: str) -> tuple[str, int] | None:
    m = CUSTOM_ID.match(s or "")
    return (m.group(1), int(m.group(2))) if m else None


class Client:
    """The approver-role change-request routes (reuses the bot's blocking HTTP helper; bot.py runs these in threads)."""

    def __init__(self, cfg: logic.Config):
        self.base, self.h = cfg.toolbelt_url, {"Authorization": "Bearer " + cfg.approver_token}

    def feed(self, after: int) -> tuple[int, dict]:
        return logic._call("GET", f"{self.base}/change-requests/feed?after={int(after)}", self.h)

    def get(self, cid: int) -> tuple[int, dict]:
        return logic._call("GET", f"{self.base}/change-requests/{int(cid)}", self.h)

    def list(self, states: str = "pending,approved,running,pr-open") -> tuple[int, dict]:
        return logic._call("GET", f"{self.base}/change-requests?state={states}", self.h)

    def create(self, class_: str, title: str, body: str, by: str) -> tuple[int, dict]:
        return logic._call("POST", f"{self.base}/change-requests", self.h,
                           {"source": "operator", "class": class_, "title": title, "body": body, "by": by})

    def decide(self, cid: int, decision: str, by: str, ref: str) -> tuple[int, dict]:
        return logic._call("POST", f"{self.base}/change-requests/{int(cid)}/decision", self.h, {"decision": decision, "by": by, "ref": ref})

    def set_message(self, cid: int, message_ref: str, thread_id: str = "") -> tuple[int, dict]:
        return logic._call("POST", f"{self.base}/change-requests/{int(cid)}/message", self.h, {"message_ref": message_ref, "thread_id": thread_id})


def buttons(cr: dict) -> list[str]:
    return {"pending": ["approve", "reject"], "approved": ["cancel"], "running": ["cancel"]}.get(cr["state"], [])


def card(cr: dict) -> dict:
    """title / description / fields / colour / footer / buttons for one change request."""
    s = logic.sanitize
    fields = [("Class", f"`{s(cr['class'], 30)}`", True), ("State", f"`{cr['state']}`", True), ("Source", f"`{s(cr['source'], 30)}`", True),
              ("May change", "\n".join(f"`{s(p, 80)}`" for p in cr["allowed_paths"][:6]) or "-", False)]
    if cr.get("pr_url"):
        fields.append(("Pull request", cr["pr_url"], False))
    if cr.get("error"):
        fields.append(("Why it failed", s(cr["error"], 400), False))
    if cr.get("summary") and cr["state"] in ("pr-open", "merged", "closed"):
        fields.append(("Author's summary", s(cr["summary"], 500), False))
    foot = f"change request {cr['id']} · filed by {s(cr.get('created_by') or '?', 20)}"
    if cr.get("decided_by"):
        foot += f" · decided by {str(cr['decided_by'])[-4:]}"
    return {"title": f"Draft a PR: {s(cr['title'], 90)}", "description": s(cr["body"], 600), "fields": fields,
            "colour": COLOUR.get(cr["state"], 0x99AAB5), "footer": foot, "buttons": buttons(cr)}


def announcement(cr: dict) -> str:
    s = logic.sanitize
    st = cr["state"]
    if st == "pr-open":
        return f"Draft ready for review: {cr['pr_url']} . The operator merges; nothing was applied anywhere."
    if st == "failed":
        return f"The draft for request {cr['id']} did not become a PR: {s(cr.get('error') or 'no reason recorded', 400)}"
    if st == "no-change":
        return f"Request {cr['id']}: the author found nothing grounded to change. {s(cr.get('summary') or '', 300)}"
    if st == "merged":
        return f"Merged: {cr.get('pr_url')}"
    if st == "closed":
        return f"Closed without merging: {cr.get('pr_url')}"
    if st == "expired":
        return f"Request {cr['id']} expired before it was acted on."
    return f"Request {cr['id']} is {st}."


@dataclass
class Action:
    kind: str  # post_card | edit_card | announce
    cr: dict


@dataclass
class State:
    path: Path
    cursor: int = 0
    announced: set = field(default_factory=set)

    @classmethod
    def load(cls, state_dir: Path) -> "State":
        p = Path(state_dir) / "drafts-state.json"
        try:
            d = json.loads(p.read_text())
            return cls(p, int(d.get("cursor", 0)), set(d.get("announced", [])))
        except (OSError, ValueError):
            return cls(p)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"cursor": self.cursor, "announced": sorted(self.announced)[-500:]}))
        tmp.replace(self.path)


def plan(events: list[dict], state: State) -> list[Action]:
    """One plan per request touched by this batch, from its CURRENT state (events are hints, the request is truth)."""
    seen: dict[int, dict] = {}
    for e in events:
        seen[e["change_request"]["id"]] = e["change_request"]
    out: list[Action] = []
    for cid, cr in seen.items():
        if not cr.get("message_ref"):
            if cr["state"] == "pending":
                out.append(Action("post_card", cr))
            continue
        out.append(Action("edit_card", cr))
        key = f"{cid}:{cr['state']}"
        if cr["state"] in ANNOUNCE and key not in state.announced:
            out.append(Action("announce", cr))
    return out
