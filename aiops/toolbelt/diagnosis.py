"""Validate and render a diagnosis (aiops.diagnosis/v1), Phase 10d3.

Two layers of checking, both dependency-free so they run on Frigg as-is:

  1. structure: the same rules as aiops/schema/diagnosis.v1.schema.json (a test asserts the two
     agree on the fixtures, so they cannot drift apart);
  2. grounding: the checks that need state. Every piece of evidence must correspond to a tool call the
     Toolbelt itself served for THIS incident (same tool, same arguments), referenced runbooks,
     actions and docs must exist, and no string may contain anything secret-shaped. This is what
     turns "the model says it looked at X" into "the audit log shows it did".

A diagnosis that fails is not posted as an analysis: the caller gets the list of problems (HTTP 422),
and the workflow can retry once or fall back to a plain "no analysis" post. Nothing is executed
from a diagnosis, ever; proposed actions are labelled as proposals in the rendering.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

LAYERS = ("host", "hypervisor", "workload", "network", "drift", "external", "unknown")
CONFIDENCE = ("high", "medium", "low")
TOP_KEYS = {"schema_version", "incident_id", "layer", "confidence", "summary", "reasoning", "evidence",
            "known_issue_refs", "runbook_id", "proposed_actions", "next_checks", "needs_human"}
REQUIRED = ("schema_version", "incident_id", "layer", "confidence", "summary", "evidence", "needs_human")
DOC_REF = re.compile(r"^docs/(known-issues|incidents|procedures)/[A-Za-z0-9._-]+\.md$")
TOOL_NAME = re.compile(r"^[a-z]+\.[a-z_]+$")
RUNBOOK = re.compile(r"^RB-[A-Z0-9-]+$")
ACTION = re.compile(r"^[a-z0-9-]+$")

SECRETISH = [
    (re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[\w-]+"), "a Discord webhook URL"),
    (re.compile(r"sk-ant-[\w-]{10,}"), "an Anthropic API key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"), "a bearer token"),
    (re.compile(r"\bhvs\.[A-Za-z0-9]{16,}"), "a Vault token"),
    (re.compile(r"PVEAPIToken=\S+"), "a Proxmox API token"),
    (re.compile(r"\bpassword\s*[=:]\s*\S{6,}", re.I), "a password"),
]


def _strings(node, path="$"):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _strings(v, f"{path}[{i}]")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def structure(d: object) -> list[str]:
    """Mirror of diagnosis.v1.schema.json."""
    if not isinstance(d, dict):
        return ["diagnosis must be a JSON object"]
    p: list[str] = []
    for k in sorted(set(d) - TOP_KEYS):
        p.append(f"unexpected field {k!r}")
    for k in REQUIRED:
        if k not in d:
            p.append(f"missing {k}")
    if d.get("schema_version") != "aiops.diagnosis/v1":
        p.append("schema_version must be aiops.diagnosis/v1")
    if "incident_id" in d and not (_is_int(d["incident_id"]) and d["incident_id"] >= 1):
        p.append("incident_id must be a positive integer")
    if "layer" in d and d["layer"] not in LAYERS:
        p.append(f"layer must be one of {list(LAYERS)}")
    if "confidence" in d and d["confidence"] not in CONFIDENCE:
        p.append(f"confidence must be one of {list(CONFIDENCE)}")
    s = d.get("summary")
    if "summary" in d and not (isinstance(s, str) and 10 <= len(s) <= 600):
        p.append("summary must be a string of 10..600 characters")
    if "reasoning" in d and not (isinstance(d["reasoning"], str) and len(d["reasoning"]) <= 1500):
        p.append("reasoning must be a string up to 1500 characters")
    if "needs_human" in d and not isinstance(d["needs_human"], bool):
        p.append("needs_human must be a boolean")
    ev = d.get("evidence")
    if "evidence" in d:
        if not isinstance(ev, list) or len(ev) > 12:
            p.append("evidence must be a list of at most 12 items")
        else:
            for i, e in enumerate(ev):
                if not isinstance(e, dict) or set(e) != {"tool", "args", "finding"}:
                    p.append(f"evidence[{i}] must have exactly tool, args, finding")
                    continue
                if not (isinstance(e["tool"], str) and TOOL_NAME.match(e["tool"])):
                    p.append(f"evidence[{i}].tool is not a tool name")
                if not isinstance(e["args"], dict):
                    p.append(f"evidence[{i}].args must be an object")
                if not (isinstance(e["finding"], str) and 3 <= len(e["finding"]) <= 300):
                    p.append(f"evidence[{i}].finding must be 3..300 characters")
            if d.get("layer") not in (None, "unknown") and not ev:
                p.append("a diagnosis that names a layer needs at least one piece of evidence")
    refs = d.get("known_issue_refs")
    if "known_issue_refs" in d:
        if not isinstance(refs, list) or len(refs) > 5 or not all(isinstance(r, str) and DOC_REF.match(r) for r in refs):
            p.append("known_issue_refs must be up to 5 docs/(known-issues|incidents|procedures)/*.md paths")
    if "runbook_id" in d and not (isinstance(d["runbook_id"], str) and RUNBOOK.match(d["runbook_id"])):
        p.append("runbook_id must look like RB-...")
    pa = d.get("proposed_actions")
    if "proposed_actions" in d:
        if not isinstance(pa, list) or len(pa) > 3:
            p.append("proposed_actions must be a list of at most 3")
        else:
            for i, a in enumerate(pa):
                if not isinstance(a, dict) or not {"action_id", "reason"} <= set(a) <= {"action_id", "reason", "params"}:
                    p.append(f"proposed_actions[{i}] must have action_id, reason and optionally params")
                    continue
                if "params" in a and not (isinstance(a["params"], dict) and len(a["params"]) <= 6):
                    p.append(f"proposed_actions[{i}].params must be an object of at most 6 entries")
                if not (isinstance(a["action_id"], str) and ACTION.match(a["action_id"])):
                    p.append(f"proposed_actions[{i}].action_id is malformed")
                if not (isinstance(a["reason"], str) and 3 <= len(a["reason"]) <= 300):
                    p.append(f"proposed_actions[{i}].reason must be 3..300 characters")
    nc = d.get("next_checks")
    if "next_checks" in d:
        if not isinstance(nc, list) or len(nc) > 5 or not all(isinstance(x, str) and 3 <= len(x) <= 200 for x in nc):
            p.append("next_checks must be up to 5 strings of 3..200 characters")
    return p


def grounding(d: dict, *, incident_id: int, served: Callable[[str, dict], bool], known_runbooks: set[str] | None,
              action_ids: set[str] | None, repo_dir: Path | None) -> list[str]:
    """The checks that need state. `served(tool, args)` says whether the Toolbelt answered that exact call for this incident."""
    p: list[str] = []
    if d.get("incident_id") != incident_id:
        p.append(f"incident_id {d.get('incident_id')!r} does not match the incident being diagnosed ({incident_id})")
    for i, e in enumerate(d.get("evidence", [])):
        if not served(e["tool"], e["args"]):
            p.append(f"evidence[{i}] cites {e['tool']} {e['args']} but the Toolbelt served no such call for this incident")
    rb = d.get("runbook_id")
    if rb and known_runbooks is not None and rb not in known_runbooks:
        p.append(f"runbook_id {rb} is not in the runbook registry")
    for i, a in enumerate(d.get("proposed_actions", [])):
        if action_ids is not None and a["action_id"] not in action_ids:
            p.append(f"proposed_actions[{i}].action_id {a['action_id']!r} is not in the action registry")
    if repo_dir is not None:
        for r in d.get("known_issue_refs", []):
            if not (repo_dir / r).is_file():
                p.append(f"known_issue_refs: {r} does not exist in the repo")
    for path, text in _strings(d):
        for pat, what in SECRETISH:
            if pat.search(text):
                p.append(f"{path} contains {what}")
    return p


_LAYER_LABEL = {"host": "the host itself", "hypervisor": "the hypervisor", "workload": "the workload",
                "network": "the network", "drift": "drift from IaC", "external": "something external",
                "unknown": "unknown"}


def _target(a: dict) -> str:
    return " ".join(str(v) for v in (a.get("params") or {}).values())[:80]


def render(d: dict, *, alert_count: int = 1, model: str = "", tool_calls: int = 0, proposals: list | None = None,
           refused: list | None = None, autos: list | None = None) -> str:
    """Discord-ready markdown, under the 2000-character limit. No mentions are ever produced."""
    lines = [f"**Likely layer: {_LAYER_LABEL[d['layer']]}** - confidence {d['confidence']}"
             + (" - needs a human" if d["needs_human"] else ""), "", d["summary"]]
    if d.get("reasoning"):
        lines += ["", d["reasoning"]]
    if d["evidence"]:
        lines += ["", "**Evidence**"] + [f"- `{e['tool']}` {e['finding']}" for e in d["evidence"]]
    if d.get("known_issue_refs"):
        lines += ["", "**See**"] + [f"- {r}" for r in d["known_issue_refs"]]
    if d.get("runbook_id"):
        lines += ["", f"Runbook: `{d['runbook_id']}`"]
    if d.get("proposed_actions"):
        live = bool(proposals) and any(proposals)
        any_auto = bool(autos) and any(a and a.get("auto") for a in autos)
        lines += ["", ("**Proposed actions (the operator approves each one in this thread; those marked automatic run under a reviewed policy)**" if any_auto
                       else "**Proposed actions (nothing runs unless the operator approves each one in this thread)**") if live
                  else "**Proposed actions (proposals only - nothing was executed)**"]
        for i, a in enumerate(d["proposed_actions"]):
            pid = proposals[i].get("number", proposals[i]["id"]) if proposals and i < len(proposals) and proposals[i] else None
            tgt = _target(a)
            auto = autos[i] if autos and i < len(autos) and autos[i] and autos[i].get("auto") else None
            lines.append(f"- `{a['action_id']}`" + (f" {tgt}" if tgt else "") + f": {a['reason']}" + (f" (proposal #{pid}" if pid else "")
                         + (f", running automatically by policy `{auto['policy']}`" if auto else "") + (")" if pid else ""))
        for r in refused or []:
            lines.append(f"- `{r['action_id']}` was NOT proposed: {r['why']}")
    if d.get("next_checks"):
        lines += ["", "**Next checks**"] + [f"- {c}" for c in d["next_checks"]]
    foot = f"incident #{d['incident_id']} - {alert_count} alert(s) - {tool_calls} tool call(s)" + (f" - {model}" if model else "")
    lines += ["", f"_{foot}_"]
    text = "\n".join(lines)
    return text if len(text) <= 1900 else text[:1880].rstrip() + "\n...(truncated)_"
