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
  zabbix-native  (10d2) every fixtures/zabbix-native case: the event validates against
             zabbix-event.v1, zabbix_event.from_zabbix_event(event) == expected, the
             expected alerts validate; and the keys the n8n-webhook.js sender emits
             equal the schema's properties (script and contract cannot drift)
  rightsizing (10i) aiops/rightsizing.yml validates against its schema; floors and limit headroom are consistent
  n8n        (10d) every aiops/n8n/workflows/*.json: valid JSON with a fixed id, only
             allow-listed node types (no Code / Execute-Command / SSH), no inline
             secrets (the repo is public), credentials are id+name references,
             HTTP URLs are $env.AIOPS_* or a known host, `aiops/<source>` webhooks
             are header-authenticated and respond immediately, and each ingest
             source agrees across the workflow, the n8n-agent role and terraform/vault

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
import zabbix_event  # noqa: E402

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
            pols = list((reg.get("autonomy") or {}).get("policies", {}).values()) + list((reg.get("rebuild") or {}).get("policies", {}).values())
            covered = any(p.get("action") == name for p in pols)
            if tier != "T1" or not covered:
                errs.append(f"actions: {name}: mutating action may not be auto unless it is T1 and an autonomy or rebuild policy covers it (10f/10g)")
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
            elif tgt["tier"] != "T0" and not (tgt.get("internal") and tgt.get("guard", {}).get("target_policy") == "canaries"
                                              and tgt.get("idempotent")):
                # 10h2: the PR canary test's second dry run executes the PR's own code, so it is T1 by honesty, not T0; it is allowed
                # as a verify step only because it is Toolbelt-internal, idempotent and confined to the disposable canaries.
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
        if a.get("scope") == "rebuild" and "target" not in a["extra_vars"]:
            errs.append(f"actions: {name}: scope rebuild needs a `target` extra_var")
        names = [s["name"] for s in a.get("steps", [])]
        if len(set(names)) != len(names):
            errs.append(f"actions: {name}: step names must be unique")
        for s in a.get("steps", []):
            if s.get("backend", "semaphore") == "runner" and s["name"] not in ("plan", "apply"):
                errs.append(f"actions: {name}: step {s['name']}: the runner only does plan and apply")
            if s.get("action") and (s["action"] not in actions or actions[s["action"]]["tier"] != "T0"):
                errs.append(f"actions: {name}: step {s['name']}: action {s['action']!r} must be a T0 registry action")
        if "apply" in names and ("plan" not in names or names.index("plan") > names.index("apply")):
            errs.append(f"actions: {name}: an apply step needs a plan step before it (the apply is bound to the plan)")
        if "verify" in names and names[-1] != "verify":
            errs.append(f"actions: {name}: verify must be the last step")
        if a.get("steps") and a["tier"] == "T0" and "apply" in names:
            errs.append(f"actions: {name}: a T0 action cannot apply")

        # semaphore template + playbook
        sem = a["semaphore"]
        if sem.get("runner_only"):
            if not a.get("steps") or any(s.get("backend", "semaphore") not in ("runner", "burst") for s in a["steps"]):
                errs.append(f"actions: {name}: semaphore.runner_only needs steps that all use backend: runner (or burst)")
            if sem.get("planned"):
                errs.append(f"actions: {name}: semaphore.runner_only cannot also be planned")
            continue
        if sem.get("planned"):
            # 10g: template and playbook are planned, not written. Never let a planned action look runnable.
            if sem["applied"]:
                errs.append(f"actions: {name}: semaphore.planned needs applied: false")
            continue
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


PRECHECK_ACTION = {"unit-not-active": "restart-unit", "helmrelease-stalled": "flux-reconcile-reset", "drift-present": "replay-role"}


def check_autonomy(reg: dict, rb_doc: dict) -> list[str]:
    """The autonomy section (10f): its scope, its limits and every policy must be consistent with the registry and the
    runbooks, because this is the file a reviewer reads to know what the loop may do by itself."""
    errs: list[str] = []
    au = reg.get("autonomy")
    if not au:
        return errs
    actions = reg["actions"]
    t1 = set(reg["host_tiers"]["T1"])
    runbooks = {r["id"]: r for r in rb_doc["runbooks"]}
    for h in au["hosts"]:
        if h not in t1:
            errs.append(f"autonomy: host {h!r} is not in host_tiers.T1 (autonomy only ever acts on T1)")
    for pname, p in au["policies"].items():
        a = actions.get(p["action"])
        if a is None:
            errs.append(f"autonomy: policy {pname}: action {p['action']!r} is not a registry action")
            continue
        if a["tier"] != "T1":
            errs.append(f"autonomy: policy {pname}: action {p['action']} is tier {a['tier']}; autonomy covers T1 only")
        if not a["idempotent"]:
            errs.append(f"autonomy: policy {pname}: action {p['action']} is not idempotent")
        if PRECHECK_ACTION.get(p["precheck"]) != p["action"]:
            errs.append(f"autonomy: policy {pname}: precheck {p['precheck']} does not belong to action {p['action']} (expects {PRECHECK_ACTION.get(p['precheck'])})")
        if "max_changed" in p and p["precheck"] != "drift-present":
            errs.append(f"autonomy: policy {pname}: max_changed only applies to the drift-present precheck")
        if p["precheck"] == "drift-present" and "max_changed" not in p:
            errs.append(f"autonomy: policy {pname}: the drift-present precheck needs max_changed (the diff scope gate)")
        rb = runbooks.get(p["runbook"])
        if rb is None:
            errs.append(f"autonomy: policy {pname}: runbook {p['runbook']} is not in runbooks.yml")
        elif p["action"] not in rb.get("remediation", []):
            errs.append(f"autonomy: policy {pname}: runbook {p['runbook']} does not list {p['action']} as a remediation")
        if p["enabled"]:
            if a["max_autonomy"] != "auto":
                errs.append(f"autonomy: policy {pname} is enabled but action {p['action']} has max_autonomy {a['max_autonomy']} (needs auto)")
            if rb is not None and rb["automatable"] != "auto":
                errs.append(f"autonomy: policy {pname} is enabled but runbook {p['runbook']} is automatable {rb['automatable']} (needs auto)")
            if a.get("guard", {}).get("target_policy") == "host_tiers.T1" and not au["hosts"]:
                errs.append(f"autonomy: policy {pname}: a host-targeted action needs autonomy.hosts")
    return errs


# 10g hard limits, pinned here so a PR cannot quietly drop one from the deny list (docs/plans/active/10g-rebuild-loop.md).
REBUILD_DENY_NAMES = {"saga", "fulla", "vor", "idunn", "hlin", "eir", "snotra", "hugin", "factorio", "gna", "ratatoskr", "frigg",
                      "gondul", "hlokk", "sigrun", "pbs"}
REBUILD_DENY_VMIDS = {1101, 1102, 1110, 1120, 1121, 1122, 1130, 1131, 1132, 1133, 1134, 1135, 2001, 2002, 2003, 2900}
CONTROL_PLANE_NAMES = {"gondul", "hlokk", "sigrun", "rota", "hildr", "kara"}
PBS_NAME, PBS_VMID = "pbs", 1101
CANARY_VMIDS = {1190, 1191, 1192}
def check_soak(reg: dict) -> list[str]:
    """The soak section (10f scheduled fault injection): the scope the scheduler may inject in must sit inside the autonomy scope
    and the `canary-fault` action's allow-list, and only the scheduler may propose that action."""
    errs: list[str] = []
    actions = reg["actions"]
    for name, a in actions.items():
        if a.get("internal_source") and not a.get("internal"):
            errs.append(f"actions: {name}: internal_source needs internal: true")
    sk = reg.get("soak")
    soak_actions = sorted(n for n, a in actions.items() if a.get("internal_source") == "soak")
    if not sk:
        if soak_actions:
            errs.append(f"soak: {soak_actions} are internal to the soak scheduler but the registry has no `soak` section")
        return errs
    act = actions.get("canary-fault")
    if act is None or act.get("internal_source") != "soak" or not act.get("internal"):
        return errs + ["soak: the section needs the `canary-fault` action with internal: true and internal_source: soak"]
    if soak_actions != ["canary-fault"]:
        errs.append(f"soak: only canary-fault may be internal to the scheduler (found {soak_actions})")
    if (act["tier"], act["max_autonomy"]) != ("T1", "approval"):
        errs.append("soak: canary-fault must be T1 / max_autonomy approval (the scheduler approves it itself, no autonomy policy may cover it)")
    if act.get("guard", {}).get("target_policy") != "canaries":
        errs.append("soak: canary-fault must have guard.target_policy canaries")
    if "autonomy" not in reg:
        errs.append("soak: the scheduler only runs while autonomy is on, so the registry needs an `autonomy` section")
    t1, au = set(reg["host_tiers"]["T1"]), set((reg.get("autonomy") or {}).get("hosts", []))
    allowed = act.get("guard", {}).get("allowed_units", {})
    for h in sk["hosts"]:
        if h not in t1:
            errs.append(f"soak: host {h!r} is not in host_tiers.T1")
        if h not in au:
            errs.append(f"soak: host {h!r} is not in autonomy.hosts (a fault is only injected where autonomy may heal it)")
        for u in sk["units"]:
            if u not in allowed.get(h, []):
                errs.append(f"soak: unit {u!r} is not allow-listed for {h!r} in canary-fault.guard.allowed_units")
    try:
        datetime.strptime(sk["ends"], "%Y-%m-%d")
    except ValueError:
        errs.append(f"soak: ends {sk['ends']!r} is not a real date")
    return errs


# action -> (tier, max_autonomy) exactly as the plan's table says
REBUILD_ACTIONS = {
    "rebuild-plan": ("T0", "auto"),
    "start-guest": ("T1", "auto"),
    "rebuild-guest": ("T1", "approval"),
    "rebuild-worker": ("T2", "approval"),
    "rebuild-verify": ("T0", "auto"),
}
REBUILD_PRECHECK_ACTIONS = {"guest-dead": {"start-guest", "rebuild-guest"}, "guest-broken": {"rebuild-guest"}}


def check_rebuild(reg: dict, rb_doc: dict) -> list[str]:
    """The rebuild section (10g): scope, hard limits and policies must be consistent with host_tiers, the actions and the
    runbooks. This is the file a reviewer reads to know what the loop may destroy."""
    errs: list[str] = []
    rb = reg.get("rebuild")
    if not rb:
        return errs
    actions = reg["actions"]
    tiers = reg["host_tiers"]
    known = set(tiers.get("T1", [])) | set(tiers.get("T2", []))
    runbooks = {r["id"]: r for r in rb_doc["runbooks"]}
    deny_names, deny_vmids = set(rb["deny"]["names"]), set(rb["deny"]["vmids"])
    for n in sorted(REBUILD_DENY_NAMES - deny_names):
        errs.append(f"rebuild: deny.names must keep {n!r} (quorum member, state-bearing, control plane or the loop's own agent)")
    for v in sorted(REBUILD_DENY_VMIDS - deny_vmids):
        errs.append(f"rebuild: deny.vmids must keep {v}")
    if rb["limits"]["breaker_failures"] != 1:
        errs.append("rebuild: limits.breaker_failures must be 1 (a failed rebuild leaves a half-built guest: stop and page)")

    stage_of_class: dict[str, str] = {}
    for sname, st in rb["stages"].items():
        for c in st["classes"]:
            if c not in rb["classes"]:
                errs.append(f"rebuild: stage {sname} lists unknown class {c!r}")
            stage_of_class[c] = sname
        if sname != "A" and st["autonomy"] == "auto":
            errs.append(f"rebuild: stage {sname}: only stage A (the canaries) may be unattended from the start")
        if sname == "A" and st["autonomy"] != "auto":
            errs.append("rebuild: stage A is the canary stage and is auto")
        if st["autonomy"] == "approval-then-auto" and "approvals_before_auto" not in st:
            errs.append(f"rebuild: stage {sname}: approval-then-auto needs approvals_before_auto")
        if st["autonomy"] != "approval-then-auto" and "approvals_before_auto" in st:
            errs.append(f"rebuild: stage {sname}: approvals_before_auto only applies to approval-then-auto")
    seen_hosts: dict[str, str] = {}
    for cname, c in rb["classes"].items():
        if stage_of_class.get(cname) != c["stage"]:
            errs.append(f"rebuild: class {cname}: stage {c['stage']} does not list it")
        if c["kind"] == "k8s-worker" and (c["stage"] != "C" or c["module"] != "asgard-k3s"):
            errs.append(f"rebuild: class {cname}: workers are stage C in the asgard-k3s module")
        if c["module"] == "asgard-lxcs-root" and c["stage"] not in ("B2",):
            errs.append(f"rebuild: class {cname}: the root-ticket module is approval-only (stage B2)")
        for n in c.get("neighbours", []):
            if n not in known and n not in deny_names:
                errs.append(f"rebuild: class {cname}: neighbour {n!r} is neither a known host nor a denied one")
        if c.get("leader_aware") and not c.get("neighbours"):
            errs.append(f"rebuild: class {cname}: leader_aware needs neighbours")
        for h, info in c["hosts"].items():
            if h in seen_hosts:
                errs.append(f"rebuild: host {h!r} is in classes {seen_hosts[h]} and {cname}")
            seen_hosts[h] = cname
            if h not in known:
                errs.append(f"rebuild: class {cname}: host {h!r} is not in host_tiers (T1 or T2)")
            if h in deny_names or info["vmid"] in deny_vmids:
                errs.append(f"rebuild: class {cname}: host {h!r} (vmid {info['vmid']}) is on the deny list")
            if h in CONTROL_PLANE_NAMES or 2000 < info["vmid"] <= 2003 or 3000 < info["vmid"] <= 3003:
                errs.append(f"rebuild: class {cname}: {h!r} is a control-plane node (etcd member): never in the loop")
            if h == PBS_NAME or info["vmid"] == PBS_VMID:
                errs.append(f"rebuild: class {cname}: PBS is never rebuilt by the loop and never placed on Skuld")
            is_canary = h.startswith("canary-") or info["vmid"] in CANARY_VMIDS
            if is_canary and c["stage"] != "A":
                errs.append(f"rebuild: class {cname}: canary {h!r} belongs only in stage A")
            if cname == "canary" and (not is_canary or info["node"] != "urd"):
                errs.append(f"rebuild: class canary: {h!r} must be a canary-N guest on urd")
            if c["stage"] == "A" and not is_canary:
                errs.append(f"rebuild: stage A is canaries only; {h!r} is not one")
            if c["kind"] != "k8s-worker" and h not in tiers.get("T1", []) and c["kind"] == "lxc":
                errs.append(f"rebuild: class {cname}: LXC {h!r} must be in host_tiers.T1")
            if c["kind"] == "k8s-worker" and h not in tiers.get("T2", []):
                errs.append(f"rebuild: class {cname}: worker {h!r} must be in host_tiers.T2")
    for aname, (tier, auto) in REBUILD_ACTIONS.items():
        a = actions.get(aname)
        if a is None:
            errs.append(f"rebuild: action {aname} is missing from the registry")
        elif (a["tier"], a["max_autonomy"]) != (tier, auto):
            errs.append(f"rebuild: action {aname} must be {tier} / max_autonomy {auto} (is {a['tier']} / {a['max_autonomy']})")
    for pname, p in rb["policies"].items():
        a = actions.get(p["action"])
        stage = rb["stages"].get(rb["classes"].get(p["class"], {}).get("stage", ""), {})
        if p["class"] not in rb["classes"]:
            errs.append(f"rebuild: policy {pname}: unknown class {p['class']!r}")
        if p["action"] not in ("start-guest", "rebuild-guest"):
            errs.append(f"rebuild: policy {pname}: only start-guest and rebuild-guest may run unattended (not {p['action']})")
        elif p["action"] == "start-guest" and p["class"] not in ("canary", "adguard-replica"):
            errs.append(f"rebuild: policy {pname}: start-guest policies cover canary and adguard-replica only")
        if p["action"] not in REBUILD_PRECHECK_ACTIONS.get(p["precheck"], set()):
            errs.append(f"rebuild: policy {pname}: precheck {p['precheck']} does not belong to action {p['action']}")
        if p["enabled"]:
            if stage.get("autonomy") not in ("auto", "approval-then-auto"):
                errs.append(f"rebuild: policy {pname} is enabled but its class is approval-only")
            if a is not None and a["max_autonomy"] != "auto":
                errs.append(f"rebuild: policy {pname} is enabled but action {p['action']} has max_autonomy {a['max_autonomy']} (needs auto)")
            r = runbooks.get(p["runbook"])
            if r is None:
                errs.append(f"rebuild: policy {pname}: runbook {p['runbook']} is not in runbooks.yml")
            elif p["action"] not in r.get("remediation", []):
                errs.append(f"rebuild: policy {pname}: runbook {p['runbook']} does not list {p['action']} as a remediation")
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


# --- native Zabbix events (Phase 10d2) -------------------------------------------
def js_payload_keys(js_text: str) -> set[str]:
    """Top-level keys of the object literal assigned to `var payload` in n8n-webhook.js."""
    m = re.search(r"var payload = \{\n(.*?)\n    \};", js_text, re.S)
    return set(re.findall(r"^        (\w+):", m.group(1), re.M)) if m else set()


def check_zabbix_native(root: Path, routes: list[dict], known_runbooks: set[str]) -> list[str]:
    errs: list[str] = []
    ev_schema = load_schema("zabbix-event.v1.schema.json")
    alert_schema = load_schema("alert.v1.schema.json")
    for p in sorted((AIOPS / "fixtures" / "zabbix-native").glob("*.json")):
        case = json.loads(p.read_text(encoding="utf-8"))
        for e in schema_errors(case["event"], ev_schema):
            errs.append(f"zabbix-native: {p.name}: event: {e}")
        got = zabbix_event.from_zabbix_event(case["event"], case["received_at"], routes, known_runbooks)
        if got != case["expected"]:
            errs.append(f"zabbix-native: {p.name}: adapter(event) != expected\n  got:      {json.dumps(got, sort_keys=True)}\n  expected: {json.dumps(case['expected'], sort_keys=True)}")
        for i, alert in enumerate(case["expected"]):
            for e in alert_errors(alert, alert_schema):
                errs.append(f"zabbix-native: {p.name}: expected[{i}]: {e}")
    # The sender script and the schema must describe the same payload.
    js = root / "ansible" / "roles" / "zabbix-server" / "templates" / "n8n-webhook.js"
    if js.exists():
        js_keys = js_payload_keys(js.read_text(encoding="utf-8"))
        schema_keys = set(ev_schema["properties"])
        if js_keys != schema_keys:
            errs.append(
                f"zabbix-native: n8n-webhook.js payload keys != zabbix-event.v1 schema properties "
                f"(only in script: {sorted(js_keys - schema_keys)}; only in schema: {sorted(schema_keys - js_keys)})"
            )
    return errs


# --- n8n workflows (Phase 10d) -------------------------------------------------
# The agent runs on a host that holds the Discord webhook (and, from 10d3, the
# Anthropic key) and n8n can execute code, so a workflow is reviewed like code:
# only allow-listed node types, no inline secrets, no URL that is not an env
# reference or a known internal host, authenticated webhooks, and every ingest
# source consistent across the workflow, the role and the Vault TF.
N8N_NODE_ALLOW = {
    "n8n-nodes-base.webhook", "n8n-nodes-base.respondToWebhook", "n8n-nodes-base.set",
    "n8n-nodes-base.if", "n8n-nodes-base.switch", "n8n-nodes-base.merge", "n8n-nodes-base.noOp",
    "n8n-nodes-base.wait", "n8n-nodes-base.httpRequest", "n8n-nodes-base.splitInBatches",
    "n8n-nodes-base.stopAndError", "n8n-nodes-base.stickyNote", "n8n-nodes-base.scheduleTrigger",
    # the diagnosis agent (10d3): exactly these three LangChain nodes. NOT the whole package: it also
    # ships code tools, sub-workflow tools, MCP clients and other model providers, none of which may
    # appear here without a deliberate change to this list.
    "@n8n/n8n-nodes-langchain.agent", "@n8n/n8n-nodes-langchain.lmChatAnthropic",
    "@n8n/n8n-nodes-langchain.toolHttpRequest",
}
N8N_NODE_ALLOW_PREFIX: tuple = ()
N8N_AGENT_MAX_ITERATIONS = 10
# Webhooks with NO header credential, allowed only where the network is the control: each path must also appear in Gná's Caddy
# group_vars with a per-path source rule (checked below), and must respond immediately. The drift hand-off (Phase 10h2) carries
# no secret; the Toolbelt re-reads the run from Semaphore before believing it, so a forged call only costs one lookup.
N8N_NETWORK_AUTH_WEBHOOKS = {"aiops/drift": "10.0.21.0/24"}
N8N_SYNC_SOURCES = {"chat"}  # the bot waits for the answer: these webhooks respond from a Respond-to-Webhook node
N8N_NODE_DENY = {  # defence in depth: also excluded at runtime via NODES_EXCLUDE
    "n8n-nodes-base.executeCommand", "n8n-nodes-base.ssh", "n8n-nodes-base.ftp",
    "n8n-nodes-base.readWriteFile", "n8n-nodes-base.localFileTrigger", "n8n-nodes-base.code",
}
N8N_URL_HOSTS = {"aiops-toolbelt.niflheim.xiiisins.com"}  # literal URLs may only point here
N8N_SECRET_PATTERNS = [
    (re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[\w-]+"), "a Discord webhook URL"),
    (re.compile(r"sk-ant-[\w-]{10,}"), "an Anthropic API key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}"), "a bearer token"),
    (re.compile(r"\bhvs\.[A-Za-z0-9]{20,}"), "a Vault token"),
]


def check_n8n_workflows(root: Path) -> list[str]:
    errs: list[str] = []
    wf_dir = root / "aiops" / "n8n" / "workflows"
    files = sorted(wf_dir.glob("*.json")) if wf_dir.is_dir() else []
    role_defaults = root / "ansible" / "roles" / "n8n-agent" / "defaults" / "main.yml"
    vault_tf = root / "terraform" / "vault" / "main.tf"
    role_sources: set[str] = set()
    tf_sources: set[str] = set()
    if role_defaults.exists():
        role_sources = set(load_yaml(role_defaults).get("n8n_ingest_sources") or [])
    if vault_tf.exists():
        m = re.search(r"n8n_ingest_sources\s*=\s*toset\(\[([^\]]*)\]\)", vault_tf.read_text())
        tf_sources = set(re.findall(r'"([\w-]+)"', m.group(1))) if m else set()
    if role_sources != tf_sources:
        errs.append(
            f"n8n: role n8n_ingest_sources {sorted(role_sources)} != terraform/vault "
            f"n8n_ingest_sources {sorted(tf_sources)} (a source needs a minted token AND a role entry)"
        )

    seen_ids: dict[str, str] = {}
    for f in files:
        rel = f.relative_to(root)
        text = f.read_text()
        try:
            wf = json.loads(text)
        except json.JSONDecodeError as e:
            errs.append(f"n8n: {rel}: invalid JSON: {e}")
            continue
        for pat, what in N8N_SECRET_PATTERNS:
            if pat.search(text):
                errs.append(f"n8n: {rel}: contains {what} (workflows are committed to a PUBLIC repo)")
        wid = wf.get("id")
        if not wid or not wf.get("name"):
            errs.append(f"n8n: {rel}: needs a fixed top-level `id` and `name` (import is by id)")
        elif wid in seen_ids:
            errs.append(f"n8n: {rel}: id {wid} also used by {seen_ids[wid]}")
        else:
            seen_ids[wid] = str(rel)
        if wf.get("active"):
            errs.append(f"n8n: {rel}: `active` must be false (the role publishes workflows with the CLI)")
        nodes = wf.get("nodes") or []
        names = [n.get("name") for n in nodes]
        if len(set(names)) != len(names):
            errs.append(f"n8n: {rel}: duplicate node names")
        for n in nodes:
            ntype, nname = n.get("type", ""), n.get("name", "?")
            if ntype in N8N_NODE_DENY:
                errs.append(f"n8n: {rel}: node {nname!r} uses denied type {ntype}")
            elif ntype not in N8N_NODE_ALLOW and not ntype.startswith(N8N_NODE_ALLOW_PREFIX):
                errs.append(f"n8n: {rel}: node {nname!r} type {ntype} is not in the allow-list (aiops/tools/lint.py)")
            creds = n.get("credentials") or {}
            for ctype, c in creds.items():
                if set(c) - {"id", "name"}:
                    errs.append(f"n8n: {rel}: node {nname!r} credential {ctype} must be a reference (id+name) only")
            params = n.get("parameters") or {}
            if ntype == "n8n-nodes-base.webhook":
                path = str(params.get("path", ""))
                hc = creds.get("httpHeaderAuth") or {}
                cname = str(hc.get("name", ""))
                if not path.startswith("aiops/"):
                    errs.append(f"n8n: {rel}: webhook {nname!r} path must start with `aiops/`")
                if path in N8N_NETWORK_AUTH_WEBHOOKS:
                    cidr = N8N_NETWORK_AUTH_WEBHOOKS[path]
                    caddy = root / "ansible" / "inventory" / "group_vars" / "n8n_agent.yml"
                    ctext = caddy.read_text() if caddy.is_file() else ""
                    if params.get("authentication") != "none":
                        errs.append(f"n8n: {rel}: webhook {nname!r} is the network-authenticated path; its authentication must be `none` (no half-way credential)")
                    if f"/webhook/{path}*') && !remote_ip('{cidr}')" not in ctext:
                        errs.append(f"n8n: {rel}: webhook {nname!r} has no credential, so n8n_agent.yml must restrict /webhook/{path} to {cidr} with a Caddy remote_ip rule")
                    if params.get("responseMode") != "onReceived":
                        errs.append(f"n8n: {rel}: webhook {nname!r} must respond immediately (responseMode onReceived)")
                    continue
                if params.get("authentication") != "headerAuth" or not cname.startswith("aiops-ingest-"):
                    errs.append(f"n8n: {rel}: webhook {nname!r} must use headerAuth with an `aiops-ingest-<source>` credential")
                else:
                    source = cname.removeprefix("aiops-ingest-")
                    if path != f"aiops/{source}":
                        errs.append(f"n8n: {rel}: webhook path {path!r} must be aiops/{source} to match its credential")
                    if source not in role_sources:
                        errs.append(f"n8n: {rel}: source {source!r} is not in n8n-agent n8n_ingest_sources")
                sync = cname.removeprefix("aiops-ingest-") in N8N_SYNC_SOURCES
                if sync:
                    if params.get("responseMode") != "responseNode" or not any(x.get("type") == "n8n-nodes-base.respondToWebhook" for x in nodes):
                        errs.append(f"n8n: {rel}: webhook {nname!r} (a synchronous source) needs responseMode responseNode and a Respond to Webhook node")
                elif params.get("responseMode") != "onReceived":
                    errs.append(
                        f"n8n: {rel}: webhook {nname!r} must respond immediately (responseMode onReceived) so a slow "
                        f"agent can never make a monitoring system's send fail"
                    )
            if ntype == "@n8n/n8n-nodes-langchain.agent":
                it = (params.get("options") or {}).get("maxIterations")
                if not isinstance(it, int) or not (1 <= it <= N8N_AGENT_MAX_ITERATIONS):
                    errs.append(f"n8n: {rel}: agent {nname!r} needs options.maxIterations in 1..{N8N_AGENT_MAX_ITERATIONS}")
                if not str((params.get("options") or {}).get("systemMessage", "")).strip():
                    errs.append(f"n8n: {rel}: agent {nname!r} needs a system message")
            if ntype == "@n8n/n8n-nodes-langchain.lmChatAnthropic":
                if (creds.get("anthropicApi") or {}).get("name") != "aiops-anthropic":
                    errs.append(f"n8n: {rel}: model {nname!r} must use the `aiops-anthropic` credential")
            if ntype in ("n8n-nodes-base.httpRequest", "@n8n/n8n-nodes-langchain.toolHttpRequest"):
                url = str(params.get("url", ""))
                if "AIOPS_TOOLBELT_URL" in url:
                    tc = creds.get("httpHeaderAuth") or {}
                    if params.get("authentication") != "genericCredentialType" or tc.get("name") != "aiops-toolbelt":
                        errs.append(f"n8n: {rel}: node {nname!r} calls the Toolbelt API without the `aiops-toolbelt` credential")
                if url.startswith("="):
                    if "$env.AIOPS_" not in url and not any(h in url for h in N8N_URL_HOSTS):
                        errs.append(f"n8n: {rel}: node {nname!r} URL expression must use $env.AIOPS_* or a known host")
                else:
                    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
                    if host not in N8N_URL_HOSTS:
                        errs.append(f"n8n: {rel}: node {nname!r} literal URL host {host!r} is not allowed {sorted(N8N_URL_HOSTS)}")
        for src, outs in (wf.get("connections") or {}).items():
            if src not in names:
                errs.append(f"n8n: {rel}: connection from unknown node {src!r}")
            for branch in outs.get("main", []):
                for link in branch or []:
                    if link.get("node") not in names:
                        errs.append(f"n8n: {rel}: connection to unknown node {link.get('node')!r}")
    return errs


def check_replays(root: Path) -> list[str]:
    """Acceptance scenarios (aiops/replays/<name>/scenario.json): the event is a valid native Zabbix event, every
    recorded call is a real Toolbelt tool with arguments that pass its contract, and the expectations are well-formed.
    A malformed recording would otherwise surface as a baffling NO_RECORDING in the middle of an acceptance run."""
    import sys as _sys

    errs: list[str] = []
    d = root / "aiops" / "replays"
    if not d.is_dir():
        return errs
    _sys.path.insert(0, str(root / "aiops" / "toolbelt"))
    import tools as _tools

    ev_schema = load_schema("zabbix-event.v1.schema.json")
    for f in sorted(d.glob("*/scenario.json")):
        rel = f.relative_to(root)
        if not _tools.REPLAY_NAME.match(f.parent.name):
            errs.append(f"replays: {rel}: directory name must match {_tools.REPLAY_NAME.pattern}")
        try:
            doc = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            errs.append(f"replays: {rel}: invalid JSON: {e}")
            continue
        evs = doc.get("events") if doc.get("events") is not None else [doc.get("event")]
        if doc.get("events") is not None and doc.get("event") is not None:
            errs.append(f"replays: {rel}: use either `event` or `events`, not both")
        if doc.get("events") is not None and len(evs) < 2:
            errs.append(f"replays: {rel}: `events` is a burst and needs at least 2 events")
        for i, ev in enumerate(evs):
            for e in schema_errors(ev, ev_schema):
                errs.append(f"replays: {rel}: event[{i}]: {e}")
        if len({(e or {}).get("event_id") for e in evs}) != len(evs):
            errs.append(f"replays: {rel}: events must have distinct event_ids")
        exp = doc.get("expect") or {}
        if not all(isinstance(x, str) and x for x in exp.get("forbidden_text", [])) or not isinstance(exp.get("forbidden_text", []), list):
            errs.append(f"replays: {rel}: expect.forbidden_text must be a list of non-empty strings")
        layers = {"host", "hypervisor", "workload", "network", "drift", "external", "unknown"}
        for key in ("layers_allowed", "layers_forbidden"):
            bad = set(exp.get(key, [])) - layers
            if bad:
                errs.append(f"replays: {rel}: expect.{key} has unknown layers {sorted(bad)}")
        if not exp.get("layers_allowed"):
            errs.append(f"replays: {rel}: expect.layers_allowed is required (what a correct diagnosis may say)")
        if set(exp.get("layers_allowed", [])) & set(exp.get("layers_forbidden", [])):
            errs.append(f"replays: {rel}: a layer is both allowed and forbidden")
        for t in exp.get("must_cite_any_of", []):
            if t not in _tools.SPEC:
                errs.append(f"replays: {rel}: expect.must_cite_any_of names unknown tool {t}")
        seen = set()
        for i, c in enumerate(doc.get("calls", [])):
            try:
                _tools.validate(c.get("tool", ""), c.get("args"))
            except _tools.ToolError as e:
                errs.append(f"replays: {rel}: calls[{i}] {c.get('tool')}: {e.message}")
                continue
            key = (c["tool"], json.dumps(c["args"], sort_keys=True))
            if key in seen:
                errs.append(f"replays: {rel}: calls[{i}] duplicates an earlier {c['tool']} call with the same arguments")
            seen.add(key)
            if "response" not in c:
                errs.append(f"replays: {rel}: calls[{i}] has no response")
        if not doc.get("calls"):
            errs.append(f"replays: {rel}: no recorded calls")
    return errs


def check_rightsizing(doc: dict) -> list[str]:
    """(10i) rightsizing.yml: the schema, plus the cross-field rules it cannot say: no proposed floor below the global floor, and a raised
    limit keeps real headroom over the request."""
    errs = [f"rightsizing: {e}" for e in schema_errors(doc, load_schema("rightsizing.v1.schema.json"))]
    if errs:
        return errs
    if doc["memory"]["over_request"]["floor_mib"] < doc["floors"]["memory_mib"]:
        errs.append("rightsizing: memory.over_request.floor_mib is below floors.memory_mib")
    if doc["cpu"]["over_request"]["floor_millicores"] < doc["floors"]["cpu_millicores"]:
        errs.append("rightsizing: cpu.over_request.floor_millicores is below floors.cpu_millicores")
    if doc["memory"]["under_request"]["limit_headroom"] < 1.2:
        errs.append("rightsizing: memory.under_request.limit_headroom below 1.2 leaves a raised limit with no room")
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
    errs += check_autonomy(reg, rb)
    errs += check_soak(reg)
    errs += check_rebuild(reg, rb)
    errs += check_routing(rt, rb)
    errs += check_fixtures(root, rt["routes"])
    errs += check_zabbix_native(root, rt["routes"], {r["id"] for r in rb["runbooks"]})
    errs += check_n8n_workflows(root)
    errs += check_replays(root)
    errs += check_rightsizing(load_yaml(AIOPS / "rightsizing.yml"))
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
