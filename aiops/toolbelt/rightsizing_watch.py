"""The 72-hour post-merge watch of a rightsizing PR (Phase 10i5).

When a `rightsizing` change request is reported merged, a watch starts. It first WAITS until the new values are really live (every pod
of the workload started after the merge, carries the new requests/limits, is Ready and the controller is available); that is the data-driven
version of "the HelmRelease is Ready at the new revision", and it cannot be fooled by a Flux that applied nothing. Then it WATCHES for 72 hours,
reading VictoriaMetrics once an interval:

  OOMKilled since going live        -> regressed          working set above 90 % of the new limit -> regressed
  CrashLoopBackOff                  -> regressed          two or more restarts since going live   -> regressed (one restart is a warning)
  controller not fully available for two checks in a row -> regressed
  an alert since going live that names the workload       -> regressed

No regression for 72 h is `held`. A workload whose new values never showed up within 24 h of the merge is `inconclusive` (look at Flux). The verdict
is recorded on the watch and on the change request, shown in the next digest, and a regression offers a Draft revert PR. Nothing reverts by itself.

The change request carries a machine-readable spec in its body (`<!-- rightsizing-spec {...} -->`, written by the Toolbelt when the PR is requested):
the workload, and per container the old and the new request/limit. The watch trusts only that block, which the Toolbelt itself wrote.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import urllib.parse
from dataclasses import dataclass
from typing import Callable

import rightsizing

MIB = rightsizing.MIB
SPEC_RX = re.compile(r"<!-- rightsizing-spec (\{.*?\}) -->", re.S)
CONTROLLER = re.compile(r"^([a-z0-9-]+)/(Deployment|StatefulSet|DaemonSet)/([a-z0-9.-]+)$")
VALUE_KEYS = ("request_mib", "limit_mib", "request_millicores")
ACTIVE = ("waiting", "watching")

SCHEMA = """
CREATE TABLE IF NOT EXISTS rightsizing_watches (
  id INTEGER PRIMARY KEY AUTOINCREMENT, cr_id INTEGER NOT NULL UNIQUE, pr_url TEXT, controller TEXT NOT NULL, spec_json TEXT NOT NULL,
  state TEXT NOT NULL, created_at INTEGER NOT NULL, merged_at INTEGER NOT NULL, live_at INTEGER, until_at INTEGER,
  last_check_at INTEGER, checks_json TEXT, verdict_json TEXT, finished_at INTEGER, reverted_by INTEGER
);
CREATE INDEX IF NOT EXISTS rs_watch_state ON rightsizing_watches(state);
"""


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class WatchConfig:
    hold_seconds: int = 72 * 3600
    ready_timeout: int = 24 * 3600
    workingset_fraction: float = 0.90
    restart_regress: int = 2
    unavailable_checks: int = 2


# ---- the spec ----------------------------------------------------------------------------------------------------------------------------


def spec_block(spec: dict) -> str:
    return "<!-- rightsizing-spec " + json.dumps(spec, sort_keys=True, separators=(",", ":")) + " -->"


def validate_spec(spec: object) -> dict:
    """The spec or a Refused. Numbers only, a closed set of keys, a workload name that cannot smuggle a query."""
    if not isinstance(spec, dict) or spec.get("v") != 1:
        raise Refused(400, "spec must be an object with v=1")
    if not isinstance(spec.get("controller"), str) or not CONTROLLER.match(spec["controller"]):
        raise Refused(400, "spec.controller must be namespace/Kind/name")
    cs = spec.get("containers")
    if not isinstance(cs, dict) or not cs or len(cs) > 20:
        raise Refused(400, "spec.containers must hold 1-20 containers")
    for name, c in cs.items():
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}", str(name)) or not isinstance(c, dict) or set(c) - {"old", "new"} or "new" not in c:
            raise Refused(400, f"spec.containers[{name!r}] must be {{old, new}}")
        for side in ("old", "new"):
            v = c.get(side, {})
            if not isinstance(v, dict) or set(v) - set(VALUE_KEYS) or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0 < x < 10_000_000 for x in v.values()):
                raise Refused(400, f"spec.containers[{name!r}].{side} holds numbers for {', '.join(VALUE_KEYS)} only")
        if not c["new"]:
            raise Refused(400, f"spec.containers[{name!r}].new is empty")
    if spec.get("kind", "trim") not in ("trim", "revert"):
        raise Refused(400, "spec.kind must be trim or revert")
    if not isinstance(spec.get("freed_mib", 0), (int, float)):
        raise Refused(400, "spec.freed_mib must be a number")
    return spec


def parse_spec(body: str) -> dict | None:
    m = SPEC_RX.search(body or "")
    if not m:
        return None
    try:
        return validate_spec(json.loads(m.group(1)))
    except (ValueError, Refused):
        return None


def revert_spec(spec: dict) -> dict:
    """The same workload with old and new swapped (a container that had no old value for a key keeps the new one: nothing to restore)."""
    cs = {}
    for name, c in spec["containers"].items():
        cs[name] = {"old": c["new"], "new": {**c["new"], **c.get("old", {})}}
    return {"v": 1, "controller": spec["controller"], "kind": "revert", "freed_mib": -float(spec.get("freed_mib", 0)), "containers": cs}


# ---- the watch ---------------------------------------------------------------------------------------------------------------------------


class Watches:
    def __init__(self, db: sqlite3.Connection, lock: threading.RLock, clock: Callable[[], float], audit: Callable[..., None],
                 make_vm: Callable[[], "rightsizing.VM | None"], note: Callable[[int, str, dict], None],
                 alerts_since: Callable[[str, int], list] | None = None, cfg: WatchConfig | None = None):
        self.db, self.lock, self.clock, self.audit = db, lock, clock, audit
        self.make_vm, self.note, self.alerts_since, self.cfg = make_vm, note, alerts_since, cfg or WatchConfig()
        self.db.executescript(SCHEMA)

    def now(self) -> int:
        return int(self.clock())

    # -- lifecycle -----------------------------------------------------------------------------------------------------------------------
    def start(self, cr: dict) -> dict | None:
        """Called when a rightsizing change request is reported merged. None when its body carries no valid spec (an operator-written request)."""
        spec = parse_spec(cr.get("body") or "")
        if spec is None:
            self.audit("rightsizing_watch_skipped", cr=cr.get("id"), reason="no spec")
            return None
        with self.lock:
            row = self.db.execute("SELECT id FROM rightsizing_watches WHERE cr_id=?", (cr["id"],)).fetchone()
            if row:
                return self.get(row["id"])
            now = self.now()
            cur = self.db.execute("INSERT INTO rightsizing_watches(cr_id, pr_url, controller, spec_json, state, created_at, merged_at) VALUES (?,?,?,?,?,?,?)",
                                  (cr["id"], cr.get("pr_url"), spec["controller"], json.dumps(spec, sort_keys=True), "waiting", now, now))
            wid = cur.lastrowid
        self.audit("rightsizing_watch_started", watch=wid, cr=cr["id"], controller=spec["controller"])
        return self.get(wid)

    def get(self, wid: int) -> dict:
        with self.lock:
            r = self.db.execute("SELECT * FROM rightsizing_watches WHERE id=?", (int(wid),)).fetchone()
        if r is None:
            raise Refused(404, f"no watch {wid}")
        d = {k: r[k] for k in r.keys() if k not in ("spec_json", "checks_json", "verdict_json")}
        d["spec"] = json.loads(r["spec_json"])
        d["checks"] = json.loads(r["checks_json"]) if r["checks_json"] else None
        d["verdict"] = json.loads(r["verdict_json"]) if r["verdict_json"] else None
        return d

    def list(self, states: tuple = ACTIVE) -> list[dict]:
        q = ",".join("?" * len(states))
        with self.lock:
            ids = [r["id"] for r in self.db.execute(f"SELECT id FROM rightsizing_watches WHERE state IN ({q}) ORDER BY id", states).fetchall()]
        return [self.get(i) for i in ids]

    def held(self, controller: str) -> bool:
        """The latest watch of this workload held: the condition for proposing to cut its LIMIT (the request cut stood up)."""
        with self.lock:
            r = self.db.execute("SELECT state FROM rightsizing_watches WHERE controller=? AND json_extract(spec_json, '$.kind') IS NOT 'revert' ORDER BY id DESC LIMIT 1",
                                (controller,)).fetchone()
        return bool(r and r["state"] == "held")

    def results(self, limit: int = 8) -> list[dict]:
        """For the digest: the latest watches, newest first, with what they freed and how they ended."""
        with self.lock:
            ids = [r["id"] for r in self.db.execute("SELECT id FROM rightsizing_watches ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
        out = []
        for i in ids:
            w = self.get(i)
            v = (w["verdict"] or {}).get("verdict") or ("watching" if w["state"] == "watching" else "waiting for the rollout")
            out.append({"watch": w["id"], "cr": w["cr_id"], "target": w["controller"], "verdict": v, "pr_url": w["pr_url"],
                        "freed_mib": w["spec"].get("freed_mib") if v in ("held", "watching", "waiting for the rollout") else None,
                        "reasons": (w["verdict"] or {}).get("reasons", [])})
        return out

    def mark_reverted(self, wid: int, cr_id: int) -> None:
        with self.lock:
            self.db.execute("UPDATE rightsizing_watches SET reverted_by=? WHERE id=?", (cr_id, int(wid)))

    # -- the loop --------------------------------------------------------------------------------------------------------------------------
    def poll(self) -> int:
        """Check every active watch once. A failing watch is audited and left for the next round. Returns how many were checked."""
        vm = self.make_vm()
        if vm is None:
            return 0
        n = 0
        for w in self.list():
            try:
                self._check(vm, w)
                n += 1
            except Exception as e:  # noqa: BLE001 - one watch must not stop the others
                self.audit("rightsizing_watch_error", watch=w["id"], error=type(e).__name__, detail=str(e)[:120])
        return n

    def _finish(self, w: dict, state: str, verdict: dict) -> None:
        now = self.now()
        with self.lock:
            self.db.execute("UPDATE rightsizing_watches SET state=?, verdict_json=?, finished_at=?, last_check_at=? WHERE id=?", (state, json.dumps(verdict, sort_keys=True), now, now, w["id"]))
        self.note(w["cr_id"], "watch", {"watch": w["id"], "state": state, **verdict})
        self.audit("rightsizing_watch_verdict", watch=w["id"], cr=w["cr_id"], state=state, reasons=verdict.get("reasons"))

    def _check(self, vm: "rightsizing.VM", w: dict) -> None:
        now = self.now()
        spec = w["spec"]
        ns, kind, name = spec["controller"].split("/")
        obs = self.observe(vm, w, now)
        if w["state"] == "waiting":
            if self._live(obs, spec, w["merged_at"], now):
                with self.lock:
                    self.db.execute("UPDATE rightsizing_watches SET state='watching', live_at=?, until_at=?, last_check_at=?, checks_json=? WHERE id=?",
                                    (now, now + self.cfg.hold_seconds, now, json.dumps({"live": True}), w["id"]))
                self.audit("rightsizing_watch_live", watch=w["id"], until=now + self.cfg.hold_seconds)
            elif now - w["merged_at"] > self.cfg.ready_timeout:
                self._finish(w, "inconclusive", {"verdict": "inconclusive", "reasons": ["the new values were not live on every pod within 24 h of the merge (check Flux and the HelmRelease)"], "warnings": []})
            else:
                with self.lock:
                    self.db.execute("UPDATE rightsizing_watches SET last_check_at=? WHERE id=?", (now, w["id"]))
            return
        reasons, warnings = self._evaluate(obs, spec, w, now)
        prev = w.get("checks") or {}
        unavailable = (prev.get("unavailable_checks", 0) + 1) if obs["available"] is not None and obs["available"] < obs["desired"] else 0
        if unavailable >= self.cfg.unavailable_checks:
            reasons.append(f"the workload was not fully available on {unavailable} checks in a row ({obs['available']} of {obs['desired']} ready)")
        checks = {"unavailable_checks": unavailable, "warnings": warnings, "working_set_mib": obs["ws_mib"], "restarts": obs["restarts_total"], "at": now}
        with self.lock:
            self.db.execute("UPDATE rightsizing_watches SET last_check_at=?, checks_json=? WHERE id=?", (now, json.dumps(checks, sort_keys=True), w["id"]))
        if reasons:
            self._finish(w, "regressed", {"verdict": "regressed", "reasons": reasons, "warnings": warnings, "checks": checks})
        elif now >= (w["until_at"] or now + 1):
            self._finish(w, "held", {"verdict": "held", "reasons": [], "warnings": warnings, "checks": checks})

    # -- reading VictoriaMetrics -----------------------------------------------------------------------------------------------------------
    def observe(self, vm: "rightsizing.VM", w: dict, now: int) -> dict:
        ns, kind, name = w["spec"]["controller"].split("/")
        since = max(60, now - (w["live_at"] or w["merged_at"]))
        win = f"{since}s"
        nsel = f'namespace="{ns}"'

        def q(label: str, promql: str):
            try:
                return vm.instant(promql)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"{label}: {type(e).__name__}") from e

        owners = rightsizing.pod_controllers(lambda label, promql: q(label, promql), ns, f"{max(since, now - w['merged_at']) + 600}s")
        pods = {p for (n, p), ctl in owners.items() if ctl == (ns, kind, name)}
        age = {m["pod"]: v for m, v in q("age", f"max by (pod)(time() - kube_pod_start_time{{{nsel}}})") if m.get("pod") in pods}
        pods = set(age)   # only pods that exist now
        ready = {m["pod"]: v for m, v in q("ready", f'max by (pod)(kube_pod_status_ready{{{nsel},condition="true"}})') if m.get("pod") in pods}
        spec: dict = {}
        for side, metric in (("req", "kube_pod_container_resource_requests"), ("lim", "kube_pod_container_resource_limits")):
            for m, v in q(side, f'max by (pod,container,resource)({metric}{{{nsel},resource=~"cpu|memory"}})'):
                if m.get("pod") in pods:
                    spec[(m["pod"], m["container"], side, m["resource"])] = v
        ws = {(m["pod"], m["container"]): v for m, v in q("workingset", f'max by (pod,container)(max_over_time(container_memory_working_set_bytes{{{nsel},container!="",container!="POD"}}[{win}]))')
              if m.get("pod") in pods}
        rst = {(m["pod"], m["container"]): v for m, v in q("restarts", f"sum by (pod,container)(increase(kube_pod_container_status_restarts_total{{{nsel}}}[{win}]))") if m.get("pod") in pods}
        oom_reason = {(m["pod"], m["container"]) for m, v in q("oom", f'max by (pod,container)(kube_pod_container_status_last_terminated_reason{{{nsel},reason="OOMKilled"}})') if m.get("pod") in pods and v}
        oom_ts = {(m["pod"], m["container"]): v for m, v in q("oom-ts", f"max by (pod,container)(kube_pod_container_status_last_terminated_timestamp{{{nsel}}})") if m.get("pod") in pods}
        loop = {(m["pod"], m["container"]) for m, v in q("crashloop", f'max by (pod,container)(kube_pod_container_status_waiting_reason{{{nsel},reason="CrashLoopBackOff"}})') if m.get("pod") in pods and v}
        avail, desired = self._availability(q, ns, kind, name)
        live_at = w["live_at"] or 0
        containers = sorted({c for (_, c, _, _) in spec} | {c for (_, c) in ws})
        oom = sorted({c for (p, c) in oom_reason if oom_ts.get((p, c), 0) >= live_at and live_at})
        return {"pods": sorted(pods), "age": age, "ready": ready, "spec": spec, "ws": ws, "restarts": rst, "oom": oom, "crashloop": sorted({c for _, c in loop}),
                "available": avail, "desired": desired, "containers": containers, "restarts_total": round(sum(rst.values()), 2),
                "ws_mib": round(max(ws.values()) / MIB, 1) if ws else None}

    @staticmethod
    def _availability(q, ns: str, kind: str, name: str) -> tuple[float | None, float | None]:
        sel = {"Deployment": ("kube_deployment_status_replicas_available", "kube_deployment_spec_replicas", "deployment"),
               "StatefulSet": ("kube_statefulset_status_replicas_ready", "kube_statefulset_replicas", "statefulset"),
               "DaemonSet": ("kube_daemonset_status_number_ready", "kube_daemonset_status_desired_number_scheduled", "daemonset")}[kind]
        got = []
        for metric in sel[:2]:
            rows = q(metric, f'max({metric}{{namespace="{ns}",{sel[2]}="{name}"}})')
            got.append(rows[0][1] if rows else None)
        return got[0], got[1]

    # -- judging ---------------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _expected(c: dict) -> dict:
        n = c["new"]
        return {("req", "memory"): n["request_mib"] * MIB if "request_mib" in n else None, ("lim", "memory"): n["limit_mib"] * MIB if "limit_mib" in n else None,
                ("req", "cpu"): n["request_millicores"] / 1000 if "request_millicores" in n else None}

    def _live(self, obs: dict, spec: dict, merged_at: int, now: int) -> bool:
        if not obs["pods"] or obs["desired"] in (None, 0) or obs["available"] is None or obs["available"] < obs["desired"]:
            return False
        if any(a > now - merged_at for a in obs["age"].values()):          # a pod that started before the merge still runs the old template
            return False
        if any(not obs["ready"].get(p) for p in obs["pods"]):
            return False
        for cname, c in spec["containers"].items():
            for p in obs["pods"]:
                for (side, res), want in self._expected(c).items():
                    if want is None:
                        continue
                    got = obs["spec"].get((p, cname, side, res))
                    if got is None or abs(got - want) > (MIB if res == "memory" else 0.0015):
                        return False
        return True

    def _evaluate(self, obs: dict, spec: dict, w: dict, now: int) -> tuple[list, list]:
        reasons, warnings = [], []
        if obs["oom"]:
            reasons.append("OOMKilled since the new values went live: " + ", ".join(obs["oom"]))
        if obs["crashloop"]:
            reasons.append("CrashLoopBackOff: " + ", ".join(obs["crashloop"]))
        if obs["restarts_total"] >= self.cfg.restart_regress:
            reasons.append(f"{obs['restarts_total']:g} container restarts since the new values went live")
        elif obs["restarts_total"] >= 1:
            warnings.append(f"{obs['restarts_total']:g} container restart since the new values went live")
        for cname, c in spec["containers"].items():
            limit = (c["new"].get("limit_mib") or (c.get("old") or {}).get("limit_mib"))
            if limit:
                worst = max((v for (p, cn), v in obs["ws"].items() if cn == cname), default=None)
                if worst is not None and worst > self.cfg.workingset_fraction * limit * MIB:
                    reasons.append(f"{cname}: working set {worst / MIB:.0f} MiB is above {int(self.cfg.workingset_fraction * 100)} % of the {limit:g} MiB limit")
        if self.alerts_since is not None and w["live_at"]:
            for a in self.alerts_since(spec["controller"].split("/")[2], w["live_at"])[:3]:
                reasons.append("an alert names this workload: " + str(a)[:120])
        return reasons, warnings


def describe_spec(spec: dict) -> str:
    """The per-container old -> new lines (Markdown), used in change-request bodies and PR evidence."""
    rows = []
    for name, c in sorted(spec["containers"].items()):
        old, new = c.get("old", {}), c["new"]
        parts = []
        for key, label, unit in (("request_mib", "memory request", " MiB"), ("limit_mib", "memory limit", " MiB"), ("request_millicores", "CPU request", "m")):
            if key in new:
                parts.append(f"{label} {old.get(key, '?'):g}{unit} → {new[key]:g}{unit}" if key in old else f"{label} → {new[key]:g}{unit}")
        rows.append(f"- `{name}`: " + ", ".join(parts))
    return "\n".join(rows)


def revert_request(w: dict) -> tuple[str, str]:
    """(title, body) of the change request that restores the values a regressed watch replaced. Resources only; the spec block lets the
    revert be watched like any other change."""
    spec = revert_spec(w["spec"])
    ns, kind, name = spec["controller"].split("/")
    why = "; ".join((w.get("verdict") or {}).get("reasons") or ["an operator asked for it"])[:600]
    body = (f"Restore the previous resources of {kind} `{name}` in namespace `{ns}`. Rightsizing change request #{w['cr_id']} was watched for 72 hours and "
            f"regressed: {why}.\n\nFind the HelmRelease that deploys it under k8s/asgard/ and put these values back (resources only; change nothing else, "
            f"no image, replica or other value):\n{describe_spec(spec)}\n\n"
            "If the chart sets resources through a preset, restore the preset or the explicit block exactly as it was in the commit before the rightsizing change "
            "(`git log -p` on the file shows it). In the summary say which file you changed and the old and new values.\n\n" + spec_block(spec))
    return f"Revert rightsizing of {name}"[:120], body


def vmui(expr: str) -> str:
    return "https://metric.niflheim.xiiisins.com/vmui/#/?g0.range_input=7d&g0.expr=" + urllib.parse.quote(expr, safe="")
