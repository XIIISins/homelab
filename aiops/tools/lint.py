#!/usr/bin/env python3
"""Consistency linter for aiops/ (Phase 10c exit criteria, machine-checked).

Checks, in order:
  schemas    runbooks.yml / actions.yml / alert-routing.yml validate against
             their JSON Schemas; every fixture validates (or, under
             fixtures/invalid/, fails the way it says it should)
  runbooks   unique ids; the doc marker `<!-- runbook: RB-XXX -->` exists exactly
             once, in the declared source file, and no orphan markers exist;
             tier/automatable rules (T3 -> none, T2 <= approval, auto needs a
             proof + T<=1); remediation iff automatable; action references
             resolve with compatible tiers/autonomy; verify commands are
             read-only; cited incident files exist
  actions    every action has a tier and a verify step; verify.action resolves
             to a T0 action; mutators are never `auto` and T3 is never above
             `none`; Semaphore template exists in terraform/semaphore/templates.tf
             and points at the same playbook, which exists and emits
             AIOPS_RESULT; fixed_vars / task_fields reference declared
             extra_vars; allow-listed hosts are in host_tiers.T1
  routing    route ids unique; regexes compile; runbook ids resolve; every
             source ends in a catch-all; no critical-capable route without a
             runbook_id (the 10c exit criterion "every critical alert path
             carries a runbook_id")
  fixtures   normalize(wire) == expected, fingerprints recompute

Needs PyYAML + jsonschema (aiops/requirements.txt). Run from anywhere:
    python3 aiops/tools/lint.py
Exit 0 = clean, 1 = findings (printed as `ERROR <check>: <detail>`).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import normalize  # noqa: E402

AIOPS = Path(__file__).resolve().parent.parent
ROOT = AIOPS.parent

AUTONOMY = {"none": 0, "approval": 1, "auto": 2}
TIER = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}
MARKER = re.compile(r"<!--\s*runbook:\s*(RB-[A-Z0-9]+(?:-[A-Z0-9]+)*)\s*-->")

# A verify command must be safe to run any time. This is a tripwire for the
# obvious mutators, not a sandbox; reviewers still read the diff.
_MUTATING = re.compile(
    r"(?<![\w-])(rm|mv|cp|dd|tee|kill|pkill|reboot|shutdown|poweroff"
    r"|systemctl\s+(?:start|stop|restart|reload|enable|disable|mask|kill|reset-failed)"
    r"|kubectl\s+(?:apply|delete|patch|replace|edit|scale|drain|cordon|uncordon|label|annotate|taint|rollout|create)"
    r"|flux\s+(?:reconcile|suspend|resume|create|delete)"
    r"|terraform\s+(?:apply|destroy|import)"
    r"|helm\s+(?:install|upgrade|uninstall|rollback)"
    r"|vault\s+(?:write|delete|kv\s+(?:put|patch|delete))"
    r"|op\s+item\s+(?:edit|create|delete)"
    r"|git\s+(?:push|commit|reset))(?![\w-])"
)


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_schema(name: str) -> dict:
    return json.loads((AIOPS / "schema" / name).read_text(encoding="utf-8"))


def schema_errors(doc, schema: dict) -> list[str]:
    v = Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in sorted(v.iter_errors(doc), key=str)]


def _is_ts(s: str) -> bool:
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except (ValueError, AttributeError):
        return False


def alert_errors(alert: dict, schema: dict) -> list[str]:
    """Schema + the checks JSON Schema cannot express (date-time, fingerprint)."""
    errs = schema_errors(alert, schema)
    for k in ("fired_at", "resolved_at", "received_at"):
        if k in alert and not _is_ts(alert[k]):
            errs.append(f"{k}: not an ISO-8601 date-time")
    try:
        want = normalize.fingerprint(alert["source"], alert["host"], alert["service"], alert["check"])
        if alert.get("fingerprint") != want:
            errs.append(f"fingerprint: {alert.get('fingerprint')} != sha256(source|host|service|check)[:16] = {want}")
    except KeyError:
        pass
    return errs


def parse_templates(tf_text: str) -> dict[str, str]:
    """template name -> playbook path, from terraform/semaphore/templates.tf (regex, no HCL parser)."""
    out: dict[str, str] = {}
    for m in re.finditer(r'resource "semaphoreui_project_template" "\w+" \{\n(?P<body>.*?)\n\}\n', tf_text, re.S):
        body = m.group("body")
        n = re.search(r'^\s*name\s*=\s*"([^"]+)"', body, re.M)
        p = re.search(r'^\s*playbook\s*=\s*"([^"]+)"', body, re.M)
        if n and p:
            out[n.group(1)] = p.group(1)
    return out


def doc_markers(root: Path) -> dict[str, list[str]]:
    """runbook id -> repo-relative files containing its marker (one entry per occurrence)."""
    found: dict[str, list[str]] = {}
    for md in sorted((root / "docs").rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        # a marker QUOTED in prose (fenced block or `inline code`) documents the
        # convention; it is not an anchor
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        text = re.sub(r"`[^`\n]*`", "", text)
        for m in MARKER.finditer(text):
            found.setdefault(m.group(1), []).append(str(md.relative_to(root)))
    return found


# --- individual checks --------------------------------------------------------


def check_actions(reg: dict, root: Path) -> list[str]:
    errs: list[str] = []
    actions = reg["actions"]
    tf = parse_templates((root / "terraform/semaphore/templates.tf").read_text(encoding="utf-8"))
    t1 = set(reg["host_tiers"]["T1"])
    for name, a in actions.items():
        tier, auto = a["tier"], a["max_autonomy"]
        if tier == "T3" and auto != "none":
            errs.append(f"actions: {name}: T3 action must be max_autonomy none")
        if tier == "T2" and AUTONOMY[auto] > AUTONOMY["approval"]:
            errs.append(f"actions: {name}: T2 action may not exceed approval")
        if tier != "T0" and auto == "auto":
            errs.append(f"actions: {name}: mutating action may not be auto until the 10f1 guards exist")
        if auto != "none" and not a["idempotent"]:
            errs.append(f"actions: {name}: non-idempotent action cannot have max_autonomy {auto}")
        if a["idempotent"] and not a.get("idempotency"):
            errs.append(f"actions: {name}: idempotent: true needs an `idempotency` justification")

        # verify
        v = a["verify"]
        if "action" in v:
            tgt = actions.get(v["action"])
            if tgt is None:
                errs.append(f"actions: {name}: verify.action {v['action']!r} is not a registry action")
            elif tgt["tier"] != "T0":
                errs.append(f"actions: {name}: verify.action {v['action']!r} must be T0 (read-only)")
            else:
                for k in v.get("vars", {}):
                    if k not in tgt["extra_vars"]:
                        errs.append(f"actions: {name}: verify.vars.{k} is not an extra_var of {v['action']}")
        if "self" in v and v["expect"].get("ok") is not True:
            errs.append(f"actions: {name}: a self-verifying action must expect ok: true (its playbook performs the post-condition read)")

        # typed vars
        for vn, spec in a["extra_vars"].items():
            if spec["type"] == "enum" and "values" not in spec:
                errs.append(f"actions: {name}: extra_var {vn}: enum needs values")
            if spec["type"] == "string" and "pattern" in spec:
                try:
                    re.compile(spec["pattern"])
                except re.error as e:
                    errs.append(f"actions: {name}: extra_var {vn}: bad pattern ({e})")
        for fv in a.get("fixed_vars", {}):
            if fv in a["extra_vars"]:
                errs.append(f"actions: {name}: {fv} is both fixed and an extra_var")
        refs = re.findall(r"\{(\w+)\}", json.dumps(a["semaphore"].get("task_fields", {})))
        refs += re.findall(r"\{(\w+)\}", json.dumps(v.get("vars", {})))
        for r in refs:
            if r not in a["extra_vars"]:
                errs.append(f"actions: {name}: placeholder {{{r}}} is not a declared extra_var")

        # guard
        g = a["guard"]
        for host in g.get("allowed_units", {}):
            if host not in t1:
                errs.append(f"actions: {name}: guard.allowed_units host {host!r} is not in host_tiers.T1")
        if tier != "T0" and g["target_policy"] == "host_tiers.T1" and "allowed_units" not in g and "allowed_tags" not in g:
            errs.append(f"actions: {name}: host_tiers.T1 policy needs allowed_units or allowed_tags")
        if "requires_prior" in g and g["requires_prior"] not in actions:
            errs.append(f"actions: {name}: guard.requires_prior {g['requires_prior']!r} is not a registry action")

        # semaphore template + playbook
        sem = a["semaphore"]
        if sem["template"] not in tf:
            errs.append(f"actions: {name}: Semaphore template {sem['template']!r} not defined in terraform/semaphore/templates.tf")
        elif tf[sem["template"]] != sem["playbook"]:
            errs.append(f"actions: {name}: template {sem['template']!r} runs {tf[sem['template']]!r}, registry says {sem['playbook']!r}")
        pb = root / sem["playbook"]
        if not pb.is_file():
            errs.append(f"actions: {name}: playbook {sem['playbook']} does not exist")
        elif "AIOPS_RESULT" not in pb.read_text(encoding="utf-8"):
            errs.append(f"actions: {name}: playbook {sem['playbook']} never emits AIOPS_RESULT (verify cannot be evaluated)")
    return errs


def check_runbooks(rb_doc: dict, reg: dict, root: Path) -> list[str]:
    errs: list[str] = []
    actions = reg["actions"]
    markers = doc_markers(root)
    seen: set[str] = set()
    for rb in rb_doc["runbooks"]:
        rid = rb["id"]
        if rid in seen:
            errs.append(f"runbooks: duplicate id {rid}")
        seen.add(rid)
        src = root / rb["source"]
        if not src.is_file():
            errs.append(f"runbooks: {rid}: source {rb['source']} does not exist")
        where = markers.get(rid, [])
        if where != [rb["source"]]:
            errs.append(f"runbooks: {rid}: marker must appear exactly once, in {rb['source']} (found in {where or 'nowhere'})")

        tier, auto = rb["tier"], rb["automatable"]
        if tier == "T3" and auto != "none":
            errs.append(f"runbooks: {rid}: T3 runbook must be automatable none (is {auto})")
        if tier == "T2" and AUTONOMY[auto] > AUTONOMY["approval"]:
            errs.append(f"runbooks: {rid}: T2 runbook may not exceed approval (is {auto})")
        if auto == "auto":
            proof = rb.get("idempotency_proof")
            if TIER[tier] > TIER["T1"]:
                errs.append(f"runbooks: {rid}: auto requires tier <= T1")
            if not proof or not (root / proof).exists():
                errs.append(f"runbooks: {rid}: auto requires an idempotency_proof path that exists")
        if auto != "auto" and "idempotency_proof" in rb:
            errs.append(f"runbooks: {rid}: idempotency_proof is only meaningful with automatable: auto")

        rem = rb.get("remediation", [])
        if auto == "none" and rem:
            errs.append(f"runbooks: {rid}: automatable none but remediation actions listed")
        if auto != "none" and not rem:
            errs.append(f"runbooks: {rid}: automatable {auto} needs at least one remediation action")
        for an in rem:
            a = actions.get(an)
            if a is None:
                errs.append(f"runbooks: {rid}: remediation action {an!r} is not in the registry")
                continue
            if TIER[a["tier"]] > TIER[tier]:
                errs.append(f"runbooks: {rid}: remediation {an} (tier {a['tier']}) exceeds the runbook tier {tier}")
            if AUTONOMY[a["max_autonomy"]] < AUTONOMY[auto]:
                errs.append(f"runbooks: {rid}: automatable {auto} but action {an} caps at {a['max_autonomy']}")
        for an in rb.get("diagnostics", []):
            a = actions.get(an)
            if a is None:
                errs.append(f"runbooks: {rid}: diagnostics action {an!r} is not in the registry")
            elif a["tier"] != "T0":
                errs.append(f"runbooks: {rid}: diagnostics action {an} must be T0")

        v = rb["verify"]
        if "action" in v:
            a = actions.get(v["action"])
            if a is None:
                errs.append(f"runbooks: {rid}: verify.action {v['action']!r} is not in the registry")
            elif a["tier"] != "T0":
                errs.append(f"runbooks: {rid}: verify.action {v['action']} must be T0")
        else:
            cmd = v["command"]
            if _MUTATING.search(cmd):
                errs.append(f"runbooks: {rid}: verify command looks mutating: {_MUTATING.search(cmd).group(0)!r}")
            if re.search(r"ansible-playbook", cmd) and "--check" not in cmd:
                errs.append(f"runbooks: {rid}: verify runs ansible-playbook without --check")
            if re.search(r"(?i)(password|token|secret)\s*=\s*['\"]?[A-Za-z0-9+/_-]{12,}", cmd):
                errs.append(f"runbooks: {rid}: verify command appears to embed a credential")
        for inc in rb["selection"].get("incidents", []):
            if not (root / inc).is_file():
                errs.append(f"runbooks: {rid}: cited incident {inc} does not exist")
    for rid, files in markers.items():
        if rid not in seen:
            errs.append(f"runbooks: orphan marker {rid} in {files} has no entry in aiops/runbooks.yml")
    return errs


def check_routing(rt_doc: dict, rb_doc: dict) -> list[str]:
    errs: list[str] = []
    ids = {r["id"] for r in rb_doc["runbooks"]}
    seen: set[str] = set()
    by_source: dict[str, list[dict]] = {}
    for r in rt_doc["routes"]:
        if r["id"] in seen:
            errs.append(f"routing: duplicate route id {r['id']}")
        seen.add(r["id"])
        try:
            re.compile(r["match"])
        except re.error as e:
            errs.append(f"routing: {r['id']}: bad regex ({e})")
        rid = r["runbook_id"]
        if rid is not None and rid not in ids:
            errs.append(f"routing: {r['id']}: runbook_id {rid} is not in runbooks.yml")
        if r["severity"] in ("critical", "any") and rid is None:
            errs.append(f"routing: {r['id']}: critical-capable route has no runbook_id")
        by_source.setdefault(r["source"], []).append(r)
    for src in ("zabbix", "s4-prober", "patroni", "semaphore", "frigg", "unknown"):
        rs = by_source.get(src)
        if not rs:
            errs.append(f"routing: source {src} has no routes")
            continue
        last = rs[-1]
        if last["match"] != ".*" or last["severity"] not in ("any", "critical"):
            errs.append(f"routing: source {src} must end with a catch-all (match '.*', severity any|critical); last is {last['id']}")
    return errs


def check_fixtures(root: Path, routes: list[dict]) -> list[str]:
    errs: list[str] = []
    alert_schema = load_schema("alert.v1.schema.json")
    for p in sorted((AIOPS / "fixtures" / "cases").glob("*.json")):
        case = json.loads(p.read_text(encoding="utf-8"))
        got = normalize.normalize(case["wire"], case["received_at"], routes)
        if got != case["expected"]:
            errs.append(f"fixtures: {p.name}: normalize(wire) != expected\n  got:      {json.dumps(got, sort_keys=True)}\n  expected: {json.dumps(case['expected'], sort_keys=True)}")
        for i, alert in enumerate(case["expected"]):
            for e in alert_errors(alert, alert_schema):
                errs.append(f"fixtures: {p.name}: expected[{i}]: {e}")
    for p in sorted((AIOPS / "fixtures" / "invalid").glob("*.json")):
        case = json.loads(p.read_text(encoding="utf-8"))
        got = alert_errors(case["alert"], alert_schema)
        if not got:
            errs.append(f"fixtures: invalid/{p.name}: validates but must fail")
        elif not any(case["must_fail_with"] in e for e in got):
            errs.append(f"fixtures: invalid/{p.name}: fails, but not with {case['must_fail_with']!r}: {got}")
    return errs


def run(root: Path = ROOT) -> list[str]:
    errs: list[str] = []
    docs = {
        "runbooks.yml": ("runbooks.v1.schema.json", load_yaml(AIOPS / "runbooks.yml")),
        "actions.yml": ("actions.v1.schema.json", load_yaml(AIOPS / "actions.yml")),
        "alert-routing.yml": ("routing.v1.schema.json", load_yaml(AIOPS / "alert-routing.yml")),
    }
    schema_ok = True
    for fname, (sch, doc) in docs.items():
        for e in schema_errors(doc, load_schema(sch)):
            errs.append(f"schemas: {fname}: {e}")
            schema_ok = False
    if not schema_ok:  # cross-file checks assume well-formed documents
        return errs
    rb, reg, rt = docs["runbooks.yml"][1], docs["actions.yml"][1], docs["alert-routing.yml"][1]
    errs += check_actions(reg, root)
    errs += check_runbooks(rb, reg, root)
    errs += check_routing(rt, rb)
    errs += check_fixtures(root, rt["routes"])
    return errs


def main() -> int:
    errs = run()
    for e in errs:
        print(f"ERROR {e}")
    reg = load_yaml(AIOPS / "actions.yml")
    rb = load_yaml(AIOPS / "runbooks.yml")
    pending = [n for n, a in reg["actions"].items() if not a["semaphore"]["applied"]]
    print(
        f"aiops lint: {len(rb['runbooks'])} runbooks, {len(reg['actions'])} actions, "
        f"{len(errs)} error(s); {len(pending)} action(s) wait on a terraform/semaphore apply: {', '.join(pending) or '-'}"
    )
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
