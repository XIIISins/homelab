"""The change request behind a digest's "Draft PR" button (Phase 10i4, class `rightsizing`).

One request per WORKLOAD: every open finding of that workload becomes one old -> new table, so the PR is one coherent resources change. The
Toolbelt writes the whole request (the drafting session only finds the file and makes the edit):

  instructions   what to change and, as important, what not to; the class's path allow-list and the resources-only check enforce it
  evidence       the old -> new table with the numbers behind each row, and the per-worker memory requested before and after; the dispatcher
                 copies this block verbatim into the PR description (`<!-- evidence -->`)
  spec           the machine-readable block the 10i5 post-merge watch reads (`<!-- rightsizing-spec ... -->`)

Nothing here applies anything: the PR waits for a human to approve the request and again to merge it.
"""
from __future__ import annotations

import rightsizing
import rightsizing_digest
import rightsizing_watch

MIB = rightsizing.MIB
EVIDENCE_OPEN, EVIDENCE_CLOSE = "<!-- evidence -->", "<!-- /evidence -->"
BODY_BUDGET = 3900   # change_requests allows 4000 characters


def container_of(target: str) -> str:
    return target.split("/", 3)[3] if target.count("/") >= 3 else ""


def collect(rows: list, controller: str, held: bool) -> dict:
    """{container: {"old": {...}, "new": {...}, "why": {...}}} from the open findings of one workload. A finding with no number is skipped; a limit
    cut only counts when the workload's request cut held (the same gate as the digest)."""
    out: dict = {}
    for r in rows:
        if rightsizing_digest.controller_of(r["target"]) != controller:
            continue
        e = (r.get("evidence") or {}).get("evidence") or {}
        f = e.get("finding")
        if f == "memory-creep" or rightsizing_digest.proposal(e) is None:
            continue
        if f == "memory-over-limit" and not held:
            continue
        c = out.setdefault(container_of(r["target"]), {"old": {}, "new": {}, "why": {}})
        if f in ("memory-under-request", "memory-over-request"):
            if e.get("proposed_request_mib") is not None and e["proposed_request_mib"] != e.get("request_mib"):
                c["old"]["request_mib"], c["new"]["request_mib"] = e["request_mib"], e["proposed_request_mib"]
            if e.get("proposed_limit_mib") is not None and e.get("limit_mib"):
                c["old"]["limit_mib"], c["new"]["limit_mib"] = e["limit_mib"], e["proposed_limit_mib"]
        elif f == "memory-over-limit":
            c["old"]["limit_mib"], c["new"]["limit_mib"] = e["limit_mib"], e["proposed_limit_mib"]
        elif f in ("cpu-under-request", "cpu-over-request"):
            c["old"]["request_millicores"], c["new"]["request_millicores"] = e["request_millicores"], e["proposed_request_millicores"]
        why = c["why"]
        for k in ("max_working_set_mib", "vpa_upper_mib", "median_daily_p95_millicores", "worst_day_p95_millicores"):
            if e.get(k) is not None:
                why[k] = e[k]
        if e.get("oomkilled"):
            why["oomkilled"] = True
        why.setdefault("findings", []).append(f)
    return {k: v for k, v in out.items() if v["new"]}


def placement(vm, controller: str, window: str = "2h") -> dict:
    """{node: number of the workload's pods running there} (best effort; {} when VictoriaMetrics cannot say)."""
    ns, kind, name = controller.split("/")
    try:
        q = lambda label, promql: vm.instant(promql)   # noqa: E731
        owners = rightsizing.pod_controllers(q, ns, window)
        pods = {p for (n, p), ctl in owners.items() if ctl == (ns, kind, name)}
        nodes: dict = {}
        for m, _ in vm.instant(f'max by (pod,node)(kube_pod_info{{namespace="{ns}"}})'):
            if m.get("pod") in pods and m.get("node"):
                nodes[m["node"]] = nodes.get(m["node"], 0) + 1
        return nodes
    except Exception:   # noqa: BLE001 - the totals are a nicety; the request is fine without them
        return {}


def totals(workers: list, nodes: dict, containers: dict) -> tuple[list, float]:
    """Per worker memory requested before -> after, and the cluster-wide MiB the change frees (negative when it adds: a request raised to fix an OOM).
    Each pod of the workload gives back the NET request change of its containers, so a raise on one container and a cut on another offset."""
    cut = sum(c["old"]["request_mib"] - c["new"]["request_mib"] for c in containers.values() if "request_mib" in c["old"] and "request_mib" in c["new"])   # per pod, signed
    freed = cut * (sum(nodes.values()) or 1)
    lines = []
    for w in workers:
        n = nodes.get(w["node"], 0)
        if n and w.get("memory_requested_mib") is not None and w.get("memory_allocatable_mib"):
            before = w["memory_requested_mib"]
            after = before - cut * n
            lines.append(f"{w['node'].removeprefix('einherjar-')} {before:.0f} → {after:.0f} MiB ({100 * before / w['memory_allocatable_mib']:.0f} % → {100 * after / w['memory_allocatable_mib']:.0f} %)")
    return lines, freed


def _cell(c: dict, key: str, unit: str) -> str:
    if key not in c["new"]:
        return "-"
    return f"{c['old'].get(key, '?'):g} → {c['new'][key]:g}{unit}"


def evidence_md(containers: dict, lines: list, with_why: bool = True) -> str:
    rows = ["| container | memory request | memory limit | CPU request |" + (" evidence |" if with_why else ""), "|---|---|---|---|" + ("---|" if with_why else "")]
    for name, c in sorted(containers.items()):
        why = ""
        if with_why:
            w = c["why"]
            bits = ([f"30-day peak {w['max_working_set_mib']:g} MiB"] if "max_working_set_mib" in w else []) + ([f"VPA upper {w['vpa_upper_mib']:g} MiB"] if "vpa_upper_mib" in w else []) \
                + ([f"CPU normal day {w['median_daily_p95_millicores']:g}m"] if "median_daily_p95_millicores" in w else []) + (["OOMKilled"] if w.get("oomkilled") else [])
            why = " " + "; ".join(bits) + " |"
        rows.append(f"| `{name}` | {_cell(c, 'request_mib', ' MiB')} | {_cell(c, 'limit_mib', ' MiB')} | {_cell(c, 'request_millicores', 'm')} |" + why)
    text = "\n".join(rows)
    if lines:
        text += "\n\nWorker memory requested before → after: " + "; ".join(lines) + "."
    return text


def build(controller: str, containers: dict, workers: list, nodes: dict) -> tuple[str, str, dict]:
    """(title, body, spec) of the change request for one workload; `containers` is `collect()`'s result (non-empty)."""
    ns, kind, name = controller.split("/")
    lines, freed = totals(workers, nodes, containers)
    spec = {"v": 1, "controller": controller, "kind": "trim", "freed_mib": round(freed, 1),
            "containers": {n: {"old": c["old"], "new": c["new"]} for n, c in containers.items()}}
    rightsizing_watch.validate_spec(spec)
    instructions = (
        f"Change the resources of {kind} `{name}` in namespace `{ns}` and nothing else. Find where it is declared: a HelmRelease (k8s/asgard/apps/<app>/helmrelease.yaml, or "
        "k8s/asgard/infrastructure/authentik/helmrelease.yaml) or a plain Deployment/StatefulSet manifest next to it. Set exactly these values:\n"
        + rightsizing_watch.describe_spec(spec) + "\n\n"
        "Rules: requests and limits only (cpu and memory, quantities such as 640Mi or 80m); never an image, tag, replica count, chart version or any other key; "
        "never a CPU limit; a memory limit never below its request. If the chart takes resources through a `resourcesPreset`, set that preset to \"none\" and add the "
        "explicit `resources:` block beside it with the full values (keep every value that is not listed unchanged). A CI check refuses anything beyond resources. "
        "In the summary, say which file you changed and list old -> new for each value. If you cannot map a listed container to a resources block cleanly, change nothing and say why.")
    for with_why in (True, False):
        ev = evidence_md(containers, lines, with_why)
        body = f"{instructions}\n\n{EVIDENCE_OPEN}\n{ev}\n{EVIDENCE_CLOSE}\n\n{rightsizing_watch.spec_block(spec)}"
        if len(body) <= BODY_BUDGET:
            break
    return f"Rightsize {name}"[:120], body, spec
