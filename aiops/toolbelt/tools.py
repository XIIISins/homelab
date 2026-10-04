"""Read-only tools for the diagnosis agent (Phase 10d2): the allow-list, argument contract and replay.

This module IS the allow-list. A tool exists only if it has an entry in SPEC; anything
else is refused by the API before any handler runs, so a write-shaped or unknown call
from a misbehaving agent fails here, not by the model's good behaviour. Every argument is
typed, bounded and (where free-form) pattern-checked. There is no generic exec, no shell,
no SSH: handlers build fixed argv lists or call a client library.

Live handlers exist only for tools that need no credential (registry, git, reach); the
rest (logs, metrics, kube, netbox, semaphore) are contract-only until their read-only
identity is minted, and answer 501 live. They work in REPLAY mode regardless, which is how the
acceptance incidents become repeatable (you cannot re-freeze Skuld): a scenario is one readable
file, aiops/replays/<scenario>/scenario.json, holding the triggering event, what a correct
diagnosis must look like, and the recorded tool calls. An incident ingested with
`X-AIOPS-Replay: <scenario>` answers every tool call from those recordings (matched on tool +
exact arguments) and never touches the network; anything unrecorded is NO_RECORDING.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPLAY_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")


class ToolError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Arg:
    kind: str                       # str | int | enum
    required: bool = False
    max_len: int = 200
    pattern: str | None = None
    lo: int = 0
    hi: int = 0
    choices: tuple = ()


def S(required=False, max_len=200, pattern=None):
    return Arg("str", required, max_len=max_len, pattern=pattern)


def I(lo, hi, required=False):  # noqa: E743
    return Arg("int", required, lo=lo, hi=hi)


def E(*choices, required=False):
    return Arg("enum", required, choices=tuple(choices))


HOST = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$"
K8S_NAME = r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$"
TIME = r"^[0-9A-Za-z:.+_-]{1,40}$"  # RFC3339, unix seconds, or a relative form like 15m

SPEC: dict[str, dict[str, Arg]] = {
    "registry.runbooks": {},
    "registry.runbook": {"id": S(True, 60, r"^RB-[A-Z0-9-]+$")},
    "registry.actions": {},
    "git.log": {"path": S(False, 200, r"^[A-Za-z0-9._/-]+$"), "max": I(1, 20)},
    "git.show": {"rev": S(True, 40, r"^[0-9a-f]{7,40}$"), "path": S(False, 200, r"^[A-Za-z0-9._/-]+$")},
    "reach.tcp": {"host": S(True, 100, HOST), "port": I(1, 65535, True)},
    "logs.query": {"query": S(True, 500), "start": S(False, 40, TIME), "end": S(False, 40, TIME), "limit": I(1, 200)},
    "metrics.query": {"query": S(True, 500), "time": S(False, 40, TIME)},
    "metrics.range": {"query": S(True, 500), "start": S(True, 40, TIME), "end": S(False, 40, TIME), "step": S(False, 12, r"^[0-9]{1,5}[smh]$")},
    "zabbix.problems": {"host": S(False, 100, HOST)},
    "zabbix.host": {"host": S(True, 100, HOST)},
    "zabbix.triggers": {"host": S(True, 100, HOST)},
    "kube.get": {"kind": E("pods", "nodes", "deployments", "statefulsets", "daemonsets", "services", "events", "helmreleases",
                           "kustomizations", "persistentvolumeclaims", "endpoints", required=True),
                 "namespace": S(False, 63, K8S_NAME), "name": S(False, 253, K8S_NAME)},
    "kube.logs": {"namespace": S(True, 63, K8S_NAME), "pod": S(True, 253, K8S_NAME), "container": S(False, 63, K8S_NAME),
                  "tail": I(1, 200), "previous": E("true", "false")},
    "netbox.host": {"name": S(True, 100, HOST)},
    "netbox.hypervisor_peers": {"host": S(True, 100, HOST)},
    "pve.node_status": {"node": S(True, 40, r"^[a-z0-9-]+$")},
    "pve.guests": {"node": S(False, 40, r"^[a-z0-9-]+$")},
    "semaphore.tasks": {"limit": I(1, 20), "task_id": I(1, 10_000_000)},
}


def validate(name: str, args: object) -> dict:
    spec = SPEC.get(name)
    if spec is None:
        raise ToolError(404, f"unknown tool {name!r}")
    if not isinstance(args, dict):
        raise ToolError(400, "args must be an object")
    extra = set(args) - set(spec)
    if extra:
        raise ToolError(400, f"unexpected argument(s): {', '.join(sorted(extra))}")
    out = {}
    for key, a in spec.items():
        if key not in args:
            if a.required:
                raise ToolError(400, f"missing argument {key!r}")
            continue
        v = args[key]
        if a.kind == "str":
            if not isinstance(v, str) or not v or len(v) > a.max_len:
                raise ToolError(400, f"{key} must be a non-empty string up to {a.max_len} chars")
            if a.pattern and not re.match(a.pattern, v):
                raise ToolError(400, f"{key} has a disallowed format")
        elif a.kind == "int":
            if isinstance(v, bool) or not isinstance(v, int) or not (a.lo <= v <= a.hi):
                raise ToolError(400, f"{key} must be an integer in [{a.lo}, {a.hi}]")
        elif a.kind == "enum":
            if v not in a.choices:
                raise ToolError(400, f"{key} must be one of {list(a.choices)}")
        out[key] = v
    return out


def args_hash(name: str, args: dict) -> str:
    return hashlib.sha256((name + "\n" + json.dumps(args, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()[:16]


# ---- replay -------------------------------------------------------------------------------------
def scenario_file(replay_dir: Path, scenario: str) -> Path:
    if not REPLAY_NAME.match(scenario or ""):
        raise ToolError(400, "bad replay scenario name")
    return replay_dir / scenario / "scenario.json"


def replay(replay_dir: Path, scenario: str, name: str, args: dict) -> dict:
    f = scenario_file(replay_dir, scenario)
    if not f.is_file():
        raise ToolError(404, "NO_RECORDING")
    for call in json.loads(f.read_text()).get("calls", []):
        if call.get("tool") == name and call.get("args") == args:
            return call["response"]
    raise ToolError(404, "NO_RECORDING")


# ---- live handlers (credential-free tools only) ---------------------------------------------------
@dataclass
class LiveConfig:
    root: Path                      # the deployed aiops/ parent (runbooks.yml, actions.yml)
    repo_dir: Path | None = None    # read-only clone for git.*
    reach_ports: tuple = (22, 53, 80, 443, 3000, 5432, 6443, 8006, 8200, 8428, 9428, 10050)
    reach_nets: tuple = ("10.0.0.0/8",)
    creds_dir: Path | None = None   # <name>.json per read-only credential, written by the root loader
    # Any cluster member answers for the whole cluster; try them in turn so a dead node is still diagnosable.
    pve_urls: tuple = ("https://10.0.254.11:8006", "https://10.0.254.12:8006", "https://10.0.254.13:8006")
    pve_timeout: float = 5.0
    zabbix_url: str = "http://10.0.11.21/api_jsonrpc.php"  # the credential file's `url` wins when present
    zabbix_timeout: float = 10.0
    netbox_url: str = "https://netbox.niflheim.xiiisins.com"  # the credential file's `url` wins when present
    netbox_timeout: float = 10.0
    kube_timeout: float = 15.0
    semaphore_project: int = 1
    semaphore_timeout: float = 15.0
    # Frigg-only read routes (k8s/asgard/apps/victoria*/httproute-aiops-read.yaml): no credential, the source-IP allow-list is the control
    logs_url: str = "https://logs-read.niflheim.xiiisins.com"
    metrics_url: str = "https://metrics-read.niflheim.xiiisins.com"
    obs_timeout: float = 20.0
    logs_max_chars: int = 20000
    metrics_max_series: int = 50
    metrics_max_points: int = 120
    kube_log_max_chars: int = 20000


def _yaml(path: Path):
    import yaml

    return yaml.safe_load(path.read_text())


def _registry(cfg: LiveConfig, name: str, args: dict) -> dict:
    if name == "registry.runbooks":
        rb = _yaml(cfg.root / "aiops" / "runbooks.yml")["runbooks"]
        return {"runbooks": [{"id": r["id"], "title": r.get("title", "")} for r in rb]}
    if name == "registry.runbook":
        for r in _yaml(cfg.root / "aiops" / "runbooks.yml")["runbooks"]:
            if r["id"] == args["id"]:
                return {"runbook": r}
        raise ToolError(404, "no such runbook")
    acts = _yaml(cfg.root / "aiops" / "actions.yml")
    return {"actions": acts.get("actions", acts)}


def _git(cfg: LiveConfig, name: str, args: dict) -> dict:
    if cfg.repo_dir is None or not (cfg.repo_dir / ".git").exists():
        raise ToolError(501, "git clone not configured")
    path = args.get("path")
    if path is not None and (path.startswith("/") or ".." in path.split("/")):
        raise ToolError(400, "path must be relative and stay inside the repo")
    base = ["git", "-C", str(cfg.repo_dir), "--no-pager"]
    if name == "git.log":
        cmd = base + ["log", "--no-color", f"-n{args.get('max', 10)}", "--format=%h %ad %an %s", "--date=short"]
        cmd += ["--", path] if path else []
    else:
        cmd = base + ["show", "--no-color", "--stat", "--format=%h %ad %an%n%B", "--date=short", args["rev"]]
        cmd += ["--", path] if path else []
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    if r.returncode != 0:
        raise ToolError(404, "git: not found")
    return {"output": r.stdout[:20000]}


def _reach(cfg: LiveConfig, args: dict) -> dict:
    port = args["port"]
    if port not in cfg.reach_ports:
        raise ToolError(400, f"port {port} is not probe-able (allowed: {list(cfg.reach_ports)})")
    nets = [ipaddress.ip_network(n) for n in cfg.reach_nets]
    try:
        infos = socket.getaddrinfo(args["host"], port, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return {"open": False, "error": "dns-failed"}
    addrs = {i[4][0] for i in infos}
    if not all(any(ipaddress.ip_address(a) in n for n in nets) for a in addrs):
        raise ToolError(400, "host resolves outside the homelab ranges")
    t0 = time.monotonic()
    try:
        with socket.create_connection((sorted(addrs)[0], port), timeout=3):
            return {"open": True, "ms": int((time.monotonic() - t0) * 1000), "address": sorted(addrs)[0]}
    except OSError as e:
        return {"open": False, "error": type(e).__name__, "ms": int((time.monotonic() - t0) * 1000)}


def _cred(cfg: LiveConfig, name: str) -> dict:
    if cfg.creds_dir is None or not (cfg.creds_dir / f"{name}.json").is_file():
        raise ToolError(501, f"credential {name!r} is not available (not minted yet, or the loader could not read it)")
    return json.loads((cfg.creds_dir / f"{name}.json").read_text())


_UNVERIFIED = ssl.create_default_context()
_UNVERIFIED.check_hostname = False   # PVE serves a self-signed cert on an internal VLAN; the token is
_UNVERIFIED.verify_mode = ssl.CERT_NONE  # PVEAuditor-only and the path is the management network


def _pve_get(cfg: LiveConfig, path: str) -> dict:
    """GET one PVE API path (read-only by construction: GET only) from the first node that answers."""
    c = _cred(cfg, "pve")
    header = f"PVEAPIToken={c['token_id']}={c['secret']}"
    last = "no node answered"
    for base in cfg.pve_urls:
        req = urllib.request.Request(f"{base}/api2/json{path}", headers={"Authorization": header}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=cfg.pve_timeout, context=_UNVERIFIED) as r:
                return json.load(r)["data"]
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ToolError(502, f"pve refused the read-only token ({e.code})")
            last = f"HTTP {e.code} from {base}"
        except (OSError, ValueError) as e:  # connection refused / timeout / bad JSON: try the next node
            last = f"{type(e).__name__} from {base}"
    raise ToolError(502, f"pve unreachable: {last}")


_VM_FIELDS = ("vmid", "name", "node", "type", "status", "uptime", "cpu", "mem", "maxmem", "template")
_NODE_FIELDS = ("node", "status", "uptime", "cpu", "mem", "maxmem", "maxcpu")


def _pve(cfg: LiveConfig, name: str, args: dict) -> dict:
    if name == "pve.guests":
        rows = _pve_get(cfg, "/cluster/resources?type=vm")
        keep = [{k: r.get(k) for k in _VM_FIELDS} for r in rows if not args.get("node") or r.get("node") == args["node"]]
        keep.sort(key=lambda r: (str(r["node"]), r["vmid"] or 0))
        return {"guests": keep}
    # pve.node_status: the cluster's own view first (works for a dead node too), then live detail if it is up
    nodes = _pve_get(cfg, "/cluster/resources?type=node")
    me = next((r for r in nodes if r.get("node") == args["node"]), None)
    if me is None:
        raise ToolError(404, "no such node in the cluster")
    out = {"cluster_view": {k: me.get(k) for k in _NODE_FIELDS}}
    if me.get("status") == "online":
        try:
            d = _pve_get(cfg, f"/nodes/{args['node']}/status")
            out["detail"] = {"loadavg": d.get("loadavg"), "uptime": d.get("uptime"), "kversion": d.get("kversion"),
                             "memory": d.get("memory"), "swap": d.get("swap"), "rootfs": d.get("rootfs")}
        except ToolError as e:
            out["detail_error"] = e.message
    return out

def _zbx(cfg: LiveConfig, method: str, params: dict):
    """One Zabbix JSON-RPC read. Method names are fixed in this module; the guard keeps it that way."""
    if not method.endswith(".get"):
        raise ToolError(500, "internal: only *.get methods may be called")
    c = _cred(cfg, "zabbix")
    req = urllib.request.Request(c.get("url") or cfg.zabbix_url,
                                 json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode(),
                                 {"Content-Type": "application/json-rpc", "Authorization": "Bearer " + c["value"]}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=cfg.zabbix_timeout) as r:
            d = json.load(r)
    except (OSError, ValueError) as e:
        raise ToolError(502, f"zabbix unreachable: {type(e).__name__}")
    if "error" in d:
        raise ToolError(502, "zabbix refused: " + str(d["error"].get("data") or d["error"].get("message"))[:120])
    return d["result"]


def _zabbix(cfg: LiveConfig, name: str, args: dict) -> dict:
    sev = {"0": "not_classified", "1": "information", "2": "warning", "3": "average", "4": "high", "5": "disaster"}
    hostids = None
    if args.get("host"):
        hosts = _zbx(cfg, "host.get", {"output": ["hostid"], "filter": {"host": [args["host"]]}})
        if not hosts:
            raise ToolError(404, "no such host in Zabbix")
        hostids = [h["hostid"] for h in hosts]
    if name == "zabbix.host":
        h = _zbx(cfg, "host.get", {
            "output": ["host", "name", "status", "maintenance_status", "description"], "hostids": hostids,
            "selectHostGroups": ["name"], "selectTags": ["tag", "value"], "selectParentTemplates": ["name"],
            "selectInterfaces": ["ip", "port", "type", "available", "error"]})[0]
        return {"host": {
            "host": h["host"], "enabled": h["status"] == "0", "in_maintenance": h["maintenance_status"] == "1",
            "groups": [g["name"] for g in h.get("hostgroups", [])], "tags": h.get("tags", []),
            "templates": [t["name"] for t in h.get("parentTemplates", [])],
            "interfaces": [{"ip": i["ip"], "port": i["port"], "type": i["type"], "available": i["available"],
                            "error": (i.get("error") or "")[:200]} for i in h.get("interfaces", [])]}}
    if name == "zabbix.problems":
        params = {"output": ["eventid", "objectid", "name", "severity", "clock", "acknowledged", "r_eventid"], "recent": True,
                  "sortfield": ["eventid"], "sortorder": "DESC", "limit": 50, "selectTags": ["tag", "value"]}
        if hostids:
            params["hostids"] = hostids
        rows = _zbx(cfg, "problem.get", params)
        # problem.get cannot say which host a problem is on (a Zabbix 7 limit); the problem's object is a trigger, and a trigger can.
        hosts_of = _trigger_hosts(cfg, [r["objectid"] for r in rows if r.get("objectid")])
        return {"problems": [{"eventid": r["eventid"], "name": r["name"], "hosts": hosts_of.get(r.get("objectid"), []),
                              "severity": sev.get(r["severity"], r["severity"]),
                              "since": int(r["clock"]), "acknowledged": r["acknowledged"] == "1",
                              "resolved": r["r_eventid"] != "0", "tags": r.get("tags", [])} for r in rows]}
    rows = _zbx(cfg, "trigger.get", {
        "output": ["description", "priority", "lastchange", "value", "state", "error"], "hostids": hostids,
        "monitored": True, "filter": {"value": 1}, "expandDescription": True, "sortfield": "priority", "sortorder": "DESC",
        "limit": 100, "selectHosts": ["host"]})
    return {"active_triggers": [{"description": r["description"], "hosts": [h["host"] for h in r.get("hosts", [])],
                                 "severity": sev.get(r["priority"], r["priority"]),
                                 "since": int(r["lastchange"]), "state": "unknown" if r["state"] == "1" else "normal",
                                 "error": (r.get("error") or "")[:200]} for r in rows]}


def _trigger_hosts(cfg: LiveConfig, triggerids: list) -> dict:
    """{triggerid: [host name, ...]} for the triggers behind a list of problems. Best effort: a Zabbix hiccup here leaves the hosts
    empty rather than failing the whole problems answer."""
    ids = sorted({str(t) for t in triggerids})
    if not ids:
        return {}
    try:
        rows = _zbx(cfg, "trigger.get", {"output": ["triggerid"], "triggerids": ids, "selectHosts": ["host"]})
    except ToolError:
        return {}
    return {r["triggerid"]: [h["host"] for h in r.get("hosts", [])] for r in rows}


def _nb_get(cfg: LiveConfig, path: str):
    """GET one NetBox API path with the view-only token (GET only, by construction)."""
    c = _cred(cfg, "netbox")
    req = urllib.request.Request(f"{(c.get('url') or cfg.netbox_url).rstrip('/')}/api{path}",
                                 headers={"Authorization": "Bearer " + c["value"], "Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=cfg.netbox_timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ToolError(502, f"netbox refused the read-only token ({e.code})")
        raise ToolError(502, f"netbox answered HTTP {e.code}")
    except (OSError, ValueError) as e:
        raise ToolError(502, f"netbox unreachable: {type(e).__name__}")


def _nb_name(o):
    return o.get("name") if isinstance(o, dict) else None


def _nb_vm(cfg: LiveConfig, name: str) -> dict | None:
    res = _nb_get(cfg, "/virtualization/virtual-machines/?" + urllib.parse.urlencode({"name": name, "limit": 2})).get("results", [])
    return res[0] if res else None


def _netbox(cfg: LiveConfig, name: str, args: dict) -> dict:
    if name == "netbox.host":
        vm = _nb_vm(cfg, args["name"])
        if vm:
            ip = (vm.get("primary_ip4") or {}).get("address")
            return {"kind": "vm", "name": vm["name"], "status": (vm.get("status") or {}).get("value"), "hypervisor": _nb_name(vm.get("device")),
                    "cluster": _nb_name(vm.get("cluster")), "site": _nb_name(vm.get("site")), "role": _nb_name(vm.get("role")),
                    "vcpus": vm.get("vcpus"), "memory_mb": vm.get("memory"), "disk_gb": vm.get("disk"), "primary_ip": ip,
                    "tags": [t["name"] for t in vm.get("tags", [])], "vmid": (vm.get("custom_fields") or {}).get("VMID")}
        res = _nb_get(cfg, "/dcim/devices/?" + urllib.parse.urlencode({"name": args["name"], "limit": 2})).get("results", [])
        if not res:
            raise ToolError(404, "no such host in NetBox")
        d = res[0]
        return {"kind": "device", "name": d["name"], "status": (d.get("status") or {}).get("value"), "role": _nb_name(d.get("role")),
                "site": _nb_name(d.get("site")), "primary_ip": (d.get("primary_ip4") or {}).get("address"),
                "tags": [t["name"] for t in d.get("tags", [])]}
    # netbox.hypervisor_peers: every guest that shares the hypervisor with `host` (host may itself be the hypervisor)
    vm = _nb_vm(cfg, args["host"])
    hv = _nb_name(vm.get("device")) if vm else None
    if not hv:
        dev = _nb_get(cfg, "/dcim/devices/?" + urllib.parse.urlencode({"name": args["host"], "limit": 2})).get("results", [])
        hv = dev[0]["name"] if dev else None
    if not hv:
        raise ToolError(404, "NetBox has no hypervisor recorded for that host")
    guests = _nb_get(cfg, "/virtualization/virtual-machines/?" + urllib.parse.urlencode({"device": hv, "limit": 200})).get("results", [])
    return {"hypervisor": hv, "count": len(guests),
            "guests": sorted(({"name": g["name"], "status": (g.get("status") or {}).get("value"), "vmid": (g.get("custom_fields") or {}).get("VMID"),
                               "role": _nb_name(g.get("role"))} for g in guests), key=lambda g: g["name"])}


# kind -> (API prefix, namespaced?, resource). The kinds are the closed enum in SPEC["kube.get"]; `secrets` and
# `configmaps` are not in it, and the ServiceAccount's ClusterRole does not grant them either (two independent walls).
_KUBE_KINDS = {
    "pods": ("/api/v1", True, "pods"), "nodes": ("/api/v1", False, "nodes"), "services": ("/api/v1", True, "services"),
    "endpoints": ("/api/v1", True, "endpoints"), "events": ("/api/v1", True, "events"),
    "persistentvolumeclaims": ("/api/v1", True, "persistentvolumeclaims"),
    "deployments": ("/apis/apps/v1", True, "deployments"), "statefulsets": ("/apis/apps/v1", True, "statefulsets"),
    "daemonsets": ("/apis/apps/v1", True, "daemonsets"),
    "helmreleases": ("/apis/helm.toolkit.fluxcd.io/v2", True, "helmreleases"),
    "kustomizations": ("/apis/kustomize.toolkit.fluxcd.io/v1", True, "kustomizations"),
}
_REDACT = [
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [redacted]"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)\S{6,}"), r"\1\2[redacted]"),
    (re.compile(r"sk-ant-[\w-]{10,}"), "[redacted]"), (re.compile(r"\bhvs\.[A-Za-z0-9]{16,}"), "[redacted]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[redacted private key]"),
    (re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[\w-]+"), "[redacted webhook]"),
]


def redact(text: str) -> str:
    for pat, repl in _REDACT:
        text = pat.sub(repl, text)
    return text


def _kube_get(cfg: LiveConfig, path: str, raw: bool = False):
    """GET one Kubernetes API path with the short-lived read-only token (tried against each API server in turn)."""
    c = _cred(cfg, "kube")
    ctx = ssl.create_default_context(cadata=c["ca"]) if c.get("ca") else None
    last = "no API server answered"
    for server in c.get("servers", []):
        req = urllib.request.Request(server.rstrip("/") + path, headers={"Authorization": "Bearer " + c["token"], "Accept": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=cfg.kube_timeout, context=ctx) as r:
                body = r.read()
            return body.decode(errors="replace") if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ToolError(502, f"kubernetes refused the read-only token ({e.code}): expired, or the RBAC does not allow this read")
            if e.code == 404:
                raise ToolError(404, "not found in the cluster")
            last = f"HTTP {e.code} from {server}"
        except (OSError, ValueError) as e:
            last = f"{type(e).__name__} from {server}"
    raise ToolError(502, f"kubernetes API unreachable: {last}")


def _conds(o: dict) -> list[dict]:
    return [{"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason"), "message": (c.get("message") or "")[:200]}
            for c in (o.get("status") or {}).get("conditions", [])]


def _summarise(kind: str, o: dict) -> dict:
    md, st, sp = o.get("metadata", {}), o.get("status") or {}, o.get("spec") or {}
    base = {"name": md.get("name"), **({"namespace": md["namespace"]} if md.get("namespace") else {})}
    if kind == "pods":
        cs = st.get("containerStatuses", [])
        waiting = [f"{c['name']}: {c['state']['waiting'].get('reason')}" for c in cs if "waiting" in c.get("state", {})]
        return {**base, "phase": st.get("phase"), "ready": f"{sum(1 for c in cs if c.get('ready'))}/{len(cs)}",
                "restarts": sum(c.get("restartCount", 0) for c in cs), "node": sp.get("nodeName"), "waiting": waiting,
                "reason": st.get("reason")}
    if kind == "nodes":
        bad = [c for c in _conds(o) if (c["type"] == "Ready") != (c["status"] == "True")]
        return {**base, "ready": any(c["type"] == "Ready" and c["status"] == "True" for c in _conds(o)), "problems": bad,
                "kubelet": (st.get("nodeInfo") or {}).get("kubeletVersion"), "taints": [f"{t['key']}:{t['effect']}" for t in sp.get("taints", [])]}
    if kind in ("deployments", "statefulsets"):
        return {**base, "desired": sp.get("replicas"), "ready": st.get("readyReplicas", 0), "available": st.get("availableReplicas", 0),
                "updated": st.get("updatedReplicas", 0), "conditions": [c for c in _conds(o) if c["status"] != "True" or c["type"] == "Progressing"]}
    if kind == "daemonsets":
        return {**base, "desired": st.get("desiredNumberScheduled"), "ready": st.get("numberReady", 0), "unavailable": st.get("numberUnavailable", 0)}
    if kind == "services":
        return {**base, "type": sp.get("type"), "clusterIP": sp.get("clusterIP"), "ports": [f"{p.get('port')}/{p.get('protocol')}" for p in sp.get("ports", [])]}
    if kind == "endpoints":
        subs = o.get("subsets") or []
        return {**base, "ready_addresses": sum(len(x.get("addresses", [])) for x in subs), "not_ready_addresses": sum(len(x.get("notReadyAddresses", [])) for x in subs)}
    if kind == "persistentvolumeclaims":
        return {**base, "phase": st.get("phase"), "storageClass": sp.get("storageClassName"), "capacity": (st.get("capacity") or {}).get("storage")}
    if kind == "events":
        return {**base, "type": o.get("type"), "reason": o.get("reason"), "object": f"{(o.get('involvedObject') or {}).get('kind')}/{(o.get('involvedObject') or {}).get('name')}",
                "message": (o.get("message") or "")[:300], "count": o.get("count"), "last": o.get("lastTimestamp") or o.get("eventTime")}
    # helmreleases / kustomizations: Ready condition is what matters
    ready = next((c for c in _conds(o) if c["type"] == "Ready"), {})
    return {**base, "ready": ready.get("status"), "reason": ready.get("reason"), "message": ready.get("message"),
            "revision": st.get("lastAppliedRevision") or st.get("lastAttemptedRevision"), "suspended": bool(sp.get("suspend"))}


def _kube(cfg: LiveConfig, name: str, args: dict) -> dict:
    if name == "kube.logs":
        q = {"tailLines": args.get("tail", 100), "timestamps": "true"}
        if args.get("container"):
            q["container"] = args["container"]
        if args.get("previous") == "true":
            q["previous"] = "true"
        text = _kube_get(cfg, f"/api/v1/namespaces/{args['namespace']}/pods/{args['pod']}/log?" + urllib.parse.urlencode(q), raw=True)
        text = redact(text)
        return {"lines": text[-cfg.kube_log_max_chars:].splitlines()[-int(args.get("tail", 100)):]}
    kind = args["kind"]
    prefix, namespaced, res = _KUBE_KINDS[kind]
    if args.get("name") and namespaced and not args.get("namespace"):
        raise ToolError(400, "name needs a namespace for this kind")
    ns = f"/namespaces/{args['namespace']}" if namespaced and args.get("namespace") else ""
    path = f"{prefix}{ns}/{res}" + (f"/{args['name']}" if args.get("name") else "")
    data = _kube_get(cfg, path)
    if args.get("name"):
        return {"kind": kind, "item": _summarise(kind, data), "conditions": _conds(data)}
    items = data.get("items", [])
    if kind == "events":  # newest first, warnings before normals, bounded
        items = sorted(items, key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "", reverse=True)
        items = sorted(items, key=lambda e: e.get("type") != "Warning")
    out = [_summarise(kind, i) for i in items]
    return {"kind": kind, "count": len(out), "items": out[:100], "truncated": len(out) > 100}


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _sem_get(cfg: LiveConfig, path: str):
    """GET one Semaphore API path with the guest-role token (read-only by Semaphore's own role model; GET only here)."""
    c = _cred(cfg, "semaphore")
    req = urllib.request.Request(c["url"].rstrip("/") + path, headers={"Authorization": "Bearer " + c["value"], "Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=cfg.semaphore_timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ToolError(502, f"semaphore refused the read-only token ({e.code})")
        raise ToolError(502, f"semaphore answered HTTP {e.code}")
    except (OSError, ValueError) as e:
        raise ToolError(502, f"semaphore unreachable: {type(e).__name__}")


def _semaphore(cfg: LiveConfig, args: dict) -> dict:
    """Recent task runs (newest first). For the most recent failures, the redacted tail of the task output: the answer to
    'why did the last apply / drift-check fail' without the agent needing broader access."""
    if "task_id" in args:
        return _semaphore_changes(cfg, args["task_id"])
    limit = args.get("limit", 10)
    tasks = _sem_get(cfg, f"/project/{cfg.semaphore_project}/tasks/last")[:limit]
    out, failed_fetched = [], 0
    for t in tasks:
        row = {"id": t["id"], "template": t.get("tpl_alias"), "status": t.get("status"), "created": t.get("created"),
               "start": t.get("start"), "end": t.get("end"), "playbook": t.get("playbook"), "commit": (t.get("commit_hash") or "")[:8]}
        if t.get("status") == "error" and failed_fetched < 3:
            failed_fetched += 1
            try:
                lines = [_ANSI.sub("", o.get("output", "")) for o in _sem_get(cfg, f"/project/{cfg.semaphore_project}/tasks/{t['id']}/output")]
                row["output_tail"] = redact("\n".join(lines[-20:]))[-2500:].splitlines()
            except ToolError as e:
                row["output_tail_error"] = e.message
        out.append(row)
    return {"tasks": out}


def _semaphore_changes(cfg: LiveConfig, task_id: int) -> dict:
    """What one run (a drift-check, say) would change or changed: its PLAY RECAP lines and every `changed:` task with the
    first lines of its diff, redacted and capped. Lets the drafting session document a drift finding from evidence."""
    task = _sem_get(cfg, f"/project/{cfg.semaphore_project}/tasks/{task_id}")
    lines = [_ANSI.sub("", o.get("output", "")) for o in _sem_get(cfg, f"/project/{cfg.semaphore_project}/tasks/{task_id}/output")]
    recap = [redact(l.strip())[:200] for l in lines if "changed=" in l and "ok=" in l][:20]
    changed, current = [], "?"
    for i, l in enumerate(lines):
        if l.startswith("TASK ["):
            current = l.strip()[:160]
        elif l.startswith("changed:") and len(changed) < 40:
            diff = [x.rstrip()[:200] for x in lines[i + 1:i + 12] if x.startswith(("--- ", "+++ ", "@@", "+", "-")) and not x.startswith(("+++ /dev", "--- /dev"))]
            changed.append({"task": current, "line": redact(l.strip())[:200], "diff": [redact(x) for x in diff[:8]]})
    return {"task": {"id": task.get("id"), "template": task.get("tpl_alias"), "status": task.get("status"), "start": task.get("start"),
                     "end": task.get("end"), "commit": (task.get("commit_hash") or "")[:8]}, "recap": recap, "changed": changed}


_REL = re.compile(r"^([0-9]{1,5})([smhd])$")


def _when(v: str | None, default: str | None = None) -> str | None:
    """A time argument: relative ('15m', '2h', '1d') becomes an RFC3339 UTC instant; anything else is passed through
    (the argument pattern already limits it to RFC3339 / unix-seconds-shaped text)."""
    v = v or default
    if v is None:
        return None
    m = _REL.match(v)
    if m:
        secs = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - secs))
    return v


def _obs_get(cfg: LiveConfig, url: str, what: str) -> str:
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=cfg.obs_timeout) as r:
            return r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ToolError(502, f"{what} refused this caller ({e.code}): the route is restricted to the Toolbelt host")
        if e.code == 404:
            raise ToolError(502, f"{what}: that endpoint is not routable for the Toolbelt (404)")
        body = e.read().decode(errors="replace")[:200]
        raise ToolError(502, f"{what} answered HTTP {e.code}: {redact(body)}")
    except (OSError, ValueError) as e:
        raise ToolError(502, f"{what} unreachable: {type(e).__name__}")


def _logs(cfg: LiveConfig, args: dict) -> dict:
    q = {"query": args["query"], "limit": args.get("limit", 50)}
    start, end = _when(args.get("start"), "15m"), _when(args.get("end"))
    q["start"] = start
    if end:
        q["end"] = end
    text = _obs_get(cfg, f"{cfg.logs_url.rstrip('/')}/select/logsql/query?" + urllib.parse.urlencode(q), "VictoriaLogs")
    lines = []
    for ln in text.splitlines():
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        # Kubernetes log lines carry a dozen label fields (container id, pod ip, helm labels...) that only cost the model
        # tokens: keep the four that locate the source, drop the rest. Other (non-underscore) fields such as `level` stay.
        keep_k8s = {"kubernetes.pod_name", "kubernetes.pod_namespace", "kubernetes.container_name", "kubernetes.node_name"}
        lines.append({"time": row.get("_time"), "stream": row.get("_stream"), "msg": redact(str(row.get("_msg", "")))[:500],
                      **{k: str(v)[:120] for k, v in row.items() if k not in ("_time", "_stream", "_msg", "_stream_id")
                         and not k.startswith("_") and (not k.startswith("kubernetes.") or k in keep_k8s)}})
    out, size = [], 0
    for row in lines:
        size += len(json.dumps(row))
        if size > cfg.logs_max_chars:
            break
        out.append(row)
    return {"window_start": start, "returned": len(out), "truncated": len(out) < len(lines), "lines": out}


def _trim_series(cfg: LiveConfig, result: list) -> tuple[list, bool]:
    out = []
    for s in result[:cfg.metrics_max_series]:
        row = {"metric": s.get("metric", {})}
        if "values" in s:
            vals = s["values"]
            if len(vals) > cfg.metrics_max_points:  # keep the shape: evenly thinned, last point always kept
                step = len(vals) / cfg.metrics_max_points
                vals = [vals[int(i * step)] for i in range(cfg.metrics_max_points - 1)] + [vals[-1]]
            row["values"] = vals
        if "value" in s:
            row["value"] = s["value"]
        out.append(row)
    return out, len(result) > cfg.metrics_max_series


def _metrics(cfg: LiveConfig, name: str, args: dict) -> dict:
    base = cfg.metrics_url.rstrip("/")
    if name == "metrics.query":
        q = {"query": args["query"]}
        if args.get("time"):
            q["time"] = _when(args["time"])
        path = "/api/v1/query"
    else:
        q = {"query": args["query"], "start": _when(args["start"]), "end": _when(args.get("end"), "0s") or _when("0s"), "step": args.get("step", "60s")}
        path = "/api/v1/query_range"
    data = json.loads(_obs_get(cfg, f"{base}{path}?" + urllib.parse.urlencode(q), "VictoriaMetrics"))
    if data.get("status") != "success":
        raise ToolError(502, "VictoriaMetrics: " + redact(str(data.get("error", "query failed")))[:200])
    res = data["data"].get("result", [])
    series, truncated = _trim_series(cfg, res if isinstance(res, list) else [])
    return {"result_type": data["data"].get("resultType"), "series": len(res) if isinstance(res, list) else 1, "returned": len(series),
            "truncated_series": truncated, "result": series}


def live(cfg: LiveConfig, name: str, args: dict) -> dict:
    if name.startswith("registry."):
        return _registry(cfg, name, args)
    if name.startswith("git."):
        return _git(cfg, name, args)
    if name == "reach.tcp":
        return _reach(cfg, args)
    if name.startswith("pve."):
        return _pve(cfg, name, args)
    if name.startswith("zabbix."):
        return _zabbix(cfg, name, args)
    if name.startswith("netbox."):
        return _netbox(cfg, name, args)
    if name.startswith("kube."):
        return _kube(cfg, name, args)
    if name == "semaphore.tasks":
        return _semaphore(cfg, args)
    if name == "logs.query":
        return _logs(cfg, args)
    if name in ("metrics.query", "metrics.range"):
        return _metrics(cfg, name, args)
    raise ToolError(501, f"{name} has no live backend yet (replay only)")
