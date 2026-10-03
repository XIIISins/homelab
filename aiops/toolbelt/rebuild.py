"""Fleet rebuild loop logic (Phase 10g1): the pure decisions, no I/O.

Plan: docs/operations/10g-rebuild-loop.md. Three stateless pieces live here, each a pure function over plain data, so
the rules can be tested exhaustively before any runner, Terraform or Semaphore piece exists:

  eligible(facts, policy)            may THIS target be rebuilt right now? verdict go|skip|stop + a machine-readable reason
  check_plan(plan_json, expected)    is this `terraform show -json` plan exactly one in-scope replace/create? -> problems
  worker_data_manifest(...)          what dies with a worker, and is it safe to drain it? (the approval card's data-loss list)

The policy is the registry's `rebuild:` section (aiops/actions.yml); a reviewed PR is the only way to widen it. A
diagnosis only ever supplies a PROPOSAL; whether a rebuild is eligible is decided here from reality (facts the Toolbelt
read itself), never from what the model said.

Verdict semantics
  go    every rule passed: the rebuild may proceed (the caller then plans, applies, converges, verifies)
  skip  nothing to do right now and nothing to escalate: not dead yet, a cheaper rung applies first, a rebuild is already
        in flight, maintenance is on. Re-evaluate later.
  stop  refuse AND escalate to a human (diagnosis only): hard limit, unhealthy node, tripped breaker, rate limit,
        out-of-scope target, kill switch.

facts (a plain dict; every key the Toolbelt must read itself)
  target                 inventory name, e.g. "canary-2"
  mode                   "approval" (default) or "auto": whether the proposal is operator-approved or unattended
  guest_state            running | stopped | hung | missing (deleted behind Terraform's back) | unknown
  start_attempted        bool: rung 0 (`start-guest`) was tried
  start_failed           bool: and it failed
  probes                 [{"t": epoch seconds, "ok": bool}, ...] reach.tcp results over time
  agent_silent           bool: the Zabbix agent has not reported
  node_online            bool: the PVE host node is online
  node_guests_ok         bool: its other guests answer
  last_backup_age_hours  float | None: age of the last successful PBS backup (None = none known)
  restart_breaker_tripped bool: the 10f restart breaker tripped for this target in the last 24 h
  drift_changed          int | None: changed tasks in a dry-run replay
  replay_converges       bool | None: whether a role replay can fix that drift
  peers_healthy          bool | None: the neighbours_healthy guard (None = unknown, treated as unhealthy)
  is_leader              bool: holds the leader/VRRP-master role right now
  inflight               int: rebuilds in flight fleet-wide (queue length 1)
  flags                  {"maintenance", "kill_switch", "autonomy_rebuild"}: bools
  history                {"target_day", "target_week", "class_day", "fleet_day": ints, "breaker_open": bool,
                          "peer_rebuilt_24h": bool, "clean_rebuilds": int (approved clean rebuilds of this target)}
  state_bearing / quorum_member / agent_host   bools (default False); any True is a hard stop
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------------------------------------------------
# Policy: the registry's `rebuild:` section as objects
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    per_target_per_day: int = 1
    per_target_per_week: int = 2
    per_class_per_day: int = 2
    fleet_per_day: int = 3
    breaker_failures: int = 1
    plan_max_age_seconds: int = 900


@dataclass(frozen=True)
class GuestClass:
    name: str
    stage: str
    kind: str  # lxc | droplet | k8s-worker
    hosts: dict  # name -> {"vmid": int, "node": str}
    backup: str  # none | pbs-last-chance | manifest
    neighbours: tuple = ()
    leader_aware: str | None = None
    post_conditions: tuple = ()


@dataclass(frozen=True)
class Stage:
    name: str
    classes: tuple
    autonomy: str  # auto | approval-then-auto | approval
    approvals_before_auto: int = 0


@dataclass(frozen=True)
class RebuildPolicyEntry:
    name: str
    enabled: bool
    action: str
    cls: str
    runbook: str
    precheck: str


class RebuildPolicy:
    def __init__(self, data: dict):
        self.autonomy_rebuild_default: bool = bool(data.get("autonomy_rebuild_default", False))
        lim = data.get("limits", {})
        self.limits = Limits(**{k: int(v) for k, v in lim.items() if k in Limits.__dataclass_fields__})
        dead = data.get("dead", {})
        self.dead_probe_count = int(dead.get("probe_count", 3))
        self.dead_probe_span_seconds = int(dead.get("probe_span_seconds", 600))
        self.backup_max_age_hours = float(dead.get("backup_max_age_hours", 36))
        self.drift_max_changed = int(data.get("broken", {}).get("drift_max_changed", 10))
        self.stages = {
            n: Stage(n, tuple(s["classes"]), s["autonomy"], int(s.get("approvals_before_auto", 0)))
            for n, s in data.get("stages", {}).items()
        }
        self.classes = {
            n: GuestClass(n, c["stage"], c["kind"], dict(c["hosts"]), c["backup"], tuple(c.get("neighbours", ())),
                          c.get("leader_aware"), tuple(c.get("post_conditions", ())))
            for n, c in data.get("classes", {}).items()
        }
        deny = data.get("deny", {})
        self.deny_names = frozenset(deny.get("names", ()))
        self.deny_vmids = frozenset(int(v) for v in deny.get("vmids", ()))
        self.policies = {
            n: RebuildPolicyEntry(n, bool(p["enabled"]), p["action"], p["class"], p["runbook"], p["precheck"])
            for n, p in data.get("policies", {}).items()
        }

    @classmethod
    def from_registry(cls, data: dict) -> "RebuildPolicy | None":
        """None when the registry has no `rebuild` section (then nothing is ever eligible)."""
        sec = data.get("rebuild")
        return cls(sec) if sec else None

    def class_of(self, target: str) -> GuestClass | None:
        return next((c for c in self.classes.values() if target in c.hosts), None)

    def vmid_of(self, target: str) -> int | None:
        c = self.class_of(target)
        return int(c.hosts[target]["vmid"]) if c else None

    def policy_for(self, cls_name: str, action: str = "rebuild-guest") -> RebuildPolicyEntry | None:
        """The ENABLED policy covering an action for a class (the first wins)."""
        return next((p for p in self.policies.values() if p.enabled and p.cls == cls_name and p.action == action), None)


# ---------------------------------------------------------------------------------------------------------------------
# (1) Eligibility
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    verdict: str  # go | skip | stop
    reason: str  # machine-readable code
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "reason": self.reason, "detail": dict(self.detail)}


def _go(reason: str, **d) -> Verdict:
    return Verdict("go", reason, d)


def _skip(reason: str, **d) -> Verdict:
    return Verdict("skip", reason, d)


def _stop(reason: str, **d) -> Verdict:
    return Verdict("stop", reason, d)


def _num(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if v == v and v >= 0 else None  # rejects NaN and negatives


def unreachable(probes: object, count: int, span_seconds: int) -> bool:
    """True when the trailing run of failed probes (after the last success) has >= `count` probes spanning >= span."""
    if not isinstance(probes, list):
        return False
    pts = []
    for p in probes:
        if not isinstance(p, dict) or _num(p.get("t")) is None or not isinstance(p.get("ok"), bool):
            return False  # a malformed probe list proves nothing
        pts.append((float(p["t"]), p["ok"]))
    pts.sort()
    tail: list[float] = []
    for t, ok in reversed(pts):
        if ok:
            break
        tail.append(t)
    return len(tail) >= count and (max(tail) - min(tail)) >= span_seconds


_REQUIRED = ("guest_state", "node_online", "node_guests_ok", "flags", "history")


def eligible(facts: dict, policy: RebuildPolicy | None) -> Verdict:
    """Apply the plan's rules in order. The first rule that fires decides; `go` only when none did."""
    if policy is None:
        return _stop("no-rebuild-policy")
    target = facts.get("target")
    if not isinstance(target, str) or not target:
        return _stop("no-target")
    missing = [k for k in _REQUIRED if facts.get(k) is None]
    if missing:
        return _stop("facts-incomplete", missing=missing)
    flags, hist = facts["flags"], facts["history"]
    mode = facts.get("mode", "approval")
    if mode not in ("approval", "auto"):
        return _stop("bad-mode", mode=str(mode)[:16])

    # --- scope: never the deny list, only an allow-listed class ---------------------------------------------------
    cls = policy.class_of(target)
    vmid = policy.vmid_of(target)
    if target in policy.deny_names or (vmid is not None and vmid in policy.deny_vmids):
        return _stop("denied-target")
    if cls is None:
        return _stop("not-in-allow-list")
    if facts.get("class") not in (None, cls.name):
        return _stop("class-mismatch", registry_class=cls.name)
    if facts.get("state_bearing") or facts.get("quorum_member") or facts.get("agent_host"):
        return _stop("hard-limit", why="state-bearing, quorum member or the agent host is never rebuilt by the loop")

    # --- switches --------------------------------------------------------------------------------------------------
    if flags.get("kill_switch"):
        return _stop("kill-switch")
    if flags.get("maintenance"):
        return _skip("maintenance")

    # --- the host node first (the Skuld lesson): a node fault is diagnosed, never healed by rebuilding its guests ----
    if facts["node_online"] is not True or facts["node_guests_ok"] is not True:
        return _stop("node-unhealthy", why="a node-level fault is a diagnosis-only case")

    # --- dead vs broken, and the ladder (start/restart before rebuild) ---------------------------------------------
    state = facts["guest_state"]
    kind: str
    if state == "missing":
        kind, why = "dead", "guest-missing"  # deleted behind Terraform's back: the plan is a `create`
    elif state in ("stopped", "hung"):
        if not facts.get("start_attempted"):
            return _skip("ladder-start-first", rung="start-guest")
        if not facts.get("start_failed"):
            return _skip("start-recovered")
        kind, why = "dead", "start-failed"
    elif state == "running":
        silent = unreachable(facts.get("probes"), policy.dead_probe_count, policy.dead_probe_span_seconds)
        if silent and facts.get("agent_silent") is True:
            kind, why = "dead", "unreachable-agent-silent"
        elif silent:
            return _skip("agent-still-reporting", why="reach fails but the Zabbix agent answers: not dead")
        elif facts.get("restart_breaker_tripped"):
            kind, why = "broken", "restart-breaker-tripped"
        elif (_num(facts.get("drift_changed")) or 0) > policy.drift_max_changed and facts.get("replay_converges") is False:
            kind, why = "broken", "drift-not-convergeable"
        else:
            return _skip("not-dead-or-broken")
    else:
        return _stop("state-unknown", state=str(state)[:16])

    # --- class guards ----------------------------------------------------------------------------------------------
    if cls.neighbours and facts.get("peers_healthy") is not True:
        return _stop("peers-unhealthy", peers=list(cls.neighbours))
    if cls.leader_aware and facts.get("is_leader") and kind != "dead":
        return _stop("leader-alive", role=cls.leader_aware)
    if cls.neighbours and hist.get("peer_rebuilt_24h"):
        return _stop("peer-rebuilt-recently")

    # --- breaker and rate limits (a repeat means the rebuild is not the fix) ---------------------------------------
    lim = policy.limits
    if hist.get("breaker_open"):
        return _stop("breaker-open")
    if int(hist.get("target_day", 0)) >= lim.per_target_per_day:
        return _stop("rate-target-day", why="a second rebuild inside 24 h means the first did not fix it")
    if int(hist.get("target_week", 0)) >= lim.per_target_per_week:
        return _stop("rate-target-week")
    if int(hist.get("class_day", 0)) >= lim.per_class_per_day:
        return _stop("rate-class-day")
    if int(hist.get("fleet_day", 0)) >= lim.fleet_per_day:
        return _stop("rate-fleet-day")
    inflight = facts.get("inflight")
    if not isinstance(inflight, int) or isinstance(inflight, bool) or inflight < 0:
        return _stop("facts-incomplete", missing=["inflight"])
    if inflight >= 1:
        return _skip("rebuild-in-flight")  # queue length 1

    # --- last-chance backup (the undo path) ------------------------------------------------------------------------
    needs: dict = {}
    if cls.backup == "pbs-last-chance":
        if kind == "dead":
            age = _num(facts.get("last_backup_age_hours"))
            if age is None or age > policy.backup_max_age_hours:
                return _stop("backup-stale", max_hours=policy.backup_max_age_hours)
            needs["backup"] = "nightly-ok"
        else:
            needs["backup"] = "on-demand-first"

    # --- who may press the button ----------------------------------------------------------------------------------
    if mode == "auto":
        stage = policy.stages.get(cls.stage)
        if stage is None or stage.autonomy == "approval":
            return _stop("class-approval-only", stage=cls.stage)
        if not flags.get("autonomy_rebuild"):
            return _skip("autonomy-rebuild-off")
        if policy.policy_for(cls.name) is None:
            return _stop("no-enabled-policy", **{"class": cls.name})
        if stage.autonomy == "approval-then-auto" and int(hist.get("clean_rebuilds", 0)) < stage.approvals_before_auto:
            return _stop("approvals-first", need=stage.approvals_before_auto, have=int(hist.get("clean_rebuilds", 0)))

    return _go(why, kind=kind, create=(state == "missing"), **{"class": cls.name}, **needs)


# ---------------------------------------------------------------------------------------------------------------------
# (2) Plan checker
# ---------------------------------------------------------------------------------------------------------------------
#
# Document shape: `terraform show -json <planfile>`: resource_changes[] = {address, type, change: {actions, before,
# after, after_unknown}}, plus resource_drift[] (same shape; objects that changed outside Terraform) and `errored`.
#
# Identity attributes are read from the bpg/proxmox schema as used in this repo (see terraform/proxmox/asgard-lxcs/
# lxcs.tf, asgard-lxcs-root/lxcs.tf, asgard-k3s/main.tf). Assumptions, flagged because they are provider-version bound:
#   LXC (proxmox_virtual_environment_container): node_name, vm_id, initialization[0].hostname,
#       initialization[0].ip_config[0].ipv4[0].address, network_interface[0].vlan_id, operating_system[0].template_file_id
#   VM  (proxmox_virtual_environment_vm): node_name, vm_id, name, initialization[0].ip_config[0].ipv4[0].address,
#       network_device[0].vlan_id, clone[0].vm_id (the template)
# Nested blocks are lists of one object in the JSON. Attributes a plan marks "known after apply" (after_unknown true)
# are skipped: they cannot be compared and are never identity inputs here (all of the above are literals in our HCL).
# An attribute absent on both sides (a droplet has no vmid) is simply not compared.

IDENTITY_PATHS = {
    "node": ("node_name",),
    "vmid": ("vm_id",),
    "name": ("initialization", 0, "hostname"),
    "name_vm": ("name",),
    "ip": ("initialization", 0, "ip_config", 0, "ipv4", 0, "address"),
    "vlan": ("network_interface", 0, "vlan_id"),
    "vlan_vm": ("network_device", 0, "vlan_id"),
    "template": ("operating_system", 0, "template_file_id"),
    "template_vm": ("clone", 0, "vm_id"),
}
_MISSING = object()
_ACTIONS_REPLACE = (["delete", "create"], ["create", "delete"])


def _dig(obj: object, path: tuple) -> object:
    cur = obj
    for p in path:
        if isinstance(p, int):
            if not isinstance(cur, list) or p >= len(cur):
                return _MISSING
            cur = cur[p]
        else:
            if not isinstance(cur, dict) or p not in cur:
                return _MISSING
            cur = cur[p]
    return cur


def _unknown(change: dict, path: tuple) -> bool:
    u = change.get("after_unknown")
    got = _dig(u, path) if u is not None else _MISSING
    return got is True


def identity(change_side: object) -> dict:
    """The identity attributes present in a before/after object (name_vm/vlan_vm/template_vm fold into name/vlan/template)."""
    out = {}
    for key, path in IDENTITY_PATHS.items():
        v = _dig(change_side, path)
        if v is not _MISSING and v is not None:
            out[key.removesuffix("_vm")] = v
    return out


def _is_noop(actions: object) -> bool:
    return actions in (["no-op"], ["read"])


def check_plan(plan_json: object, expected: dict) -> list[str]:
    """Problems with a plan (empty list = the plan is exactly one in-scope replace/create of expected['address']).

    expected: {"address": str (required), "type": str (optional), "identity": dict (optional; the registry's
    name/vmid/node/ip/vlan values the guest must keep, compared to the plan's `after`)}.
    """
    problems: list[str] = []
    if not isinstance(plan_json, dict):
        return ["plan is not a JSON object"]
    addr = expected.get("address")
    if not isinstance(addr, str) or not addr:
        return ["no expected address given"]
    if plan_json.get("errored"):
        problems.append("the plan is marked errored")
    rcs = plan_json.get("resource_changes")
    if not isinstance(rcs, list):
        return problems + ["plan has no resource_changes list"]

    acting = [rc for rc in rcs if isinstance(rc, dict) and not _is_noop((rc.get("change") or {}).get("actions"))]
    if not acting:
        return problems + ["the plan has no changes (nothing to replace)"]
    others = [rc for rc in acting if rc.get("address") != addr]
    for rc in others:
        acts = (rc.get("change") or {}).get("actions")
        verb = "destroys" if isinstance(acts, list) and "delete" in acts else "changes"
        problems.append(f"the plan {verb} another resource: {_safe(rc.get('address'))} ({_safe(acts)})")
    if len(acting) > 1 and not others:
        problems.append(f"the plan has {len(acting)} changes for {addr}: expected exactly one")
    mine = [rc for rc in acting if rc.get("address") == addr]
    if not mine:
        problems.append(f"the plan does not touch the expected address {addr}")
        return problems
    rc = mine[0]
    if expected.get("type") and rc.get("type") != expected["type"]:
        problems.append(f"resource type is {_safe(rc.get('type'))}, expected {expected['type']}")
    ch = rc.get("change") or {}
    actions = ch.get("actions")
    before, after = ch.get("before"), ch.get("after")
    if actions in _ACTIONS_REPLACE:
        if not isinstance(before, dict) or not isinstance(after, dict):
            problems.append("a replace needs both before and after objects")
        else:
            problems += _identity_drift(before, after, ch)
    elif actions == ["create"]:
        if not isinstance(after, dict):
            problems.append("a create needs an after object")
    else:
        problems.append(f"action {_safe(actions)} is not replace or create")
    if isinstance(after, dict) and isinstance(expected.get("identity"), dict):
        got = identity(after)
        for k, want in expected["identity"].items():
            if k in got and got[k] != want:
                problems.append(f"identity {k} in the plan is {_safe(got[k])}, the registry says {_safe(want)}")
    # drift outside Terraform on anything but the target means state and reality disagree elsewhere
    for d in plan_json.get("resource_drift") or []:
        if isinstance(d, dict) and d.get("address") != addr:
            problems.append(f"resource drift on another resource: {_safe(d.get('address'))}")
    return problems


def _identity_drift(before: dict, after: dict, ch: dict) -> list[str]:
    out = []
    for key, path in IDENTITY_PATHS.items():
        b, a = _dig(before, path), _dig(after, path)
        if _unknown(ch, path):
            continue
        if b is _MISSING and a is _MISSING:
            continue
        if b != a:
            out.append(f"identity attribute {key.removesuffix('_vm')} changes: {_safe(None if b is _MISSING else b)} -> {_safe(None if a is _MISSING else a)}")
    return out


def _safe(v: object, n: int = 80) -> str:
    return re.sub(r"[^A-Za-z0-9._:/\[\]\"'=, -]", "?", str(v))[:n]


# ---------------------------------------------------------------------------------------------------------------------
# (3) Worker data manifest
# ---------------------------------------------------------------------------------------------------------------------

BACKUP_MAX_AGE_HOURS = 24.0  # single-instance data with no PBS backup of the worker /data disk this recent blocks
_SAFE_TEXT = re.compile(r"[^A-Za-z0-9._/-]")
_MANIFEST_LINES = 12


def clean(v: object, n: int = 48) -> str:
    """Plain text for an approval card: a closed character set (no markdown, mentions, newlines or control chars), bounded."""
    return _SAFE_TEXT.sub("?", str(v))[:n]


def _items(doc: object) -> list:
    items = doc.get("items") if isinstance(doc, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _pv_node(pv: dict) -> str | None:
    terms = ((pv.get("spec") or {}).get("nodeAffinity") or {}).get("required", {}).get("nodeSelectorTerms") or []
    for t in terms:
        for e in (t or {}).get("matchExpressions") or []:
            if e.get("key") in ("kubernetes.io/hostname", "topology.kubernetes.io/hostname") and e.get("values"):
                return str(e["values"][0])
    return None


def _is_local_path(pv: dict) -> bool:
    spec = pv.get("spec") or {}
    ann = (pv.get("metadata") or {}).get("annotations") or {}
    return spec.get("storageClassName") == "local-path" or "local-path" in str(ann.get("pv.kubernetes.io/provisioned-by", ""))


def _is_iscsi(pv: dict) -> bool:
    spec = pv.get("spec") or {}
    return "synology" in str((spec.get("csi") or {}).get("driver", "")) or "iscsi" in str(spec.get("storageClassName", ""))


def worker_data_manifest(pv_json: object, pods_json: object, backup_age_hours: object, node: str) -> dict:
    """Classify the PVs a worker's rebuild would destroy or detach, from `kubectl get pv -o json` and
    `kubectl get pods -A -o json`.

    local-path PVs are pinned to `node` by nodeAffinity and die with it: *replicated* when the consuming pod is a Vault
    Raft member (label app.kubernetes.io/name=vault; Raft resyncs), else *single-instance* (the only copy; also the
    conservative reading of a PV with no consumer). iSCSI PVCs are listed (they detach; they are not lost). `blocks` is
    true when single-instance data has no PBS backup of the worker /data disk within 24 h.
    """
    consumers: dict[tuple, list[dict]] = {}
    for pod in _items(pods_json):
        ns = (pod.get("metadata") or {}).get("namespace")
        for vol in (pod.get("spec") or {}).get("volumes") or []:
            claim = ((vol or {}).get("persistentVolumeClaim") or {}).get("claimName")
            if claim:
                consumers.setdefault((ns, claim), []).append(pod)
    pods_on_node = {(p.get("metadata") or {}).get("name") for p in _items(pods_json) if (p.get("spec") or {}).get("nodeName") == node}

    local, iscsi = [], []
    for pv in _items(pv_json):
        ref = (pv.get("spec") or {}).get("claimRef") or {}
        key = (ref.get("namespace"), ref.get("name"))
        pods = consumers.get(key, [])
        entry = {
            "pv": clean((pv.get("metadata") or {}).get("name")),
            "namespace": clean(ref.get("namespace", "-")),
            "pvc": clean(ref.get("name", "-")),
            "size": clean(((pv.get("spec") or {}).get("capacity") or {}).get("storage", "?"), 12),
        }
        if _is_local_path(pv):
            if _pv_node(pv) != node:
                continue
            vault = any(((p.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/name") == "vault" for p in pods)
            entry["kind"] = "replicated" if vault else "single-instance"
            entry["owner"] = clean((pods[0].get("metadata") or {}).get("name")) if pods else "none"
            local.append(entry)
        elif _is_iscsi(pv):
            if any((p.get("metadata") or {}).get("name") in pods_on_node for p in pods):
                entry["owner"] = clean((pods[0].get("metadata") or {}).get("name"))
                iscsi.append(entry)

    reasons: list[str] = []
    single = [e for e in local if e["kind"] == "single-instance"]
    age = _num(backup_age_hours)
    if single:
        if age is None:
            reasons.append("single-instance data on the node and no PBS backup of the worker /data disk is known")
        elif age > BACKUP_MAX_AGE_HOURS:
            reasons.append(f"single-instance data on the node and the last PBS backup is {age:.0f} h old (limit {BACKUP_MAX_AGE_HOURS:.0f} h)")
    out = {
        "node": clean(node),
        "local_path": local,
        "iscsi": iscsi,
        "backup_age_hours": age,
        "blocks": bool(reasons),
        "reasons": reasons,
    }
    out["summary"] = manifest_text(out)
    return out


def manifest_text(m: dict) -> str:
    """The short human manifest for an approval card (already sanitised fields only; bounded)."""
    lines = [f"Data on {m['node']} if rebuilt:"]
    for e in m["local_path"][:_MANIFEST_LINES]:
        what = "replicated (Raft resyncs)" if e["kind"] == "replicated" else "SINGLE-INSTANCE (dies with the node)"
        lines.append(f"- {e['namespace']}/{e['pvc']} {e['size']}: {what}")
    if len(m["local_path"]) > _MANIFEST_LINES:
        lines.append(f"- ... and {len(m['local_path']) - _MANIFEST_LINES} more local-path volume(s)")
    if not m["local_path"]:
        lines.append("- no local-path volumes")
    if m["iscsi"]:
        lines.append("iSCSI volumes that detach: " + ", ".join(f"{e['namespace']}/{e['pvc']}" for e in m["iscsi"][:_MANIFEST_LINES]))
    age = m["backup_age_hours"]
    lines.append("Last PBS backup of /data: " + (f"{age:.0f} h ago" if age is not None else "unknown"))
    lines.append("BLOCKED: " + "; ".join(m["reasons"]) if m["blocks"] else "Manifest clean.")
    return "\n".join(lines)
