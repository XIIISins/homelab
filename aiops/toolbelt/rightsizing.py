"""Pod rightsizing data and findings (Phase 10i2): what the `kube.rightsizing` tool returns and what the daily findings pass produces.

Everything comes from VictoriaMetrics (cAdvisor, kube-state-metrics and kube-state-metrics' VPA series), so the same code runs in the
Toolbelt API (through the metrics-read route its `metrics.*` tools already use) and in the forecast job on Frigg. It never talks to the
Kubernetes API, never writes anything, and proposes numbers only: a change to a request or limit stays a reviewed PR (10i4).

The rules are the table in docs/plans/active/10i-rightsizing.md (10i2); thresholds live in aiops/rightsizing.yml. Memory is handled with
care (too low means OOMKilled), CPU is compressible, and a CPU LIMIT is never proposed.

Layout: pure helpers and `evaluate()` at the top (no I/O, unit-tested on hand-built facts); `collect()` turns PromQL results into those
facts; `summary()` / `detail()` shape the tool answers; `findings()` is the daily pass.
"""
from __future__ import annotations

import fnmatch
import json
import math
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

MIB = 1024 * 1024
DAY = 86400.0
KINDS = {"deployments": "Deployment", "statefulsets": "StatefulSet", "daemonsets": "DaemonSet"}
FINDING_KIND = "rightsizing"

# ---- config ---------------------------------------------------------------------------------------------------------------------------


def load_config(path: str | Path) -> dict:
    import yaml

    return yaml.safe_load(Path(path).read_text())


def matches(patterns: list, key: str) -> bool:
    """`namespace/Kind/name` against fnmatch patterns ("immich/*" matches everything in the namespace; `*` crosses `/`)."""
    return any(fnmatch.fnmatchcase(key, p) for p in patterns)


# ---- rounding ---------------------------------------------------------------------------------------------------------------------------


def ceil16mi(b: float) -> int:
    return int(math.ceil(b / (16 * MIB)) * 16 * MIB)


def ceil10m(cores: float) -> float:
    return math.ceil(round(cores * 100, 6)) / 100  # whole 10m steps, in cores


def mib(b: float | None) -> float | None:
    return None if b is None else round(b / MIB, 1)


def millicores(c: float | None) -> float | None:
    return None if c is None else round(c * 1000, 1)


# ---- facts ----------------------------------------------------------------------------------------------------------------------------


@dataclass
class Vpa:
    """One resource's recommendation for one container (kube-state-metrics' VPA series), cores or bytes."""
    lower: float | None = None
    target: float | None = None
    upper: float | None = None
    age_s: float | None = None      # how long the target series has existed (lifetime of the series, capped by the query window)
    hi7: float | None = None        # target's 7-day max / min: the stability check
    lo7: float | None = None


@dataclass
class Container:
    namespace: str
    kind: str
    name: str
    container: str
    pods: int = 0
    youngest_pod_age_s: float | None = None
    req_cpu: float | None = None      # cores
    req_mem: float | None = None      # bytes
    lim_cpu: float | None = None
    lim_mem: float | None = None
    mem_max: float | None = None
    mem_p50: float | None = None
    mem_p95: float | None = None
    cpu_p95: float | None = None      # cores: the MEDIAN of the daily p95 (a spike or a rollout does not move it)
    cpu_worst: float | None = None    # cores: the worst day's p95 (the floor a request must not go below)
    oom: bool = False
    restarts: float = 0
    vpa_cpu: Vpa = field(default_factory=Vpa)
    vpa_mem: Vpa = field(default_factory=Vpa)
    daily_mem_max: list = field(default_factory=list)   # [(ts, bytes)], one point per day, max across the controller's pods

    @property
    def controller(self) -> str:
        return f"{self.namespace}/{self.kind}/{self.name}"

    @property
    def key(self) -> str:
        return f"{self.controller}/{self.container}"


def vpa_valid(v: Vpa, cfg: dict) -> tuple[bool, str]:
    """A recommendation counts only when it exists, its series is old enough and its target has not moved more than the drift."""
    if v.target is None or v.upper is None:
        return False, "no-vpa-recommendation"
    if (v.age_s or 0) < cfg["vpa"]["min_sample_age_days"] * DAY:
        return False, "vpa-immature"
    d = cfg["vpa"]["max_target_drift"]
    if v.hi7 is not None and v.hi7 > v.target * (1 + d) or v.lo7 is not None and v.lo7 < v.target * (1 - d):
        return False, "vpa-unstable"
    return True, ""


def theil_sen(points: list) -> tuple[float, float]:
    """(slope per second, fraction of day pairs that rise) over [(ts, value)]. Robust to a single rollout spike, unlike a least-squares line."""
    slopes = [(b[1] - a[1]) / (b[0] - a[0]) for i, a in enumerate(points) for b in points[i + 1:] if b[0] > a[0]]
    if not slopes:
        return 0.0, 0.0
    s = sorted(slopes)
    n = len(s)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return med, sum(1 for x in slopes if x > 0) / n


# ---- the rules -------------------------------------------------------------------------------------------------------------------------


def _confidence(v: Vpa, ok: bool) -> str:
    """High only with a mature, stable recommendation that has seen two weeks; low when the finding stands on the raw 30-day numbers alone."""
    return "high" if ok and (v.age_s or 0) >= 14 * DAY else "medium" if ok else "low"


def _finding(c: Container, metric: str, name: str, now: float, confidence: str, evidence: dict) -> dict:
    return {"fingerprint": f"{FINDING_KIND}:{c.key}/{metric}", "kind": FINDING_KIND, "metric": metric, "target": c.key, "confidence": confidence,
            "days_to_full": None, "ratio": None, "ts": now, "evidence": {"finding": name, **evidence}}


def evaluate(c: Container, cfg: dict, now: float) -> tuple[list, list]:
    """(findings, suppressed reasons) for one container. Pure: facts and config in, dicts out.

    A suppressed reason says why a finding that could exist was not made; the tool shows them and the digest counts them (coverage)."""
    out: list = []
    why: list = []
    if matches(cfg.get("ignore", []), c.controller):
        return out, ["ignored"]
    if c.youngest_pod_age_s is not None and c.youngest_pod_age_s < cfg["settle_hours"] * 3600:
        return out, ["recently-rolled"]
    mem, cpu = cfg["memory"], cfg["cpu"]
    ok_mem, why_mem = vpa_valid(c.vpa_mem, cfg)
    ok_cpu, why_cpu = vpa_valid(c.vpa_cpu, cfg)
    conf_mem, conf_cpu = _confidence(c.vpa_mem, ok_mem), _confidence(c.vpa_cpu, ok_cpu)

    # -- memory request ---------------------------------------------------------------------------------------------------------------
    rare = matches(cfg.get("rare_peaks", []), c.controller)
    # A rare-peak workload is sized from its p95 on purpose (a model load, a monthly import): a peak above the request is expected there
    peak = (c.mem_p95 if rare and c.mem_p95 is not None else c.mem_max)
    if peak is not None and c.req_mem:
        base = max(c.vpa_mem.upper if ok_mem and not rare else 0.0, peak)
        under = mem["under_request"]
        if peak > c.req_mem or c.oom:
            # the one finding that does not wait for a mature recommendation: a hazard is worth saying early, from the 30-day max alone
            prop = {"request_bytes": max(ceil16mi(base * under["margin"]), int(c.req_mem))}   # an under-request finding never lowers anything
            if c.lim_mem and (c.oom or peak > under["limit_trigger"] * c.lim_mem):
                new_lim = ceil16mi(max(prop["request_bytes"], c.lim_mem if c.oom else 0) * under["limit_headroom"])
                if new_lim > c.lim_mem:
                    prop["limit_bytes"] = new_lim
            changed = prop["request_bytes"] > c.req_mem or "limit_bytes" in prop
            if changed or c.oom:
                # An OOMKilled container whose 30-day peak sits far below its limit has no number to propose: the kill happened under an older
                # limit, or the cgroup counted page cache the working set does not. It is still the first thing to look at, so it is reported.
                out.append(_finding(c, "memory-request", "memory-under-request", now, conf_mem, {
                    "request_mib": mib(c.req_mem), "limit_mib": mib(c.lim_mem), "max_working_set_mib": mib(c.mem_max), "oomkilled": c.oom,
                    "vpa_upper_mib": mib(c.vpa_mem.upper), "proposed_request_mib": mib(prop["request_bytes"]) if changed else None,
                    "proposed_limit_mib": mib(prop.get("limit_bytes")), "added_bytes": max(prop["request_bytes"] - int(c.req_mem), 0),
                    **({} if changed else {"note": "OOMKilled, but the 30-day peak working set is far below the limit: check the events and the app's cache behaviour; no number is proposed"})}))
        else:
            ov = mem["over_request"]
            if c.req_mem >= ov["ratio"] * base:
                if not ok_mem:
                    why.append(why_mem)
                else:
                    new_req = max(ceil16mi(base * ov["margin"]), int(ov["floor_mib"] * MIB))
                    freed = c.req_mem - new_req
                    if freed >= ov["min_free_mib"] * MIB:
                        out.append(_finding(c, "memory-request", "memory-over-request", now, conf_mem, {
                            "request_mib": mib(c.req_mem), "limit_mib": mib(c.lim_mem), "max_working_set_mib": mib(c.mem_max),
                            "vpa_upper_mib": mib(c.vpa_mem.upper), "vpa_target_mib": mib(c.vpa_mem.target), "proposed_request_mib": mib(new_req),
                            "freed_bytes": int(freed)}))

    # -- memory limit (off until the 10i5 watch exists) ------------------------------------------------------------------------------
    ol = mem["over_limit"]
    if ol["enabled"] and c.lim_mem and c.mem_max and not c.oom and not rare:
        ok = ok_mem
        base = max(c.vpa_mem.upper if ok else 0.0, c.mem_max)
        if ok and c.lim_mem >= ol["ratio"] * c.mem_max and not any(f["metric"] == "memory-request" for f in out):
            new_lim = ceil16mi(max(2 * c.mem_max, base * 1.5, c.req_mem or 0))
            if c.lim_mem - new_lim >= ol["min_free_mib"] * MIB:
                out.append(_finding(c, "memory-limit", "memory-over-limit", now, conf_mem, {
                    "limit_mib": mib(c.lim_mem), "max_working_set_mib": mib(c.mem_max), "proposed_limit_mib": mib(new_lim), "freed_bytes": int(c.lim_mem - new_lim)}))

    # -- memory creep ---------------------------------------------------------------------------------------------------------------
    cr = mem["creep"]
    pts = [p for p in c.daily_mem_max if p[1] is not None]
    if len(pts) >= cr["min_days"] and not c.oom and c.restarts <= cr["max_restarts"]:
        slope, rising = theil_sen(pts)
        span_days = (pts[-1][0] - pts[0][0]) / DAY
        first = sorted(v for _, v in pts[:7])
        base = first[len(first) // 2]
        rise = slope * DAY * span_days
        if rising >= cr["min_rising_fraction"] and rise >= max(cr["min_rise_mib"] * MIB, cr["min_rise_fraction"] * base):
            out.append(_finding(c, "memory-creep", "memory-creep", now, "medium", {
                "slope_mib_per_day": round(slope * DAY / MIB, 2), "rise_mib": round(rise / MIB, 1), "first_week_median_mib": mib(base),
                "latest_daily_peak_mib": mib(pts[-1][1]), "days": len(pts), "rising_pairs_fraction": round(rising, 2),
                "request_mib": mib(c.req_mem), "limit_mib": mib(c.lim_mem)}))

    # -- CPU request ----------------------------------------------------------------------------------------------------------------
    if c.req_cpu and c.cpu_p95 is not None and c.cpu_worst is not None:
        ev = {"request_millicores": millicores(c.req_cpu), "median_daily_p95_millicores": millicores(c.cpu_p95),
              "worst_day_p95_millicores": millicores(c.cpu_worst), "vpa_target_millicores": millicores(c.vpa_cpu.target)}
        floor = cfg["floors"]["cpu_millicores"] / 1000
        if c.cpu_p95 > c.req_cpu:   # a normal day needs more than the request: sustained, not a spike
            new_req = max(ceil10m(max(c.vpa_cpu.target if ok_cpu else 0.0, c.cpu_p95) * cpu["under_request"]["margin"]), ceil10m(c.cpu_worst), floor)
            if new_req > c.req_cpu:
                out.append(_finding(c, "cpu-request", "cpu-under-request", now, conf_cpu, {**ev, "proposed_request_millicores": millicores(new_req)}))
        else:
            ov = cpu["over_request"]
            if c.req_cpu >= ov["ratio"] * max(c.vpa_cpu.target or 0.0, c.cpu_worst):
                if not ok_cpu:
                    why.append(why_cpu)
                else:
                    new_req = max(ceil10m(max(c.vpa_cpu.target, c.cpu_p95) * ov["margin"]), ceil10m(c.cpu_worst), ov["floor_millicores"] / 1000)
                    if (c.req_cpu - new_req) * 1000 >= ov["min_free_millicores"]:
                        out.append(_finding(c, "cpu-request", "cpu-over-request", now, conf_cpu, {
                            **ev, "proposed_request_millicores": millicores(new_req), "freed_millicores": round((c.req_cpu - new_req) * 1000, 1)}))
    return out, sorted(set(why))


def finding_value(e: dict) -> float | None:
    """The one number a suggestion is about, to tell whether it moved since an operator called it noise (10i3)."""
    for k in ("proposed_request_mib", "proposed_limit_mib", "proposed_request_millicores", "rise_mib"):
        if isinstance(e.get(k), (int, float)):
            return float(e[k])
    return None


def freed_bytes(f: dict) -> int:
    """Memory a finding frees (negative for an under-request that adds): the ranking key for the digest."""
    e = f["evidence"]
    return int(e.get("freed_bytes") or -(e.get("added_bytes") or 0))


# ---- reading VictoriaMetrics ---------------------------------------------------------------------------------------------------------------


class VM:
    """The two read endpoints the metrics-read route serves. `fetch(url) -> text` does the GET (urllib in the job, the tool's own client in the API)."""

    def __init__(self, base_url: str, fetch):
        self.base, self.fetch = base_url.rstrip("/"), fetch

    def _get(self, path: str, params: dict) -> list:
        data = json.loads(self.fetch(f"{self.base}{path}?" + urllib.parse.urlencode(params)))
        if data.get("status") != "success":
            raise ValueError("query did not succeed: " + str(data.get("error", ""))[:120])
        return data["data"].get("result", []) or []

    def instant(self, promql: str) -> list:
        """[(labels, float)] for an instant vector; non-numeric samples are dropped."""
        out = []
        for s in self._get("/api/v1/query", {"query": promql}):
            try:
                out.append((s["metric"], float(s["value"][1])))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        return out

    def range(self, promql: str, start: float, end: float, step: str) -> list:
        out = []
        for s in self._get("/api/v1/query_range", {"query": promql, "start": int(start), "end": int(end), "step": step}):
            pts = []
            for ts, v in s.get("values", []) or []:
                try:
                    pts.append((float(ts), float(v)))
                except (TypeError, ValueError):
                    continue
            out.append((s["metric"], pts))
        return out


@dataclass
class Fleet:
    containers: dict = field(default_factory=dict)      # key -> Container
    covered: set = field(default_factory=set)           # controllers that have a VPA (namespace/Kind/name)
    controllers: set = field(default_factory=set)       # every controller seen with pods
    nodes: list = field(default_factory=list)
    errors: list = field(default_factory=list)          # queries that failed: one failing never hides the rest, and never stays silent


_SEL = '{container!="",container!="POD"}'


def _ns_sel(ns: str | None, extra: str = "") -> str:
    parts = ([f'namespace="{ns}"'] if ns else []) + ([extra] if extra else [])
    return "{" + ",".join(parts) + "}" if parts else ""


def pod_controllers(q, namespace: str | None, window: str) -> dict:
    """(namespace, pod) -> (namespace, Kind, name) over the window, so a pod that a rollout replaced still maps to its Deployment.
    `q(name, promql)` is the tolerant instant query of the caller (a failing query returns [])."""
    owner_sel = _ns_sel(namespace, "owner_is_controller='true'")
    rs_sel = _ns_sel(namespace, "owner_kind='Deployment'")
    rs_owner = {(m.get("namespace"), m.get("replicaset")): m.get("owner_name") for m, _ in q(
        "replicaset-owner", f"max by (namespace,replicaset,owner_name)(max_over_time(kube_replicaset_owner{rs_sel}[{window}]))")}
    pod_ctl: dict = {}
    for m, _ in q("pod-owner", f"max by (namespace,pod,owner_kind,owner_name)(max_over_time(kube_pod_owner{owner_sel}[{window}]))"):
        k, n, ns = m.get("owner_kind"), m.get("owner_name"), m.get("namespace")
        if k == "ReplicaSet":
            d = rs_owner.get((ns, n))
            if d:
                pod_ctl[(ns, m["pod"])] = (ns, "Deployment", d)
        elif k in ("StatefulSet", "DaemonSet"):
            pod_ctl[(ns, m["pod"])] = (ns, k, n)
    return pod_ctl


def collect(vm: VM, cfg: dict, now: float, namespace: str | None = None) -> Fleet:
    """Run the queries and fold them into Container facts. `namespace` narrows every query (the tool's detail view).

    Usage, OOM and the owner mapping are read over the whole window, so the history of pods that were replaced by a rollout still counts;
    requests, limits and pod age come from the pods that run now (their template is the current one)."""
    w = f"{int(cfg['window_days'])}d"
    f = Fleet()

    def q(name: str, promql: str):
        try:
            return vm.instant(promql)
        except Exception as e:  # noqa: BLE001 - one failing query must not hide the others
            f.errors.append(f"{name}: {type(e).__name__}: {str(e)[:100]}")
            return []

    sel = "{" + (f'namespace="{namespace}",' if namespace else "") + 'container!="",container!="POD"}'
    pod_ctl = pod_controllers(q, namespace, w)
    age = {(m.get("namespace"), m.get("pod")): v for m, v in q("pod-age", f"max by (namespace,pod)(time() - kube_pod_start_time{_ns_sel(namespace)})")}

    # requests / limits per pod+container (the youngest pod's are the current template's)
    spec: dict = {}
    res_sel = _ns_sel(namespace, "resource=~'cpu|memory'")
    for kind, metric in (("req", "kube_pod_container_resource_requests"), ("lim", "kube_pod_container_resource_limits")):
        for m, v in q(metric, f"max by (namespace,pod,container,resource)({metric}{res_sel})"):
            spec[(m.get("namespace"), m.get("pod"), m.get("container"), kind, m.get("resource"))] = v

    def per_pod(name: str, promql: str) -> dict:
        return {(m.get("namespace"), m.get("pod"), m.get("container")): v for m, v in q(name, promql)}

    mem_max = per_pod("mem-max", f"max by (namespace,pod,container)(max_over_time(container_memory_working_set_bytes{sel}[{w}]))")
    mem_p50 = per_pod("mem-p50", f"max by (namespace,pod,container)(quantile_over_time(0.5, container_memory_working_set_bytes{sel}[{w}]))")
    mem_p95 = per_pod("mem-p95", f"max by (namespace,pod,container)(quantile_over_time(0.95, container_memory_working_set_bytes{sel}[{w}]))")
    # CPU basis = the median of the daily p95 (and the worst day's p95 as a floor), as 10i0b did by hand: short-lived pods (rollouts, restarts)
    # inflate a raw 30-day p99, and a request should follow what a normal day needs, not what a startup burst needed once.
    daily = f"quantile_over_time(0.95, rate(container_cpu_usage_seconds_total{sel}[5m])[1d:5m])"
    cpu_p95 = per_pod("cpu-p95", f"max by (namespace,pod,container)(quantile_over_time(0.5, ({daily})[{w}:1d]))")
    cpu_worst = per_pod("cpu-worst", f"max by (namespace,pod,container)(max_over_time(({daily})[{w}:1d]))")
    oom_sel = _ns_sel(namespace, "reason='OOMKilled'")
    oom = per_pod("oomkilled", f"max by (namespace,pod,container)(max_over_time(kube_pod_container_status_last_terminated_reason{oom_sel}[{w}]))")
    restarts = per_pod("restarts", f"sum by (namespace,pod,container)(increase(kube_pod_container_status_restarts_total{_ns_sel(namespace)}[{w}]))")

    seen: dict = {}  # (ns, kind, name, container) -> Container
    for (ns, pod, ctr), v in mem_max.items():
        ctl = pod_ctl.get((ns, pod))
        if ctl is None:
            continue
        c = seen.setdefault((*ctl, ctr), Container(ns, ctl[1], ctl[2], ctr))
        c.mem_max = max(c.mem_max or 0.0, v)
        for attr, src in (("mem_p50", mem_p50), ("mem_p95", mem_p95), ("cpu_p95", cpu_p95), ("cpu_worst", cpu_worst)):
            x = src.get((ns, pod, ctr))
            if x is not None:
                setattr(c, attr, max(getattr(c, attr) or 0.0, x))
        c.oom = c.oom or bool(oom.get((ns, pod, ctr)))
        c.restarts += restarts.get((ns, pod, ctr), 0.0)
        a = age.get((ns, pod))
        if a is None:
            continue  # a pod that is gone: its usage counts, its template does not
        c.pods += 1
        if c.youngest_pod_age_s is None or a < c.youngest_pod_age_s:
            c.youngest_pod_age_s = a
            for attr, kind, res in (("req_cpu", "req", "cpu"), ("req_mem", "req", "memory"), ("lim_cpu", "lim", "cpu"), ("lim_mem", "lim", "memory")):
                setattr(c, attr, spec.get((ns, pod, ctr, kind, res)))
    f.controllers = {c.controller for c in seen.values() if c.pods}

    # VPA, aggregated over the duplicate series kube-state-metrics' labels can produce
    by = "namespace,target_kind,target_name,container,resource"
    for stat, attr in (("lowerbound", "lower"), ("target", "target"), ("upperbound", "upper")):
        metric = f"kube_customresource_vpa_containerrecommendations_{stat}"
        for m, v in q(f"vpa-{stat}", f"max by ({by})({metric}{_ns_sel(namespace)})"):
            f.covered.add(f"{m.get('namespace')}/{m.get('target_kind')}/{m.get('target_name')}")
            c = seen.get((m.get("namespace"), m.get("target_kind"), m.get("target_name"), m.get("container")))
            if c is not None:
                setattr(c.vpa_mem if m.get("resource") == "memory" else c.vpa_cpu, attr, v)
    tgt = f"kube_customresource_vpa_containerrecommendations_target{_ns_sel(namespace)}"
    for attr, promql in (("age_s", f"max by ({by})(lifetime({tgt}[{w}]))"), ("hi7", f"max by ({by})(max_over_time({tgt}[7d]))"),
                         ("lo7", f"min by ({by})(min_over_time({tgt}[7d]))")):
        for m, v in q("vpa-" + attr, promql):
            c = seen.get((m.get("namespace"), m.get("target_kind"), m.get("target_name"), m.get("container")))
            if c is not None:
                setattr(c.vpa_mem if m.get("resource") == "memory" else c.vpa_cpu, attr, v)

    # the daily peak per container, for the creep detector (a range query: 1 day buckets, max across the controller's pods)
    try:
        for m, pts in vm.range(f"max by (namespace,pod,container)(max_over_time(container_memory_working_set_bytes{sel}[1d]))",
                               now - cfg["window_days"] * DAY, now, "1d"):
            ctl = pod_ctl.get((m.get("namespace"), m.get("pod")))
            c = seen.get((*ctl, m.get("container"))) if ctl else None
            if c is not None:
                days = dict(c.daily_mem_max)
                for ts, v in pts:
                    days[ts] = max(days.get(ts, 0.0), v)
                c.daily_mem_max = sorted(days.items())
    except Exception as e:  # noqa: BLE001
        f.errors.append(f"daily-peak: {type(e).__name__}: {str(e)[:100]}")

    f.containers = {c.key: c for c in seen.values() if c.pods}   # a container that no longer runs is history, not a finding
    f.nodes = _nodes(q, cfg)
    return f


def _nodes(q, cfg: dict) -> list:
    """Per worker: memory and CPU allocatable / requested, memory limits and memory used (working set of its pods)."""
    pat = re.compile(cfg["worker_node_pattern"])
    rows: dict = {}

    def put(promql: str, field_: str, name: str, scale: float = 1.0):
        for m, v in q(name, promql):
            n = m.get("node")
            if n and pat.search(n):
                rows.setdefault(n, {"node": n})[field_] = v * scale

    put('sum by (node)(kube_node_status_allocatable{resource="memory"})', "allocatable_mem", "node-alloc-mem")
    put('sum by (node)(kube_node_status_allocatable{resource="cpu"})', "allocatable_cpu", "node-alloc-cpu")
    put('sum by (node)(kube_pod_container_resource_requests{resource="memory"})', "requested_mem", "node-req-mem")
    put('sum by (node)(kube_pod_container_resource_requests{resource="cpu"})', "requested_cpu", "node-req-cpu")
    put('sum by (node)(kube_pod_container_resource_limits{resource="memory"})', "limits_mem", "node-lim-mem")
    put('sum by (node)(sum by (namespace,pod)(container_memory_working_set_bytes' + _SEL + ') * on (namespace,pod) group_left(node) max by (namespace,pod,node)(kube_pod_info))',
        "used_mem", "node-used-mem")
    out = []
    for n in sorted(rows):
        r = rows[n]
        a = r.get("allocatable_mem") or 0
        out.append({"node": n, "memory_allocatable_mib": mib(r.get("allocatable_mem")), "memory_requested_mib": mib(r.get("requested_mem")),
                    "memory_requested_pct": round(100 * r.get("requested_mem", 0) / a, 1) if a else None,
                    "memory_limits_mib": mib(r.get("limits_mem")), "memory_used_mib": mib(r.get("used_mem")),
                    "cpu_allocatable_millicores": millicores(r.get("allocatable_cpu")), "cpu_requested_millicores": millicores(r.get("requested_cpu")),
                    "cpu_requested_pct": round(100 * r.get("requested_cpu", 0) / r["allocatable_cpu"], 1) if r.get("allocatable_cpu") else None})
    return out


# ---- answers ------------------------------------------------------------------------------------------------------------------------------


def analyse(fleet: Fleet, cfg: dict, now: float) -> tuple[list, dict]:
    """(findings for every covered container, {reason: count} of suppressed ones)."""
    found: list = []
    suppressed: dict = {}
    for c in sorted(fleet.containers.values(), key=lambda c: c.key):
        if c.controller not in fleet.covered:
            continue
        fs, why = evaluate(c, cfg, now)
        found += fs
        for r in why:
            suppressed[r] = suppressed.get(r, 0) + 1
    return found, suppressed


def uncovered(fleet: Fleet, cfg: dict) -> list:
    skip = set(cfg["coverage_ignore_namespaces"])
    return sorted(c for c in fleet.controllers if c not in fleet.covered and c.split("/")[0] not in skip)


def _compact(f: dict) -> dict:
    e = f["evidence"]
    return {"target": f["target"], "finding": e["finding"], "confidence": f["confidence"], "freed_mib": mib(freed_bytes(f)), "evidence": e}


def summary(fleet: Fleet, cfg: dict, now: float, top: int = 15) -> dict:
    found, suppressed = analyse(fleet, cfg, now)
    ranked = sorted(found, key=freed_bytes, reverse=True)
    ages = [c.vpa_mem.age_s for c in fleet.containers.values() if c.vpa_mem.age_s]
    return {"as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "window_days": cfg["window_days"],
            "workers": fleet.nodes, "controllers": len(fleet.controllers), "controllers_with_vpa": len(fleet.covered & fleet.controllers),
            "controllers_without_vpa": uncovered(fleet, cfg), "oldest_vpa_sample_days": round(max(ages) / DAY, 1) if ages else None,
            "vpa_min_sample_age_days": cfg["vpa"]["min_sample_age_days"], "findings_total": len(found),
            "findings": [_compact(f) for f in ranked[:top]], "findings_truncated": len(ranked) > top, "suppressed": suppressed, "query_errors": fleet.errors}


def _container_view(c: Container, cfg: dict, now: float) -> dict:
    fs, why = evaluate(c, cfg, now)
    return {"container": c.container, "pods": c.pods, "youngest_pod_age_h": round(c.youngest_pod_age_s / 3600, 1) if c.youngest_pod_age_s is not None else None,
            "requests": {"cpu_millicores": millicores(c.req_cpu), "memory_mib": mib(c.req_mem)},
            "limits": {"cpu_millicores": millicores(c.lim_cpu), "memory_mib": mib(c.lim_mem)},
            "vpa": {"memory": {"lower_mib": mib(c.vpa_mem.lower), "target_mib": mib(c.vpa_mem.target), "upper_mib": mib(c.vpa_mem.upper),
                               "sample_age_days": round(c.vpa_mem.age_s / DAY, 1) if c.vpa_mem.age_s else None, "valid": vpa_valid(c.vpa_mem, cfg)[0],
                               "target_7d_min_mib": mib(c.vpa_mem.lo7), "target_7d_max_mib": mib(c.vpa_mem.hi7)},
                    "cpu": {"lower_millicores": millicores(c.vpa_cpu.lower), "target_millicores": millicores(c.vpa_cpu.target), "upper_millicores": millicores(c.vpa_cpu.upper),
                            "valid": vpa_valid(c.vpa_cpu, cfg)[0]}},
            "usage_30d": {"memory_p50_mib": mib(c.mem_p50), "memory_p95_mib": mib(c.mem_p95), "memory_max_mib": mib(c.mem_max),
                          "cpu_median_daily_p95_millicores": millicores(c.cpu_p95), "cpu_worst_day_p95_millicores": millicores(c.cpu_worst)},
            "oomkilled": c.oom, "restarts_30d": round(c.restarts), "findings": [_compact(f) for f in fs], "suppressed": why}


def detail(fleet: Fleet, cfg: dict, now: float, namespace: str, kind: str | None = None, name: str | None = None) -> dict:
    """One controller (kind + name), or every controller in a namespace."""
    want = KINDS[kind] if kind else None
    cs = [c for c in fleet.containers.values() if c.namespace == namespace and (want is None or c.kind == want) and (name is None or c.name == name)]
    if not cs:
        return {"found": False, "note": "no pods of that controller in the metrics window (check the name, or that it runs)"}
    groups: dict = {}
    for c in sorted(cs, key=lambda c: c.key):
        groups.setdefault(c.controller, []).append(_container_view(c, cfg, now))
    return {"found": True, "window_days": cfg["window_days"], "query_errors": fleet.errors,
            "controllers": [{"controller": k, "has_vpa": k in fleet.covered, "containers": v} for k, v in groups.items()]}


def snapshot(fleet: Fleet, cfg: dict, now: float, suppressed: dict) -> dict:
    """What the 10i3 digest needs besides the findings: the per-worker scoreboard and the coverage numbers, as of this pass."""
    ages = [c.vpa_mem.age_s for c in fleet.containers.values() if c.vpa_mem.age_s]
    return {"as_of": now, "workers": fleet.nodes, "controllers": len(fleet.controllers), "controllers_with_vpa": len(fleet.covered & fleet.controllers),
            "controllers_without_vpa": uncovered(fleet, cfg), "oldest_vpa_sample_days": round(max(ages) / DAY, 1) if ages else None,
            "vpa_min_sample_age_days": cfg["vpa"]["min_sample_age_days"], "suppressed": suppressed}


def findings(vm: VM, cfg: dict, now: float, with_snapshot: bool = False):
    """The daily pass: every finding that holds now plus a stats dict (for the job's log line); with `with_snapshot` also the digest's
    snapshot as a third value. Findings are not posted anywhere by this code."""
    fleet = collect(vm, cfg, now)
    found, suppressed = analyse(fleet, cfg, now)
    stats = {"containers": len(fleet.containers), "controllers_with_vpa": len(fleet.covered & fleet.controllers), "findings": len(found),
             "without_vpa": len(uncovered(fleet, cfg)), "errors": len(fleet.errors), **{f"suppressed_{k}": v for k, v in sorted(suppressed.items())}}
    if fleet.errors:
        stats["error_detail"] = "; ".join(fleet.errors)[:300]
    return (found, stats, snapshot(fleet, cfg, now, suppressed)) if with_snapshot else (found, stats)
