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
    "semaphore.tasks": {"limit": I(1, 20)},
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
        params = {"output": ["eventid", "name", "severity", "clock", "acknowledged", "r_eventid"], "recent": True,
                  "sortfield": ["eventid"], "sortorder": "DESC", "limit": 50, "selectTags": ["tag", "value"]}
        if hostids:
            params["hostids"] = hostids
        rows = _zbx(cfg, "problem.get", params)
        return {"problems": [{"eventid": r["eventid"], "name": r["name"], "severity": sev.get(r["severity"], r["severity"]),
                              "since": int(r["clock"]), "acknowledged": r["acknowledged"] == "1",
                              "resolved": r["r_eventid"] != "0", "tags": r.get("tags", [])} for r in rows]}
    rows = _zbx(cfg, "trigger.get", {
        "output": ["description", "priority", "lastchange", "value", "state", "error"], "hostids": hostids,
        "monitored": True, "filter": {"value": 1}, "expandDescription": True, "sortfield": "priority", "sortorder": "DESC",
        "limit": 100})
    return {"active_triggers": [{"description": r["description"], "severity": sev.get(r["priority"], r["priority"]),
                                 "since": int(r["lastchange"]), "state": "unknown" if r["state"] == "1" else "normal",
                                 "error": (r.get("error") or "")[:200]} for r in rows]}


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
    raise ToolError(501, f"{name} has no live backend yet (replay only)")
