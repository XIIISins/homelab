#!/usr/bin/env python3
"""Reference normalizer: Hermod wire payload -> aiops.alert/v1 alerts.

Producers are NOT changed. Every one of them already POSTs Hermod's flat wire
format (docs/services/notifications.md "JSON schema"):

    {"title": ..., "body": ..., "type": ..., "tag": ..., "format": ...}

This module is the documented, tested mapping from that shape to
schema/alert.v1.schema.json, driven by alert-routing.yml. The 10d1 webhook
bridge is expected to lift it (or re-implement it against the same fixtures).

Pure functions, no I/O except the CLI at the bottom. Needs PyYAML only to load
the routing table.

Usage:
    python3 aiops/tools/normalize.py --received-at 2026-10-01T12:00:00Z < wire.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# The Zabbix server's local timezone (Hugin). See _zbx_ts.
ZABBIX_TZ = ZoneInfo("Europe/Amsterdam")

AIOPS_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ROUTING = AIOPS_DIR / "alert-routing.yml"

_KV = re.compile(r"^\*\*(?P<k>[^:*]+):\*\*\s*(?P<v>.*?)\s*$", re.MULTILINE)
_ZBX_TITLE = re.compile(r"^\[Zabbix\]\s+(?P<lead>RESOLVED|[A-Za-z ]+?):\s+(?P<name>.*)$")
# Non-prod (canary pool, Phase 10b1): origin markers. A "[non-prod] " title prefix
# comes from the hermod_summary nonprod-* wrappers; a canary-<n> host from the
# Zabbix webhook. Keep the host pattern in lockstep with
# ansible/roles/zabbix-server/templates/hermod-webhook.js.
NONPROD_PREFIX = "[non-prod] "
_NONPROD_HOST = re.compile(r"^canary-[0-9]+$")
_ZBX_DATE = re.compile(r"^(?P<d>\d{4}\.\d{2}\.\d{2})\s+(?P<t>\d{2}:\d{2}:\d{2})$")


def slug(text: str, maxlen: int = 60) -> str:
    """Lower-case [a-z0-9-] slug; stable across runs (it feeds the fingerprint)."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:maxlen].strip("-")) or "unknown"


def fingerprint(source: str, host: str, service: str, check: str) -> str:
    """Dedupe + audit join key. Same for the firing and resolved halves of a problem."""
    return hashlib.sha256(f"{source}|{host}|{service}|{check}".encode()).hexdigest()[:16]


def parse_kv(body: str) -> dict[str, str]:
    """`**Key:** value` lines; the FIRST occurrence of a key wins.

    Zabbix bodies carry the key twice (the webhook wrapper prepends Host and
    Severity, then the rendered message template repeats them); both agree.
    """
    out: dict[str, str] = {}
    for m in _KV.finditer(body or ""):
        out.setdefault(m.group("k").strip(), m.group("v"))
    return out


def detect_source(title: str) -> str:
    if title.startswith("[Zabbix]"):
        return "zabbix"
    if title.startswith("Infra health"):
        return "s4-prober"
    if title.startswith("Patroni:"):
        return "patroni"
    if title.startswith(("Drift ", "Apply failed")):
        return "semaphore"
    if title.startswith("Frigg:"):
        return "frigg"
    return "unknown"


def _severity(tag: str | None) -> str | None:
    """Hermod tag -> schema severity. None = not an alert (media-only)."""
    tags = {t.strip() for t in (tag or "").split(",") if t.strip()}
    if "critical" in tags:
        return "critical"
    if "alert" in tags:
        return "alert"
    if "info" in tags:
        return "info"  # FYI tier: the cap for non-prod/canary origins
    if not tags:
        return "untagged"
    return None  # e.g. media: a notification, not an alert


def _route(routes: list[dict], source: str, severity: str, subject: str) -> tuple[dict, re.Match]:
    # untagged messages are matched as `alert`-level so `any` catch-alls apply
    want = "alert" if severity == "untagged" else severity
    for r in routes:
        if r["source"] != source:
            continue
        if r["severity"] not in ("any", want):
            continue
        m = re.search(r["match"], subject, re.IGNORECASE)
        if m:
            return r, m
    raise LookupError(f"no route for source={source} severity={severity} subject={subject!r}")


def _expand_check(template: str, m: re.Match, subject: str) -> str:
    if template == "@slug":
        return slug(subject)
    return slug(re.sub(r"\{(\w+)\}", lambda g: m.group(g.group(1)) or "", template))


def _host(route: dict, m: re.Match, fallback: str | None = None) -> str:
    named = m.groupdict().get("host")
    h = named or route.get("host") or fallback or "fleet"
    h = re.sub(r"[^a-z0-9._-]+", "-", h.lower()).strip("-.")
    return h or "fleet"


def _zbx_ts(value: str | None) -> str | None:
    """Zabbix renders {EVENT.DATE} {EVENT.TIME} in the server's LOCAL timezone, no offset.

    Hugin runs Europe/Amsterdam (verified 2026-10-02: `date` -> CEST +0200;
    php timezone in roles/zabbix-server/defaults). Earlier versions assumed UTC,
    which put every Hermod-path timestamp 1-2 h in the future. Native n8n events
    (zabbix_event.py) carry exact UTC already, so this only serves the Hermod wire.
    Ambiguous/nonexistent DST-transition local times resolve with fold=0.
    """
    m = _ZBX_DATE.match((value or "").strip())
    if not m:
        return None
    try:
        local = datetime.strptime(f"{m.group('d')} {m.group('t')}", "%Y.%m.%d %H:%M:%S").replace(tzinfo=ZABBIX_TZ)
    except ValueError:
        return None
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_or(value: str | None, default: str) -> str:
    if value:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            pass
    return default


def normalize(wire: dict, received_at: str, routes: list[dict]) -> list[dict]:
    """One Hermod POST -> zero or more alerts (the S4 prober bundles findings)."""
    title = str(wire.get("title", "")).strip()
    nonprod_title = title.startswith(NONPROD_PREFIX)
    if nonprod_title:
        title = title[len(NONPROD_PREFIX):]  # route on the underlying message
    body = str(wire.get("body", ""))
    severity = _severity(wire.get("tag"))
    if severity is None:
        return []
    source = detect_source(title)
    kv = parse_kv(body)
    labels = {"apprise_tag": str(wire.get("tag") or ""), "apprise_type": str(wire.get("type") or "")}

    # (subject matched against routes, summary, detail) per resulting alert
    items: list[tuple[str, str, str]]
    status = "firing"
    resolved_at: str | None = None
    fired_at = received_at
    event_id: str | None = None
    native_sev: str | None = None
    zbx_host: str | None = None

    if source == "zabbix":
        mt = _ZBX_TITLE.match(title)
        trigger = kv.get("Trigger") or (mt.group("name") if mt else title)
        items = [(trigger, title, body.strip())]
        resolved = bool(mt and mt.group("lead") == "RESOLVED") or kv.get("Status") == "OK"
        status = "resolved" if resolved else "firing"
        native_sev = kv.get("Severity")
        zbx_host = kv.get("Host")
        event_id = kv.get("Event ID") or kv.get("Recovery event ID")
        fired_at = _zbx_ts(kv.get("Started at")) or received_at
        if resolved:
            resolved_at = _zbx_ts(kv.get("Recovered at")) or received_at
    elif source == "s4-prober" and not title.startswith("Infra health check ERRORED"):
        findings = [ln[2:].strip() for ln in body.splitlines() if ln.startswith("- ")]
        items = [(f, re.sub(r"[*`]", "", f)[:200], f) for f in findings]
        if not items:  # a bundle with no parsable lines still must not vanish
            items = [(title, title, body.strip())]
    else:
        items = [(title, title, body.strip())]
        if source == "patroni":
            fired_at = _iso_or(kv.get("Timestamp"), received_at)

    alerts: list[dict] = []
    for subject, summary, detail in items:
        route, m = _route(routes, source, severity, subject)
        host = _host(route, m, zbx_host)
        service = route["service"]
        check = _expand_check(route["check"], m, subject)
        alert_status = "event" if route.get("status") == "event" else status
        # Non-prod origin: label it, keep its fingerprint apart from the prod
        # twin ("fleet" -> "nonprod"), and never let it out above `info` even if
        # a producer tagged it critical/alert (defence in depth behind the producer caps).
        nonprod = nonprod_title or bool(_NONPROD_HOST.match(host))
        if nonprod_title and host == "fleet":
            host = "nonprod"
        out_severity = "info" if (nonprod and severity in ("critical", "alert")) else severity
        a: dict = {
            "schema_version": "aiops.alert/v1",
            "source": source,
            "status": alert_status,
            "severity": out_severity,
            "host": host,
            "service": service,
            "check": check,
            "summary": (NONPROD_PREFIX + summary) if nonprod_title else summary,
            "detail": detail,
            "runbook_id": route["runbook_id"],
            "fingerprint": fingerprint(source, host, service, check),
            "fired_at": fired_at,
            "received_at": received_at,
            "labels": dict(labels, aiops_canary="true") if nonprod else dict(labels),
        }
        if native_sev:
            a["native_severity"] = native_sev
        if event_id:
            a["event_id"] = event_id
        if alert_status == "resolved":
            a["resolved_at"] = resolved_at or received_at
        alerts.append(a)
    return alerts


def load_routes(path: Path = DEFAULT_ROUTING) -> list[dict]:
    import yaml  # local import: only the CLI/lint need it

    return yaml.safe_load(path.read_text(encoding="utf-8"))["routes"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--routing", type=Path, default=DEFAULT_ROUTING)
    ap.add_argument(
        "--received-at",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        help="ISO-8601 receive time (default: now, UTC)",
    )
    args = ap.parse_args(argv)
    wire = json.load(sys.stdin)
    json.dump(normalize(wire, args.received_at, load_routes(args.routing)), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
