"""Forecast cards for Ratatoskr (Phase 10h1), without Discord: what a finding looks like, which buttons it carries, and what the
bot should do about each batch of the Toolbelt's forecast events. bot.py wires these to discord.py.

A forecast is a heads-up, never an alert: the card says so, nothing here pages, and the only decisions an operator makes are the
two labels (Useful / Noise) that become the evidence for tuning thresholds through a reviewed PR. Same shape as drafts.py."""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import logic

CUSTOM_ID = re.compile(r"^aiops:fc-(useful|noise):([0-9]+)$")
COLOUR = {"hot": 0xE74C3C, "soon": 0xE67E22, "later": 0xF1C40F, "quiet": 0x99AAB5}
SIGNALS = {
    "vfs.fs.dependent.size[*,pused]": "Filesystem fill",
    "proxmox.node.disk/maxdisk": "Proxmox storage fill",
    "vm.memory.size[pavailable]": "Memory headroom",
    "kubelet_volume_stats_used_bytes/capacity_bytes": "Kubernetes volume fill",
    "vl_data_size_bytes": "VictoriaLogs data size",
}


def custom_id(act: str, fid: int) -> str:
    return f"aiops:fc-{act}:{int(fid)}"


def parse_custom_id(s: str) -> tuple[str, int] | None:
    m = CUSTOM_ID.match(s or "")
    return (m.group(1), int(m.group(2))) if m else None


class Client:
    """The approver-role forecast routes (the bot's blocking HTTP helper; bot.py runs these in threads)."""

    def __init__(self, cfg: logic.Config):
        self.base, self.h = cfg.toolbelt_url, {"Authorization": "Bearer " + cfg.approver_token}

    def feed(self, after: int) -> tuple[int, dict]:
        return logic._call("GET", f"{self.base}/forecasts/feed?after={int(after)}", self.h)

    def list(self, states: str = "open") -> tuple[int, dict]:
        return logic._call("GET", f"{self.base}/forecasts?state={states}", self.h)

    def get(self, fid: int) -> tuple[int, dict]:
        return logic._call("GET", f"{self.base}/forecasts/{int(fid)}", self.h)

    def label(self, fid: int, label: str, by: str) -> tuple[int, dict]:
        return logic._call("POST", f"{self.base}/forecasts/{int(fid)}/label", self.h, {"label": label, "by": by})

    def set_message(self, fid: int, message_ref: str, thread_id: str = "") -> tuple[int, dict]:
        return logic._call("POST", f"{self.base}/forecasts/{int(fid)}/message", self.h, {"message_ref": message_ref, "thread_id": thread_id})


def buttons(fc: dict) -> list[str]:
    return ["useful", "noise"] if fc.get("state") == "open" and not fc.get("label") else []


def _pct(x) -> str:
    try:
        return f"{float(x) * 100:.0f}%"
    except (TypeError, ValueError):
        return "?"


def eta_text(fc: dict) -> str:
    d = fc.get("days_to_full")
    if d is None:
        return "no date (a fast rise, not a fill)"
    when = dt.datetime.fromtimestamp(fc["eta_at"], dt.timezone.utc).strftime("%Y-%m-%d") if fc.get("eta_at") else "?"
    return f"about {d:.0f} day(s), around {when}" if d >= 1 else f"about {d * 24:.0f} hour(s)"


def headline(fc: dict) -> str:
    ev = fc.get("evidence", {}).get("evidence", {}) or {}
    if fc.get("kind") == "slow-fill":
        slope = ev.get("slope_per_day")
        rate = f" It is growing about {float(slope) * 100:.1f} points a day." if slope is not None else ""
        return f"At the current trend this reaches its limit ({_pct(ev.get('capacity'))}) in {eta_text(fc)}.{rate}"
    ratio = fc.get("ratio")
    return ("Rising much faster than over the previous day" + (f" (about {ratio:g}x)" if ratio else "") +
            f", {float(ev.get('recent_rate_per_hour', 0)):.3g} per hour." if ev else ".")


def card(fc: dict) -> dict:
    s = logic.sanitize
    d = fc.get("days_to_full")
    colour = COLOUR["quiet"] if fc.get("state") != "open" else COLOUR["hot"] if d is not None and d < 3 else COLOUR["soon"] if d is not None and d < 7 else COLOUR["later"]
    ev = fc.get("evidence", {}).get("evidence", {}) or {}
    fields = [("Signal", SIGNALS.get(fc.get("metric"), s(str(fc.get("metric")), 60)), True), ("Target", f"`{s(str(fc.get('target')), 80)}`", True),
              ("Confidence", f"`{s(str(fc.get('confidence') or '?'), 12)}`", True), ("Estimated", eta_text(fc), False)]
    if ev.get("current") is not None:
        fields.append(("Now", f"{_pct(ev.get('current'))} of a {_pct(ev.get('capacity'))} limit (fit r2 {ev.get('r2', '?')}, {ev.get('points', '?')} points)", False))
    if fc.get("label"):
        fields.append(("Your label", f"`{fc['label']}`", True))
    state = "open" if fc.get("state") == "open" else "no longer forecast"
    return {"title": f"Forecast: {s(str(fc.get('target')), 60)}", "description": ("**A heads-up, not an alert.** " + headline(fc))[:900],
            "fields": fields, "colour": colour, "footer": f"forecast {fc['id']} · {state} · first seen {dt.datetime.fromtimestamp(fc['first_seen'], dt.timezone.utc):%Y-%m-%d}",
            "buttons": buttons(fc)}


def notice(kind: str, fc: dict, data: dict) -> str:
    target = f"`{logic.sanitize(str(fc.get('target')), 60)}`"
    if kind == "escalated":
        return f"**Worse:** {target} is now about {float(data.get('days_to_full', 0)):.0f} day(s) from its limit (was {float(data.get('from_days', 0)):.0f})."
    if kind == "reposted":
        return f"**Still open:** {target}, {eta_text(fc)}."
    if kind == "resolved":
        return f"{target} is no longer forecast (the trend turned or the space was freed)."
    return ""


@dataclass
class Action:
    kind: str  # post_card | edit_card | notice
    fc: dict
    text: str = ""
    key: str = ""


@dataclass
class State:
    path: Path
    cursor: int = 0
    done: set = field(default_factory=set)

    @classmethod
    def load(cls, state_dir: Path) -> "State":
        p = Path(state_dir) / "forecasts-state.json"
        try:
            d = json.loads(p.read_text())
            return cls(p, int(d.get("cursor", 0)), set(d.get("done", [])))
        except (OSError, ValueError):
            return cls(p)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"cursor": self.cursor, "done": sorted(self.done)[-1000:]}))
        tmp.replace(self.path)


def plan(events: list[dict], state: State) -> list[Action]:
    """What to do for one batch of forecast events, decided per event id so a restart never repeats a notice."""
    out: list[Action] = []
    for e in events:
        fc, kind, key = e["forecast"], e["kind"], f"e{e['id']}"
        if key in state.done:
            continue
        if kind == "created":
            if not fc.get("message_ref"):
                out.append(Action("post_card", fc, key=key))
            else:
                out.append(Action("edit_card", fc, key=key))
        elif kind in ("escalated", "reposted", "resolved"):
            if fc.get("message_ref"):
                out.append(Action("edit_card", fc, key=key))
            out.append(Action("notice", fc, notice(kind, fc, e.get("data") or {}), key))
        elif kind == "labeled" and fc.get("message_ref"):
            out.append(Action("edit_card", fc, key=key))
    return out
