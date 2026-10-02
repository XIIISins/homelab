#!/usr/bin/env python3
"""Native Zabbix event (aiops.zabbix-event/v1) -> aiops.alert/v1 alerts.

The analysis path (Phase 10d2): Zabbix's 'n8n (AIOps agent)' media type POSTs
the full event straight to the agent, instead of the flattened Hermod message.
This adapter feeds it through the SAME pipeline as the Hermod path
(normalize.normalize), so routing, the non-prod cap and, importantly, the
fingerprint are identical on both paths: the same problem gets the same
fingerprint whichever way it arrived, which is what makes dedupe and the
resolved half work during the period both paths exist.

What the native event adds over the Hermod wire, carried through unchanged:
  * exact UTC times (the sender converts from the Zabbix server's local zone),
  * `runbook_id` stamped at the source from a trigger tag (wins over the routing
    table when it names a known runbook),
  * trigger id/expression/description, host id/groups, tags, the first item
    values and the operational data, as string labels for the diagnosis session.

Pure functions. The Toolbelt API's /ingest/zabbix is expected to lift this (or
re-implement it against the same fixtures in aiops/fixtures/zabbix-native/).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import normalize  # noqa: E402

# Zabbix severity label -> Hermod tag (the contract normalize.py understands).
# Average is shown for completeness; the media type's bitmask keeps it out.
_TAG = {"Disaster": "critical", "High": "critical", "Average": "alert"}

_LABEL_MAX = 500  # keep labels small; the session reads the schema fields, not megabytes


def _zbx_wire_date(iso_utc: str) -> str:
    """ISO UTC -> the 'YYYY.MM.DD HH:MM:SS' local text normalize() parses (round-trips exactly)."""
    from datetime import datetime, timezone

    dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return dt.astimezone(normalize.ZABBIX_TZ).strftime("%Y.%m.%d %H:%M:%S")


def to_wire(event: dict) -> dict | None:
    """Build the Hermod-shaped wire message normalize() expects, or None if not alertable."""
    tag = _TAG.get(event["severity"])
    if tag is None:
        return None
    resolved = event["status"] == "RESOLVED"
    name = event["trigger_name"]
    sev = event["severity"]
    lines = [f"**Host:** {event['host']}", f"**Severity:** {sev}", ""]
    lines += [
        f"**Status:** {'OK' if resolved else 'PROBLEM'}",
        f"**Trigger:** {name}",
        f"**Severity:** {sev}",
        f"**Host:** {event['host']}",
    ]
    if event.get("fired_at"):
        lines.append(f"**Started at:** {_zbx_wire_date(event['fired_at'])}")
    if resolved and event.get("resolved_at"):
        lines.append(f"**Recovered at:** {_zbx_wire_date(event['resolved_at'])}")
    lines.append(f"**Event ID:** {event['event_id']}")
    if resolved and event.get("recovery_event_id"):
        lines.append(f"**Recovery event ID:** {event['recovery_event_id']}")
    if event.get("opdata"):
        lines += ["", event["opdata"]]
    return {
        "title": f"[Zabbix] RESOLVED: {name}" if resolved else f"[Zabbix] {sev}: {name}",
        "body": "\n".join(lines),
        "type": "success" if resolved else "failure",
        "tag": tag,
        "format": "markdown",
    }


def _clip(s: str) -> str:
    return s if len(s) <= _LABEL_MAX else s[: _LABEL_MAX - 1] + "…"


def _labels(event: dict) -> dict[str, str]:
    out = {"ingest_path": "direct"}
    for key in ("host_id", "host_ip", "host_groups", "trigger_id", "trigger_description", "trigger_expression", "trigger_url", "opdata"):
        if event.get(key):
            out[f"zabbix_{key}"] = _clip(event[key])
    if event.get("tags"):
        out["zabbix_tags"] = _clip(json.dumps(event["tags"], separators=(",", ":"), sort_keys=True))
    if event.get("items"):
        out["zabbix_items"] = _clip(json.dumps(event["items"], separators=(",", ":"), sort_keys=True))
    if event.get("sent_at"):
        out["zabbix_sent_at"] = event["sent_at"]
    return out


def from_zabbix_event(event: dict, received_at: str, routes: list[dict], known_runbooks: set[str] | None = None) -> list[dict]:
    """One native event -> zero or one aiops.alert/v1 alert.

    `known_runbooks`: ids from runbooks.yml. A trigger-tag runbook_id that is in
    this set overrides the routing table's; an unknown id is ignored (and noted in
    labels), so a typo in a Zabbix tag can never point the agent at a runbook that
    does not exist. Pass None to accept any well-formed id (schema-validated).
    """
    wire = to_wire(event)
    if wire is None:
        return []
    alerts = normalize.normalize(wire, received_at, routes)
    for a in alerts:
        # Exact times from the sender win over the text round-trip.
        if event.get("fired_at"):
            a["fired_at"] = event["fired_at"]
        if a["status"] == "resolved" and event.get("resolved_at"):
            a["resolved_at"] = event["resolved_at"]
        a["labels"].update(_labels(event))
        rb = event.get("runbook_id") or ""
        if rb:
            if known_runbooks is None or rb in known_runbooks:
                a["labels"]["runbook_id_routed"] = a["runbook_id"]
                a["runbook_id"] = rb
                a["labels"]["runbook_id_source"] = "trigger-tag"
            else:
                a["labels"]["runbook_id_source"] = "routing"
                a["labels"]["runbook_id_tag_ignored"] = rb
        else:
            a["labels"]["runbook_id_source"] = "routing"
    return alerts


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="aiops.zabbix-event/v1 JSON on stdin -> alerts JSON on stdout")
    ap.add_argument("--received-at", required=True)
    ns = ap.parse_args(argv)
    routes = normalize.load_routes()
    print(json.dumps(from_zabbix_event(json.load(sys.stdin), ns.received_at, routes), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
