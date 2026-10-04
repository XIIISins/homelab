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
TERMINAL = {"rejected", "expired", "cancelled", "succeeded", "failed", "verify_failed", "skipped"}
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
    forecasts_channel_id: int = 0  # optional quiet channel for forecast cards; 0 = use the chat channel
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
        forecasts_channel_id=int(st.get("forecasts_channel_id", 0) or 0),
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

    def report(self, days: int = 14) -> tuple[int, dict]:
        return _call("GET", f"{self.base}/report?days={int(days)}", self.h)

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
    "skipped": "Skipped: the fault had already healed, so nothing ran.",
}


def auto_policy(p: dict) -> str | None:
    """The policy name when the Toolbelt (not a person) approved this proposal under autonomy, else None."""
    by = p.get("decided_by") or ""
    return by[5:] if by.startswith("auto:") else None


def num(p: dict) -> int:
    """The number a human reads for a proposal (1, 2, 3 within its conversation); the primary key is only the fallback."""
    return p.get("number", p["id"])


def rebuild_fields(rb: dict) -> list[tuple]:
    """The informed-approval sections of a rebuild card (10g): the plan, what gets destroyed, the data-loss manifest and the
    age of the last backup. Everything comes from the Toolbelt's stored plan and manifest, never from a model."""
    ident = rb.get("identity") or {}
    plan = (f"`{sanitize(rb.get('plan_action'), 12)}` of `{sanitize(ident.get('name'), 40)}` (vmid {sanitize(ident.get('vmid'), 8)} on "
            f"{sanitize(ident.get('node'), 20)}), {sanitize(rb.get('plan_changes'), 4)} change; main `{sanitize(rb.get('origin_main'), 12)}`; "
            f"plan `{sanitize(rb.get('plan_id'), 64)[:12]}`" + (f", valid until <t:{int(rb['plan_expires_at'])}:R>" if isinstance(rb.get("plan_expires_at"), int) else ""))
    out = [("Plan", plan[:600], False), ("What gets destroyed", sanitize(rb.get("destroys"), 400), False)]
    if rb.get("manifest"):
        lines = "\n".join(sanitize(ln, 120) for ln in str(rb["manifest"]).splitlines()[:14])
        out.append(("Data-loss manifest", f"```\n{lines[:900]}\n```", False))
    age = rb.get("backup_age_hours")
    out.append(("Last backup", f"{age:.0f} h ago" if isinstance(age, (int, float)) and not isinstance(age, bool) else "none (a canary has no data) or unknown", True))
    return out


def card(p: dict) -> dict:
    """The embed for one proposal, from Toolbelt data only. `buttons` is True only while it can still be decided."""
    params = "\n".join(f"{k} = {v}" for k, v in p["params"].items()) or "(none)"
    st = p["state"]
    state = STATE_LINE.get(st, st)
    if st == "running" and (p.get("rebuild") or {}).get("stage"):
        state = f"Rebuilding: step `{sanitize(p['rebuild']['stage'], 20)}` (done: {', '.join(sanitize(d, 20) for d in p['rebuild'].get('done', [])) or 'none yet'})."
    if auto_policy(p):
        state = f"Auto-approved by policy `{sanitize(auto_policy(p), 60)}` (no human decision). " + state
    elif st in ("approved", "running", "succeeded", "failed", "verify_failed", "rejected") and p.get("decided_by"):
        state += f" (decided by <@{p['decided_by']}>)"
    fields = [
        ("Action", f"`{p['action_id']}` ({p['tier']})", True),
        ("Target", f"`{sanitize(p['target'], 80)}`", True),
        ("Parameters", f"```\n{params[:400]}\n```", False),
        ("What it does", sanitize(p["description"], 250), False),
        ("Then it verifies", ", ".join(f"{k}={v}" for k, v in p["verify"].items()) or "-", True),
    ]
    rb = p.get("rebuild")
    if rb:
        fields += rebuild_fields(rb)
    elif p.get("requires_prior"):
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


# ---- relaying a read-only action's answer ----------------------------------------------------------------------
# "Succeeded and verified" says the probe ran, not what it found. For a T0 proposal the thread also gets one plain
# sentence built from the stored result fields (never from a model). Every value goes through sanitize().

def _flag(v: object) -> bool | None:
    """The engine stores Jinja-rendered fields, so a bool may arrive as 'True'/'false'; None means unknown."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return None


def _s(v: object, limit: int = 60) -> str:
    return sanitize(v, limit)


def _count(n: object, noun: str) -> str:
    return f"{_s(n, 12)} {noun}{'' if str(n) == '1' else 's'}"


def _sentence_patroni(r: dict) -> str:
    if _flag(r.get("ok")) is False:
        return "Checked Patroni: no PG node answered the /cluster probe."
    members = [str(m).split(":") for m in (r.get("members") or []) if isinstance(m, str)]
    leaders = [m[0] for m in members if len(m) > 1 and m[1] in ("leader", "master", "primary")]
    lead = f"leader is {_s(leaders[0])}" if leaders else "NO leader"
    total = r.get("total_members", len(members))
    out = f"Checked Patroni: {lead}, {_s(r.get('running_members', '?'), 12)} of {_s(total, 12)} members running"
    down = [f"{_s(m[0])} ({_s(m[2])})" for m in members if len(m) > 2 and m[2] not in ("running", "streaming")]
    if down:
        out += " (not running: " + ", ".join(down[:3]) + ")"
    lag = r.get("max_lag_bytes")
    if isinstance(lag, str) and lag.strip().lstrip("-").isdigit():
        lag = int(lag)  # the engine stores Jinja-rendered fields: a number may arrive as a string
    if isinstance(lag, (int, float)) and not isinstance(lag, bool):
        return out + (", no lag." if lag <= 0 else f", worst replica lag {_s(int(lag), 20)} bytes.")
    return out + "."


def _sentence_service(r: dict) -> str:
    unit, host = _s(r.get("unit", "the unit")), _s(r.get("target_host", "?"))
    if r.get("load_state") not in (None, "loaded"):
        return f"Checked {unit} on {host}: unit is {_s(r.get('load_state'))}, not loaded."
    out = f"Checked {unit} on {host}: {_s(r.get('active_state', 'unknown'))} ({_s(r.get('sub_state', 'unknown'))})"
    if str(r.get("n_restarts", "")).strip() not in ("", "0"):
        out += f", restarted {_count(r['n_restarts'], 'time')}"
    if r.get("active_since"):
        out += f", since {_s(r['active_since'], 40)}"
    return out + "."


def _sentence_vault(r: dict) -> str:
    if _flag(r.get("ok")) is False:
        return "Checked Vault: no status document came back."
    sealed = _flag(r.get("sealed"))
    out = "Checked Vault: " + {True: "SEALED", False: "unsealed", None: "seal state unknown"}[sealed]
    bits = []
    if _flag(r.get("initialized")) is not None:
        bits.append("initialized" if _flag(r["initialized"]) else "NOT initialized")
    if _flag(r.get("ha_enabled")) is not None:
        bits.append("HA enabled" if _flag(r["ha_enabled"]) else "HA off")
    if r.get("version"):
        bits.append("version " + _s(r["version"], 30))
    if r.get("storage_type"):
        bits.append("storage " + _s(r["storage_type"], 30))
    return out + "".join(", " + b for b in bits) + "."


def _sentence_replay_check(r: dict) -> str:
    host = _s(r.get("target_host", "the host"))
    if _flag(r.get("ok")) is False:
        return f"Dry run on {host} did not finish cleanly (failed={_s(r.get('failed', '?'), 12)}, unreachable={_s(r.get('unreachable', '?'), 12)})."
    ch = r.get("changed")
    if ch in (0, "0"):
        return f"Dry run on {host}: nothing would change, the host is in sync."
    return f"Dry run on {host}: a replay would change {_count(ch, 'task') if ch is not None else 'an unknown number of tasks'}."


RESULT_SENTENCES = {
    "patroni-status": _sentence_patroni,
    "service-status": _sentence_service,
    "vault-status": _sentence_vault,
    "replay-role-check": _sentence_replay_check,
}


def _sentence_generic(action_id: str, r: dict) -> str:
    """An action without a formatter: up to five scalar fields, never raw JSON."""
    bits = [f"{_s(k, 30)}={_s(v, 60)}" for k, v in r.items() if k != "action" and isinstance(v, (str, int, float, bool))][:5]
    return f"Checked {_s(action_id, 40)}: " + (", ".join(bits) if bits else "no details reported") + "."


def result_sentence(p: dict) -> str:
    """One human sentence for a succeeded T0 proposal, '' for anything else. Never raises."""
    try:
        if p.get("state") != "succeeded" or p.get("tier") != "T0":
            return ""
        r = next((s.get("result") for s in (p.get("result") or {}).get("steps", [])
                  if isinstance(s, dict) and s.get("step") == "action"), None)
        if not isinstance(r, dict) or not r:
            return ""
        fn = RESULT_SENTENCES.get(p["action_id"])
        try:
            return sanitize(fn(r), 400) if fn else sanitize(_sentence_generic(p["action_id"], r), 400)
        except Exception:  # noqa: BLE001 - odd fields degrade to the generic line, never break the announcement
            return sanitize(_sentence_generic(p["action_id"], r), 400)
    except Exception:  # noqa: BLE001
        return ""


def plan_sentence(p: dict) -> str:
    """One sentence for a `rebuild-plan` result (its step is `action`, whose result carries the runner's plan summary)."""
    if p.get("action_id") != "rebuild-plan" or not p.get("result"):
        return ""
    r = next((s.get("result") for s in p["result"].get("steps", []) if isinstance(s, dict) and s.get("step") == "action"), None) or {}
    s = r.get("summary") or {}
    ident = s.get("identity") or {}
    if r.get("plan_ok") is True:
        return sanitize(f"Plan ok: {s.get('action')} of {ident.get('name')} (vmid {ident.get('vmid')} on {ident.get('node')}), {s.get('changes')} change, "
                        f"plan {str(r.get('plan_id'))[:12]}, from main {str(r.get('origin_main'))[:12]}. Nothing was changed.", 400)
    return sanitize("The plan did not pass: " + "; ".join(str(x) for x in (r.get("problems") or ["no detail"])[:3]) + ". Nothing was changed.", 400)


def result_summary(p: dict) -> str:
    """A short outcome for a terminal proposal, from the stored result (already redacted by the engine)."""
    r = p.get("result") or {}
    head = {"succeeded": "Proposal #%d succeeded and verified." % num(p), "failed": "Proposal #%d FAILED." % num(p),
            "verify_failed": "Proposal #%d ran but its post-condition did NOT hold." % num(p),
            "rejected": "Proposal #%d was rejected." % num(p), "expired": "Proposal #%d expired undecided." % num(p),
            "cancelled": "Proposal #%d was cancelled before it started." % num(p),
            "skipped": "Proposal #%d was skipped: the fault had already healed." % num(p)}.get(p["state"], f"Proposal #{num(p)}: {p['state']}")
    lines = [head]
    answer = result_sentence(p) or plan_sentence(p)
    if answer:
        lines.append(answer)
    if r.get("why"):
        lines.append(sanitize(r["why"], 400))
    if p.get("action_id") in ("rebuild-guest", "rebuild-worker"):
        ident = (p.get("rebuild") or {}).get("identity") or {}
        lines.append(f"Rebuild of `{sanitize(p['target'], 40)}` (vmid {sanitize(ident.get('vmid'), 8)}, {sanitize(ident.get('node'), 20)}), plan "
                     f"`{sanitize(r.get('plan_id') or (p.get('rebuild') or {}).get('plan_id'), 64)[:12]}`.")
        if r.get("nothing_changed"):
            lines.append("Nothing was changed: the refusal came before the apply.")
        elif p["state"] in ("failed", "verify_failed"):
            lines.append("The guest may be half-built. The rebuild breaker is TRIPPED: no further rebuild runs until an operator runs `/aiops rebuild reset-breaker`.")
    for s in r.get("steps", []):
        if isinstance(s, dict):
            secs = s.get("seconds")
            lines.append(f"- {s.get('step')}: {s.get('status')}" + (f" ({int(secs)} s)" if isinstance(secs, (int, float)) and not isinstance(secs, bool) else ""))
    if isinstance(r.get("timings"), dict) and isinstance(r["timings"].get("total"), (int, float)):
        lines.append(f"Total {int(r['timings']['total'])} s.")
    if r.get("rollback"):
        lines.append("If it goes wrong: " + sanitize(r["rollback"], 250))
    return "\n".join(lines)[:1900]


# ---- the feed -> actions plan ----------------------------------------------------------------------------------

@dataclass
class Action:
    kind: str        # post_card | edit_card | announce | breaker
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
    plan: list[Action] = []
    for e in events:
        seen[e["proposal"]["id"]] = e["proposal"]
        if e.get("kind") == "breaker_tripped" and e["proposal"].get("thread_id"):
            plan.append(Action("breaker", {**e["proposal"], "breaker": e.get("data") or {}}))
    for pid, p in seen.items():
        if p.get("replay"):
            continue                                        # acceptance runs never reach a human
        if p["state"] == "pending":
            if not p.get("message_ref") and p.get("thread_id"):
                plan.append(Action("post_card", p))
            continue
        if auto_policy(p) and not p.get("message_ref") and p.get("thread_id"):
            plan.append(Action("post_card", p))            # decided by policy before any card existed: show it, without buttons
            if p["state"] in TERMINAL and pid not in state.announced:
                plan.append(Action("announce", p))
            continue
        if p.get("message_ref"):
            plan.append(Action("edit_card", p))
            if p["state"] in TERMINAL and pid not in state.announced:
                plan.append(Action("announce", p))
    return plan


def breaker_notice(p: dict) -> str:
    d = p.get("breaker") or {}
    if d.get("kind") == "rebuild":
        return (f"**Rebuild circuit breaker TRIPPED**: the rebuild of `{sanitize(p['target'], 40)}` (proposal #{num(p)}) failed or did not verify "
                f"({sanitize(d.get('why', ''), 150)}). The guest may be half-built. No further rebuild runs, attended or not, until an operator looks and "
                "runs `/aiops rebuild reset-breaker`.")
    return (f"**Autonomy circuit breaker TRIPPED**: {d.get('failures', '?')} autonomous runs failed or did not verify within "
            f"{int(d.get('window_seconds', 0)) // 60} minutes (last: proposal #{num(p)} on `{sanitize(p['target'], 40)}`). "
            "Autonomous healing is stopped; proposals now wait for you as usual. Look at why, then `/aiops autonomy reset-breaker`.")


def format_report(r: dict) -> str:
    lines = [f"**Autonomy, last {r.get('days', '?')} day(s):** {r.get('autonomous_runs', 0)} autonomous run(s) | breaker trips {r.get('breaker_trips', 0)} | "
             f"master switch {'ON' if r.get('flags', {}).get('autonomy') else 'off'}"]
    for pol, states in sorted(r.get("by_policy", {}).items()):
        lines.append(f"- `{sanitize(pol, 40)}`: " + ", ".join(f"{k} {v}" for k, v in sorted(states.items())))
    if r.get("by_target"):
        lines.append("By target: " + ", ".join(f"`{sanitize(t, 30)}` {n}" for t, n in r["by_target"].items()))
    if r.get("skipped_reasons"):
        lines.append("Not run, and why: " + ", ".join(f"{sanitize(k, 40)} {v}" for k, v in sorted(r["skipped_reasons"].items())))
    if r.get("flapping_targets"):
        lines.append("**Flapping** (3+ autonomous runs within 6h): " + ", ".join(f"`{sanitize(t, 30)}`" for t in r["flapping_targets"]))
    rb = r.get("rebuild")
    if rb:
        flags = r.get("flags", {})
        lines.append(f"**Rebuilds, same period:** {rb.get('runs', 0)} run(s), {rb.get('unattended', 0)} unattended | breaker trips {rb.get('breaker_trips', 0)} | "
                     f"rebuild autonomy {'ON' if flags.get('autonomy_rebuild') else 'off'}" + (" | **BREAKER TRIPPED**" if flags.get("autonomy_rebuild_breaker") else "")
                     + (f" | median {int(rb['median_seconds'])} s" if isinstance(rb.get("median_seconds"), (int, float)) else ""))
        if rb.get("by_outcome"):
            lines.append("Rebuild outcomes: " + ", ".join(f"{sanitize(k, 20)} {v}" for k, v in sorted(rb["by_outcome"].items())))
        if rb.get("by_target"):
            lines.append("Rebuilt: " + ", ".join(f"`{sanitize(t, 30)}` {n}" for t, n in rb["by_target"].items()))
        for x in rb.get("rebuilding", [])[:3]:
            lines.append(f"- REBUILDING `{sanitize(x.get('target'), 30)}`: step {sanitize(x.get('stage'), 20)}")
    return "\n".join(lines)[:1900]


def format_status(s: dict) -> str:
    a = s.get("actions", {})
    flags = a.get("flags", {})
    lines = [f"**Kill switch:** {'ENGAGED' if flags.get('kill_switch') else 'off'} | **Maintenance:** {'on' if flags.get('maintenance') else 'off'}",
             f"Open incidents: {s.get('open_incidents', '?')} | agent runs today: {s.get('runs_today', '?')}/{s.get('daily_run_cap', '?')} | chat turns today: {s.get('chat_turns_today', 0)}",
             f"Autonomy: {'ON' if flags.get('autonomy') else 'off'}" + (" | **BREAKER TRIPPED**" if flags.get("autonomy_breaker") else "")
             + (f" | policies on: {', '.join(sorted(n for n, on in a['autonomy']['policies'].items() if on)) or 'none'} | hosts: {', '.join(a['autonomy']['hosts'])}"
                if a.get("autonomy") else ""),
             f"Proposals today: {a.get('proposals_today', 0)}/{a.get('daily_proposal_cap', '?')} | by state: " + (", ".join(f"{k} {v}" for k, v in sorted(a.get('proposals', {}).items())) or "none")]
    rb = a.get("rebuild")
    if rb:
        lines.append(f"Rebuild autonomy: {'ON' if flags.get('autonomy_rebuild') else 'off'}" + (" | **REBUILD BREAKER TRIPPED**" if flags.get("autonomy_rebuild_breaker") else "")
                     + (f" | policies on: {', '.join(sorted(n for n, on in rb.get('policies', {}).items() if on)) or 'none'}"))
        for x in rb.get("rebuilding", [])[:3]:
            lines.append(f"- **REBUILDING** `{sanitize(x.get('target'), 30)}` (proposal {sanitize(x.get('proposal'), 8)}): step `{sanitize(x.get('stage'), 20)}`")
    for p in s.get("open_proposals", [])[:8]:
        lines.append(f"- #{num(p)} (id {p['id']}) `{p['action_id']}` on `{sanitize(p['target'], 40)}`: {p['state']}")
    return "\n".join(lines)[:1900]
