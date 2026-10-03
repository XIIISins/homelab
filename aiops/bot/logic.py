"""Ratatoskr: the Discord transport and approver (Phase 10e). Pure logic; bot.py is the thin discord.py shell.

Three roles in the design (docs/operations/10e-approval-actions.md):

  the brain      n8n ("Gna"): answers questions, drafts diagnoses and proposals. An LLM reading untrusted text, so it holds
                 no authority. This bot forwards a human's @mention to it and posts its answer; nothing more.
  this bot       the mouth and ears, and the ONLY party that can turn a click into a decision. A button press or slash
                 command never goes through the brain: it goes bot -> Toolbelt with the approver credential.
  the Toolbelt   the authority: validates and stores proposals, binds an approval to the exact params, runs the executor.

Rules this module enforces so they are tested without Discord:
  * Authority comes from the Discord user id on a button/slash interaction (immutable, unspoofable by message text) and
    must be on the operator allow-list. Typing "yes do it" in chat is never an approval.
  * Cards are built ONLY from Toolbelt data (proposal fields), never from model prose, so a model cannot forge a card;
    the one free-text field (`reason`) is sanitised and never pings anyone.
  * Replay proposals (acceptance runs) never get a card.
  * The feed cursor and the "already announced" set survive restarts, so nothing is posted twice and nothing is lost.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

CUSTOM_ID = re.compile(r"^aiops:(approve|reject):(\d+):([0-9a-f]{16})$")
TERMINAL = {"rejected", "expired", "cancelled", "succeeded", "failed", "verify_failed"}
TIER_COLOUR = {"T0": 0x57F287, "T1": 0xFEE75C, "T2": 0xED4245, "T3": 0xED4245}
_MENTION = re.compile(r"<@[!&]?\d+>")
ZWSP = "​"


def custom_id(action: str, pid: int, params_hash: str) -> str:
    return f"aiops:{action}:{pid}:{params_hash}"


def parse_custom_id(s: str) -> tuple[str, int, str] | None:
    m = CUSTOM_ID.match(s or "")
    return (m.group(1), int(m.group(2)), m.group(3)) if m else None


@dataclass
class Config:
    toolbelt_url: str
    approver_token: str
    n8n_chat_url: str
    n8n_chat_token: str
    operator_ids: frozenset
    guild_id: int
    diagnoses_channel_id: int
    chat_channel_id: int
    state_dir: Path = Path("/var/lib/ratatoskr")
    poll_seconds: float = 3.0
    chat_timeout: float = 150.0

    def is_operator(self, user_id: object) -> bool:
        return str(user_id) in self.operator_ids


def load_config(static_path: str, secrets_path: str, approver_token_path: str, chat_token_path: str) -> tuple[Config, str]:
    """Static settings (urls) from a root-owned file; the Discord secret and the two tokens from the tmpfs files the root
    loader wrote from Vault. Returns (config, discord bot token)."""
    st = json.loads(Path(static_path).read_text())
    sec = json.loads(Path(secrets_path).read_text())
    cfg = Config(
        toolbelt_url=st["toolbelt_url"].rstrip("/"), approver_token=Path(approver_token_path).read_text().strip(),
        n8n_chat_url=st["n8n_chat_url"], n8n_chat_token=Path(chat_token_path).read_text().strip(),
        operator_ids=frozenset(str(sec["operator_user_id"]).split(",")), guild_id=int(sec["guild_id"]),
        diagnoses_channel_id=int(sec["diagnoses_channel_id"]), chat_channel_id=int(sec["chat_channel_id"]),
        state_dir=Path(st.get("state_dir", "/var/lib/ratatoskr")), poll_seconds=float(st.get("poll_seconds", 3.0)))
    if not cfg.operator_ids or not all(i.isdigit() for i in cfg.operator_ids):
        raise SystemExit("operator_user_id must be one or more Discord user ids")
    return cfg, str(sec["token"])


# ---- HTTP clients (blocking urllib; bot.py runs them in threads) ----------------------------------------

def _call(method: str, url: str, headers: dict, body=None, timeout: float = 15.0) -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"error": "unreadable response"}
    except (OSError, ValueError) as e:
        return 0, {"error": f"unreachable: {type(e).__name__}"}


class Toolbelt:
    """The approver-role client: the feed to render, the decision, the message ref, the flags, the status."""

    def __init__(self, cfg: Config):
        self.base, self.h = cfg.toolbelt_url, {"Authorization": "Bearer " + cfg.approver_token}

    def feed(self, after: int) -> tuple[int, dict]:
        return _call("GET", f"{self.base}/proposals/feed?after={int(after)}&limit=100", self.h)

    def get(self, pid: int) -> tuple[int, dict]:
        return _call("GET", f"{self.base}/proposals/{int(pid)}", self.h)

    def decide(self, pid: int, decision: str, by: str, ref: str, params_hash: str) -> tuple[int, dict]:
        return _call("POST", f"{self.base}/proposals/{int(pid)}/decision", self.h,
                     {"decision": decision, "by": by, "ref": ref, "params_hash": params_hash})

    def set_message(self, pid: int, message_ref: str) -> tuple[int, dict]:
        return _call("POST", f"{self.base}/proposals/{int(pid)}/message", self.h, {"message_ref": message_ref})

    def flags(self) -> tuple[int, dict]:
        return _call("GET", f"{self.base}/flags", self.h)

    def set_flag(self, name: str, value: bool, by: str, reason: str = "") -> tuple[int, dict]:
        return _call("POST", f"{self.base}/flags/{name}", self.h, {"value": value, "by": by, "reason": reason})

    def status(self) -> tuple[int, dict]:
        return _call("GET", f"{self.base}/status", self.h)


class Brain:
    """The n8n chat webhook. The bot passes who asked and what; n8n does the thinking and answers."""

    def __init__(self, cfg: Config):
        self.url, self.h, self.timeout = cfg.n8n_chat_url, {"X-AIOPS-Token": cfg.n8n_chat_token}, cfg.chat_timeout

    def chat(self, thread_id: str, author: str, content: str) -> tuple[int, dict]:
        return _call("POST", self.url, self.h, {"thread_id": thread_id, "author": author, "content": content}, self.timeout)


# ---- text helpers --------------------------------------------------------------------------------------------

def sanitize(text: object, limit: int = 300) -> str:
    """Free text from outside (a proposal's reason): no pings, no mention syntax, one line, bounded."""
    t = _MENTION.sub("[mention]", str(text))   # real mention syntax first, then break any remaining @everyone/@here
    t = t.replace("@", "@" + ZWSP)
    t = " ".join(t.split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def strip_mention(content: str, bot_id: int | str) -> str:
    return re.sub(rf"<@!?{bot_id}>", "", content or "").strip()


def split_message(text: str, limit: int = 1900) -> list[str]:
    out, cur = [], ""
    for line in (text or "").splitlines(keepends=True):
        while len(line) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            out.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        out.append(cur)
    return out or [""]


def friendly_chat_error(status: int, body: dict) -> str:
    err = str(body.get("error", ""))
    if "author-rate" in err:
        return "You have asked a lot this hour. Give me a little while, or ask the operator."
    if "conversation-cap" in err:
        return "This thread has reached its turn limit. Start a new thread in AIOps-chat."
    if "daily-chat-cap" in err:
        return "I have used today's question budget. Try again tomorrow, or ask the operator."
    if status == 0:
        return "I could not reach my brain (n8n) right now. The alert path is unaffected."
    if status in (401, 403):
        return "I am not allowed to talk to my brain at the moment (credential problem). The operator has been shown this in the logs."
    return f"I could not answer that right now (error {status})."


# ---- proposal cards -------------------------------------------------------------------------------------------

STATE_LINE = {
    "pending": "Waiting for the operator. Approve or Reject below.",
    "approved": "Approved. Starting.",
    "running": "Running.",
    "succeeded": "Succeeded and verified.",
    "failed": "FAILED.",
    "verify_failed": "Ran, but the post-condition did NOT hold.",
    "rejected": "Rejected. Nothing ran.",
    "expired": "Expired without a decision. Nothing ran.",
    "cancelled": "Cancelled before it started. Nothing ran.",
}


def num(p: dict) -> int:
    """The number a human reads for a proposal (1, 2, 3 within its conversation); the primary key is only the fallback."""
    return p.get("number", p["id"])


def card(p: dict) -> dict:
    """The embed for one proposal, from Toolbelt data only. `buttons` is True only while it can still be decided."""
    params = "\n".join(f"{k} = {v}" for k, v in p["params"].items()) or "(none)"
    st = p["state"]
    state = STATE_LINE.get(st, st)
    if st in ("approved", "running", "succeeded", "failed", "verify_failed", "rejected") and p.get("decided_by"):
        state += f" (decided by <@{p['decided_by']}>)"
    fields = [
        ("Action", f"`{p['action_id']}` ({p['tier']})", True),
        ("Target", f"`{sanitize(p['target'], 80)}`", True),
        ("Parameters", f"```\n{params[:400]}\n```", False),
        ("What it does", sanitize(p["description"], 250), False),
        ("Then it verifies", ", ".join(f"{k}={v}" for k, v in p["verify"].items()) or "-", True),
    ]
    if p.get("requires_prior"):
        fields.append(("Runs first", f"`{p['requires_prior']}` (dry run) must pass", True))
    fields.append(("If it goes wrong", sanitize(p["rollback"], 250), False))
    return {
        "title": f"Proposal #{num(p)}: {p['action_id']}",
        "description": f"{sanitize(p['reason'])}\n\n**{state}**",
        "fields": fields,
        "footer": f"id {p['id']} | params {p['params_hash']} | incident {p['incident_id'] or '-'} | {p['source']}"
                  + (f" | expires <t:{p['expires_at']}:R>" if st == "pending" else ""),
        "colour": TIER_COLOUR.get(p["tier"], 0x99AAB5) if st == "pending" else
                  (0x57F287 if st == "succeeded" else 0xED4245 if st in ("failed", "verify_failed") else 0x99AAB5),
        "buttons": st == "pending",
    }


def result_summary(p: dict) -> str:
    """A short outcome for a terminal proposal, from the stored result (already redacted by the engine)."""
    r = p.get("result") or {}
    head = {"succeeded": "Proposal #%d succeeded and verified." % num(p), "failed": "Proposal #%d FAILED." % num(p),
            "verify_failed": "Proposal #%d ran but its post-condition did NOT hold." % num(p),
            "rejected": "Proposal #%d was rejected." % num(p), "expired": "Proposal #%d expired undecided." % num(p),
            "cancelled": "Proposal #%d was cancelled before it started." % num(p)}.get(p["state"], f"Proposal #{num(p)}: {p['state']}")
    lines = [head]
    if r.get("why"):
        lines.append(sanitize(r["why"], 400))
    for s in r.get("steps", []):
        if isinstance(s, dict):
            lines.append(f"- {s.get('step')}: {s.get('status')}")
    if r.get("rollback"):
        lines.append("If it goes wrong: " + sanitize(r["rollback"], 250))
    return "\n".join(lines)[:1900]


# ---- the feed -> actions plan ----------------------------------------------------------------------------------

@dataclass
class Action:
    kind: str        # post_card | edit_card | announce
    proposal: dict


@dataclass
class State:
    """What survives a restart: the feed cursor and which terminal proposals were already announced."""
    path: Path
    cursor: int = 0
    announced: set = field(default_factory=set)

    @classmethod
    def load(cls, state_dir: Path) -> "State":
        p = Path(state_dir) / "state.json"
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


def plan_feed(events: list[dict], state: State) -> list[Action]:
    """One action per proposal touched by this batch, decided from its CURRENT state (events are hints, the proposal is truth)."""
    seen: dict[int, dict] = {}
    for e in events:
        seen[e["proposal"]["id"]] = e["proposal"]
    plan: list[Action] = []
    for pid, p in seen.items():
        if p.get("replay"):
            continue                                        # acceptance runs never reach a human
        if p["state"] == "pending":
            if not p.get("message_ref") and p.get("thread_id"):
                plan.append(Action("post_card", p))
            continue
        if p.get("message_ref"):
            plan.append(Action("edit_card", p))
            if p["state"] in TERMINAL and pid not in state.announced:
                plan.append(Action("announce", p))
    return plan


def format_status(s: dict) -> str:
    a = s.get("actions", {})
    flags = a.get("flags", {})
    lines = [f"**Kill switch:** {'ENGAGED' if flags.get('kill_switch') else 'off'} | **Maintenance:** {'on' if flags.get('maintenance') else 'off'}",
             f"Open incidents: {s.get('open_incidents', '?')} | agent runs today: {s.get('runs_today', '?')}/{s.get('daily_run_cap', '?')} | chat turns today: {s.get('chat_turns_today', 0)}",
             f"Proposals today: {a.get('proposals_today', 0)}/{a.get('daily_proposal_cap', '?')} | by state: " + (", ".join(f"{k} {v}" for k, v in sorted(a.get('proposals', {}).items())) or "none")]
    for p in s.get("open_proposals", [])[:8]:
        lines.append(f"- #{num(p)} (id {p['id']}) `{p['action_id']}` on `{sanitize(p['target'], 40)}`: {p['state']}")
    return "\n".join(lines)[:1900]
