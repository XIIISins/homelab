"""The rightsizing digest in Ratatoskr (Phase 10i3), without Discord: the client for the Toolbelt's digest routes and the text of the
header post. bot.py wires these to discord.py: one header post in the forecasts channel, a thread under it, and one card per suggestion
(the same Useful / Noise / Draft PR buttons as a forecast card, on the forecast row behind the suggestion).

Quiet by design: nothing here pings anyone, and a digest is only ever a read of the cluster plus proposals for a human."""
from __future__ import annotations

import datetime as dt

import logic

CADENCES = ("weekly", "biweekly", "monthly")
TICK_EVERY = 600.0   # seconds between asking the Toolbelt whether a digest is due (it decides; this only bounds the polling)


class Client:
    """The approver-role rightsizing routes (bot.py runs these in threads)."""

    def __init__(self, cfg: logic.Config):
        self.base, self.h = cfg.toolbelt_url, {"Authorization": "Bearer " + cfg.approver_token}

    def tick(self) -> tuple[int, dict]:
        return logic._call("POST", f"{self.base}/rightsizing/digest/tick", self.h, {}, timeout=30.0)

    def now(self, by: str) -> tuple[int, dict]:
        return logic._call("POST", f"{self.base}/rightsizing/digest", self.h, {"by": by}, timeout=30.0)

    def status(self) -> tuple[int, dict]:
        return logic._call("GET", f"{self.base}/rightsizing/status", self.h)

    def set_cadence(self, cadence: str, by: str) -> tuple[int, dict]:
        return logic._call("POST", f"{self.base}/rightsizing/cadence", self.h, {"cadence": cadence, "by": by})

    def revert(self, wid: int, by: str) -> tuple[int, dict]:
        return logic._call("POST", f"{self.base}/rightsizing/watches/{int(wid)}/revert", self.h, {"by": by})

    def set_message(self, did: int, message_ref: str, thread_id: str = "") -> tuple[int, dict]:
        return logic._call("POST", f"{self.base}/rightsizing/digest/{int(did)}/message", self.h, {"message_ref": message_ref, "thread_id": thread_id})


def _deltas(row: dict) -> str:
    out = []
    for key, label in (("vs_last", "last"), ("vs_baseline", "start")):
        d = row.get(key)
        if d and d.get("memory_requested_mib"):   # nothing for "no change": a row of "+0" is noise
            out.append(f"{d['memory_requested_mib']:+.0f} vs {label}")
    return f" ({', '.join(out)})" if out else ""


def scoreboard(d: dict) -> str:
    """The per-worker lines, as a code block (monospaced, one worker per line)."""
    def n(x, unit: str = "") -> str:
        return "?" if x is None else f"{x:.0f}{unit}"

    lines = []
    for w in d.get("scoreboard", []):
        node = logic.sanitize(w["node"], 20).removeprefix("einherjar-")
        lines.append(f"{node:<6} req {n(w.get('memory_requested_mib'), ' MiB')} ({n(w.get('memory_requested_pct'), '%')}){_deltas(w)}")
        lines.append(f"       lim {n(w.get('memory_limits_mib'))} · used {n(w.get('memory_used_mib'))} · cpu {n(w.get('cpu_requested_millicores'), 'm')} ({n(w.get('cpu_requested_pct'), '%')})")
    return "```\n" + ("\n".join(lines) or "no worker data") + "\n```"


def results_text(d: dict) -> str:
    rows = d.get("results") or []
    if not rows:
        return "None yet: no rightsizing PR has merged."
    out = []
    for r in rows[:8]:
        out.append(f"- `{logic.sanitize(str(r.get('target')), 60)}`: **{r.get('verdict', '?')}**" + (f", freed {r['freed_mib']:g} MiB" if r.get("freed_mib") else "")
                   + (f" ([PR]({r['pr_url']}))" if r.get("pr_url") else ""))
    return "\n".join(out)


def coverage_text(d: dict) -> str:
    c = d.get("coverage", {})
    sup = ", ".join(f"{n} {k}" for k, n in sorted((c.get("suppressed") or {}).items())) or "none"
    miss = c.get("without_vpa") or []
    text = (f"{c.get('with_vpa', '?')} of {c.get('controllers', '?')} controllers have a VPA; VPA data is {c.get('oldest_vpa_sample_days', '?')} day(s) old. "
            f"{c.get('open_findings', 0)} open finding(s); suppressed: {sup}.")
    if miss:
        text += " **No VPA:** " + ", ".join(f"`{logic.sanitize(m, 50)}`" for m in miss[:10])
    return text[:1000]


def header(d: dict) -> dict:
    """The digest's head post as a card dict (title, description, fields, colour, footer); bot.py turns it into an embed."""
    when = dt.datetime.fromtimestamp(d["as_of"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    n = len(d.get("suggestions") or [])
    intro = ("**First digest: this is the baseline** the later ones are measured against. " if d.get("baseline") else "") + \
        f"Cadence **{d.get('cadence')}** (every {d.get('period_days')} days). " + (f"{n} suggestion(s) below in the thread." if n else "Nothing to suggest right now.")
    fields = [("Workers (memory requested vs scheduler capacity)", scoreboard(d), False)]
    tuning = d.get("tuning") or []
    if tuning:
        fields.append(("Worth a look (no proposal)", "\n".join(f"- `{logic.sanitize(t['target'], 60)}`: {logic.sanitize(t['text'], 220)}" for t in tuning)[:1000], False))
    fields.append(("Results of earlier PRs", results_text(d)[:1000], False))
    fields.append(("Coverage", coverage_text(d), False))
    return {"title": "Rightsizing digest", "description": intro[:900], "fields": fields, "colour": 0x3498DB,
            "footer": f"digest {d['id']} · data as of {when} · nothing here changes the cluster: a human reviews every PR"}
