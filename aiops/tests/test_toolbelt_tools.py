"""Phase 10d2: Toolbelt read-only tools - allow-list, argument contract, replay, caps, live handlers."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))
sys.path.insert(0, str(REPO / "aiops" / "tools"))

import core  # noqa: E402
import normalize  # noqa: E402
import server  # noqa: E402
import test_toolbelt as base  # noqa: E402
import tools  # noqa: E402

TOKEN = base.TOKEN
WRITE_VERBS = {"delete", "exec", "create", "update", "apply", "patch", "write", "run", "restart", "scale", "set", "kill",
               "drain", "cordon", "post", "put", "remove", "start", "stop", "reboot"}


def make(tmp: Path, **cfg):
    audit: list[dict] = []
    c = core.Config(replay_dir=tmp / "replays", live=tools.LiveConfig(root=REPO), **cfg)
    tb = core.Toolbelt(c, normalize.load_routes(), base.KNOWN, clock=base.Clock(), audit=audit.append)
    return tb, audit


def record(tmp: Path, scenario: str, name: str, args: dict, response: dict) -> None:
    """Append one recorded call to <replays>/<scenario>/scenario.json (the readable replay format)."""
    f = tmp / "replays" / scenario / "scenario.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    doc = json.loads(f.read_text()) if f.exists() else {"calls": []}
    doc["calls"].append({"tool": name, "args": args, "response": response})
    f.write_text(json.dumps(doc))


class Contract(unittest.TestCase):
    def test_every_tool_name_is_read_shaped(self):
        for name in tools.SPEC:
            verb = name.split(".", 1)[1]
            self.assertFalse(set(verb.split("_")) & WRITE_VERBS, f"{name} looks like a write")

    def test_write_shaped_and_unknown_names_are_404(self):
        for name in ("kube.delete", "kube.exec", "vault.read", "exec.run", "zabbix.update", "pve.reboot", "semaphore.run",
                     "git.push", "shell.bash", "kube.get_secret"):
            with self.assertRaises(tools.ToolError, msg=name) as cm:
                tools.validate(name, {})
            self.assertEqual(cm.exception.status, 404)

    def test_arguments_are_strictly_typed_and_bounded(self):
        bad = [
            ("registry.runbook", {"id": "rb-lower"}), ("registry.runbook", {}), ("registry.runbook", {"id": "RB-X", "x": 1}),
            ("git.log", {"max": 21}), ("git.log", {"max": True}), ("git.log", {"path": "a b"}),
            ("git.show", {"rev": "--output=/tmp/x"}), ("git.show", {"rev": "HEAD"}),
            ("reach.tcp", {"host": "a;b", "port": 22}), ("reach.tcp", {"host": "h", "port": 0}), ("reach.tcp", {"host": "h", "port": "22"}),
            ("kube.get", {"kind": "secrets"}), ("kube.get", {"kind": "pods", "namespace": "Bad_NS"}),
            ("kube.logs", {"namespace": "a", "pod": "b", "tail": 201}),
            ("logs.query", {"query": "x" * 501}), ("metrics.range", {"query": "up", "start": "1h", "step": "1d"}),
            ("zabbix.host", {"host": ""}), ("semaphore.tasks", {"limit": 99}), ("registry.actions", {"x": 1}),
        ]
        for name, args in bad:
            with self.assertRaises(tools.ToolError, msg=(name, args)) as cm:
                tools.validate(name, args)
            self.assertEqual(cm.exception.status, 400, (name, args))
        self.assertEqual(tools.validate("kube.get", {"kind": "pods", "namespace": "flux-system"}),
                         {"kind": "pods", "namespace": "flux-system"})

    def test_secrets_are_not_a_readable_kind(self):
        self.assertNotIn("secrets", tools.SPEC["kube.get"]["kind"].choices)


class Replay(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_recorded_call_is_returned_without_touching_live(self):
        tb, audit = make(self.tmp)
        tb.cfg.live = None  # a live call would 501: replay must not need it
        record(self.tmp, "skuld-freeze", "kube.get", {"kind": "nodes"}, {"nodes": [{"name": "einherjar-skuld", "ready": False}]})
        out = tb.call_tool("kube.get", {"kind": "nodes"}, replay="skuld-freeze")
        self.assertEqual((out["replayed"], out["result"]["nodes"][0]["name"]), (True, "einherjar-skuld"))
        self.assertTrue(any(a["event"] == "tool_call" and a["replayed"] for a in audit))

    def test_unrecorded_call_is_no_recording_and_counted(self):
        tb, _ = make(self.tmp)
        with self.assertRaises(core.Rejected) as cm:
            tb.call_tool("kube.get", {"kind": "pods"}, replay="skuld-freeze")
        self.assertEqual((cm.exception.status, cm.exception.message), (404, "NO_RECORDING"))
        self.assertEqual(tb.stats()["no_recording_today"], 1)

    def test_matching_is_on_tool_and_exact_arguments_not_key_order(self):
        record(self.tmp, "m", "kube.logs", {"namespace": "a", "pod": "b"}, {"lines": ["x"]})
        tb, _ = make(self.tmp)
        self.assertEqual(tb.call_tool("kube.logs", {"pod": "b", "namespace": "a"}, replay="m")["result"], {"lines": ["x"]})
        for other in ({"namespace": "a", "pod": "c"}, {"namespace": "a", "pod": "b", "tail": 5}):
            with self.assertRaises(core.Rejected) as cm:
                tb.call_tool("kube.logs", other, replay="m")
            self.assertEqual(cm.exception.message, "NO_RECORDING")

    def test_args_hash_ignores_key_order_but_not_values(self):
        a = tools.args_hash("kube.logs", {"namespace": "a", "pod": "b"})
        self.assertEqual(a, tools.args_hash("kube.logs", {"pod": "b", "namespace": "a"}))
        self.assertNotEqual(a, tools.args_hash("kube.logs", {"namespace": "a", "pod": "c"}))

    def test_scenario_name_cannot_escape_the_replay_dir(self):
        tb, _ = make(self.tmp)
        for bad in ("../x", "a/b", "A", ".hidden", "x" * 80):
            with self.assertRaises(core.Rejected, msg=bad) as cm:
                tb.call_tool("registry.runbooks", {}, replay=bad)
            self.assertEqual(cm.exception.status, 400)

    def test_replay_also_validates_arguments(self):
        tb, _ = make(self.tmp)
        with self.assertRaises(core.Rejected) as cm:
            tb.call_tool("kube.get", {"kind": "secrets"}, replay="skuld-freeze")
        self.assertEqual(cm.exception.status, 400)


class Live(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.tb, self.audit = make(self.tmp, max_tool_calls_per_incident=3)
        self.inc = self.tb.ingest_zabbix(base.ev())["incident_id"]

    def test_live_calls_need_a_real_incident(self):
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("registry.runbooks", {})
        self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("registry.runbooks", {}, incident_id=999)
        self.assertEqual(cm.exception.status, 404)

    def test_registry_serves_the_real_runbooks(self):
        out = self.tb.call_tool("registry.runbooks", {}, self.inc)["result"]["runbooks"]
        self.assertTrue(any(r["id"] == "RB-ZBX-TRIAGE" for r in out))
        rb = self.tb.call_tool("registry.runbook", {"id": "RB-ZBX-TRIAGE"}, self.inc)["result"]["runbook"]
        self.assertEqual(rb["id"], "RB-ZBX-TRIAGE")
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("registry.runbook", {"id": "RB-NOPE"}, self.inc)
        self.assertEqual(cm.exception.status, 404)

    def test_per_incident_cap_stops_a_runaway_agent(self):
        for _ in range(3):
            self.tb.call_tool("registry.actions", {}, self.inc)
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("registry.actions", {}, self.inc)
        self.assertEqual(cm.exception.status, 429)

    def test_contract_only_tools_are_501_live(self):
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("logs.query", {"query": "error"}, self.inc)
        self.assertEqual(cm.exception.status, 501)

    def test_repo_history_without_a_clone_is_501_and_with_one_is_read_only(self):
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("git.log", {}, self.inc)
        self.assertEqual(cm.exception.status, 501)
        # a throwaway two-commit repo: the real checkout is shallow in CI, so it cannot be the fixture
        repo = self.tmp / "repo"
        repo.mkdir()
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
               "PATH": os.environ["PATH"], "HOME": str(self.tmp)}
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "one"], ["commit", "-q", "--allow-empty", "-m", "two"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)
        self.tb.cfg.live.repo_dir = repo
        self.tb.cfg.max_tool_calls_per_incident = 50
        out = self.tb.call_tool("git.log", {"max": 2}, self.inc)["result"]["output"]
        self.assertEqual([ln.split(" ", 4)[-1] for ln in out.strip().splitlines()], ["two", "one"])
        for path in ("../etc/passwd", "/etc/passwd", "a/../../b"):
            with self.assertRaises(core.Rejected, msg=path) as cm:
                self.tb.call_tool("git.log", {"path": path}, self.inc)
            self.assertEqual(cm.exception.status, 400)

    def test_reach_refuses_outside_the_homelab_and_unlisted_ports(self):
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("reach.tcp", {"host": "127.0.0.1", "port": 22}, self.inc)  # not in 10.0.0.0/8
        self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("reach.tcp", {"host": "10.0.11.30", "port": 31337}, self.inc)
        self.assertEqual(cm.exception.status, 400)

    def test_reach_reports_open_and_closed(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        cport = closed.getsockname()[1]
        closed.close()
        self.addCleanup(srv.close)
        live = self.tb.cfg.live
        live.reach_nets, live.reach_ports = ("127.0.0.0/8",), (port, cport)
        self.assertTrue(self.tb.call_tool("reach.tcp", {"host": "127.0.0.1", "port": port}, self.inc)["result"]["open"])
        self.assertFalse(self.tb.call_tool("reach.tcp", {"host": "127.0.0.1", "port": cport}, self.inc)["result"]["open"])


class FakePve:
    """A tiny PVE API: checks the token header, records methods, serves cluster resources."""

    def __init__(self, token_header, online=True):
        self.seen = []
        outer = self
        nodes = [{"node": "urd", "status": "online", "uptime": 5, "cpu": 0.1, "mem": 1, "maxmem": 2, "maxcpu": 4},
                 {"node": "skuld", "status": "online" if online else "offline", "uptime": 0, "cpu": 0, "mem": 0, "maxmem": 2, "maxcpu": 4}]
        vms = [{"vmid": 2013, "name": "einherjar-skuld", "node": "skuld", "type": "qemu", "status": "stopped" if not online else "running"},
               {"vmid": 1101, "name": "pbs", "node": "urd", "type": "lxc", "status": "running"}]

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_GET(self):  # noqa: N802
                outer.seen.append(("GET", self.path))
                if self.headers.get("Authorization") != token_header:
                    self.send_response(401)
                    self.end_headers()
                    return
                data = (vms if "type=vm" in self.path else nodes if "type=node" in self.path
                        else {"loadavg": ["0.1", "0.2", "0.3"], "uptime": 5, "kversion": "6.x", "memory": {}, "swap": {}, "rootfs": {}})
                raw = json.dumps({"data": data}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):  # noqa: N802
                outer.seen.append(("POST", self.path))
                self.send_response(500)
                self.end_headers()

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class Pve(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.tmp / "creds").mkdir()
        (self.tmp / "creds" / "pve.json").write_text(json.dumps({"token_id": "aiops@pve!toolbelt", "secret": "s3cret-uuid"}))
        self.tb, _ = make(self.tmp)
        self.tb.cfg.live.creds_dir = self.tmp / "creds"
        self.inc = self.tb.ingest_zabbix(base.ev())["incident_id"]
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        self.dead = f"http://127.0.0.1:{dead.getsockname()[1]}"
        dead.close()

    def pve(self, **kw):
        f = FakePve("PVEAPIToken=aiops@pve!toolbelt=s3cret-uuid", **kw)
        self.addCleanup(f.close)
        self.tb.cfg.live.pve_urls = (self.dead, f.url)  # first node dead: must fail over
        self.tb.cfg.live.pve_timeout = 2
        return f

    def test_guests_fail_over_past_a_dead_node_and_only_ever_get(self):
        f = self.pve()
        out = self.tb.call_tool("pve.guests", {}, self.inc)["result"]["guests"]
        self.assertEqual([g["name"] for g in out], ["einherjar-skuld", "pbs"])  # sorted by node name, then vmid
        self.assertEqual({m for m, _ in f.seen}, {"GET"})

    def test_guest_list_can_be_limited_to_one_node(self):
        self.pve()
        out = self.tb.call_tool("pve.guests", {"node": "skuld"}, self.inc)["result"]["guests"]
        self.assertEqual([g["name"] for g in out], ["einherjar-skuld"])

    def test_node_status_reports_an_offline_node_from_the_cluster_view(self):
        f = self.pve(online=False)
        out = self.tb.call_tool("pve.node_status", {"node": "skuld"}, self.inc)["result"]
        self.assertEqual(out["cluster_view"]["status"], "offline")
        self.assertNotIn("detail", out)
        self.assertFalse(any("/nodes/skuld/status" in p for _, p in f.seen))  # no call to a node that is down

    def test_node_status_adds_live_detail_for_an_online_node(self):
        self.pve()
        out = self.tb.call_tool("pve.node_status", {"node": "urd"}, self.inc)["result"]
        self.assertEqual(out["detail"]["loadavg"][0], "0.1")

    def test_unknown_node_is_404(self):
        self.pve()
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("pve.node_status", {"node": "nope"}, self.inc)
        self.assertEqual(cm.exception.status, 404)

    def test_refused_token_is_reported_not_retried_forever(self):
        f = FakePve("PVEAPIToken=someone-else")
        self.addCleanup(f.close)
        self.tb.cfg.live.pve_urls = (f.url,)
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("pve.guests", {}, self.inc)
        self.assertEqual(cm.exception.status, 502)
        self.assertIn("refused", cm.exception.message)

    def test_all_nodes_down_is_a_502_naming_the_problem(self):
        self.tb.cfg.live.pve_urls = (self.dead,)
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("pve.guests", {}, self.inc)
        self.assertEqual(cm.exception.status, 502)
        self.assertIn("unreachable", cm.exception.message)

    def test_missing_credential_is_501(self):
        (self.tmp / "creds" / "pve.json").unlink()
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("pve.guests", {}, self.inc)
        self.assertEqual(cm.exception.status, 501)
        self.assertIn("credential", cm.exception.message)


class FakeZabbix:
    """JSON-RPC stand-in: checks the bearer token, records methods, serves canned *.get answers."""

    def __init__(self, token="zbx-token-0123456789012345678901234567"):
        self.methods = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.methods.append(body["method"])
                if self.headers.get("Authorization") != f"Bearer {token}":
                    res = {"error": {"message": "Not authorised", "data": "Session terminated"}}
                elif body["method"] == "host.get" and body["params"].get("filter", {}).get("host") == ["ghost"]:
                    res = {"result": []}
                elif body["method"] == "host.get" and "selectInterfaces" in body["params"]:
                    res = {"result": [{"host": "canary-1", "name": "canary-1", "status": "0", "maintenance_status": "0",
                                       "description": "", "hostgroups": [{"name": "Asgard/LXCs/Canary"}],
                                       "tags": [{"tag": "env", "value": "nonprod"}], "parentTemplates": [{"name": "Linux by Zabbix agent"}],
                                       "interfaces": [{"ip": "10.0.11.190", "port": "10050", "type": "1", "available": "2", "error": "timed out"}]}]}
                elif body["method"] == "host.get":
                    res = {"result": [{"hostid": "10708"}]}
                elif body["method"] == "problem.get":
                    res = {"result": [{"eventid": "9", "name": "Linux: Zabbix agent is not available", "severity": "3", "clock": "1790000000",
                                       "acknowledged": "0", "r_eventid": "0", "tags": [{"tag": "class", "value": "os"}]}]}
                elif body["method"] == "trigger.get":
                    res = {"result": [{"description": "Disk full on /", "priority": "4", "lastchange": "1790000100", "value": "1",
                                       "state": "0", "error": ""}]}
                else:
                    res = {"error": {"message": "Method not found", "data": body["method"]}}
                raw = json.dumps({"jsonrpc": "2.0", "id": 1, **res}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/api_jsonrpc.php"
        self.token = token

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class ZabbixTool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.tmp / "creds").mkdir()
        self.tb, _ = make(self.tmp)
        self.tb.cfg.live.creds_dir = self.tmp / "creds"
        self.inc = self.tb.ingest_zabbix(base.ev())["incident_id"]
        self.fz = FakeZabbix()
        self.addCleanup(self.fz.close)

    def cred(self, token=None):
        (self.tmp / "creds" / "zabbix.json").write_text(json.dumps({"value": token or self.fz.token, "url": self.fz.url}))

    def test_host_problems_and_triggers_are_translated_and_only_get_methods_are_called(self):
        self.cred()
        host = self.tb.call_tool("zabbix.host", {"host": "canary-1"}, self.inc)["result"]["host"]
        self.assertEqual((host["enabled"], host["groups"], host["templates"]), (True, ["Asgard/LXCs/Canary"], ["Linux by Zabbix agent"]))
        self.assertEqual(host["interfaces"][0]["available"], "2")
        probs = self.tb.call_tool("zabbix.problems", {"host": "canary-1"}, self.inc)["result"]["problems"]
        self.assertEqual((probs[0]["severity"], probs[0]["resolved"], probs[0]["acknowledged"]), ("average", False, False))
        trig = self.tb.call_tool("zabbix.triggers", {"host": "canary-1"}, self.inc)["result"]["active_triggers"]
        self.assertEqual((trig[0]["description"], trig[0]["severity"]), ("Disk full on /", "high"))
        self.assertTrue(self.fz.methods and all(m.endswith(".get") for m in self.fz.methods), self.fz.methods)

    def test_problems_without_a_host_is_fleet_wide(self):
        self.cred()
        self.tb.call_tool("zabbix.problems", {}, self.inc)
        self.assertEqual(self.fz.methods, ["problem.get"])  # no host lookup needed

    def test_unknown_host_is_404(self):
        self.cred()
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("zabbix.host", {"host": "ghost"}, self.inc)
        self.assertEqual(cm.exception.status, 404)

    def test_a_refused_token_is_a_502_naming_zabbix(self):
        self.cred(token="wrong-token-wrong-token-wrong-token-xx")
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("zabbix.problems", {}, self.inc)
        self.assertEqual(cm.exception.status, 502)
        self.assertIn("zabbix refused", cm.exception.message)

    def test_missing_credential_is_501(self):
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("zabbix.problems", {}, self.inc)
        self.assertEqual(cm.exception.status, 501)

    def test_the_client_refuses_to_call_a_non_get_method(self):
        self.cred()
        with self.assertRaises(tools.ToolError) as cm:
            tools._zbx(self.tb.cfg.live, "host.update", {})
        self.assertEqual(cm.exception.status, 500)
        self.assertEqual(self.fz.methods, [])  # nothing was sent


class ReplayOnIncident(unittest.TestCase):
    """An acceptance run carries its scenario on the incident, so the agent's tool calls need no header."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        record(self.tmp, "skuld-freeze", "kube.get", {"kind": "nodes"}, {"nodes": [{"name": "einherjar-skuld", "ready": False}]})
        self.tb, _ = make(self.tmp)
        self.tb.cfg.live = None  # a live call would be refused: replay must not need it

    def test_scenario_on_ingest_makes_later_tool_calls_replays(self):
        inc = self.tb.ingest_zabbix(base.ev(), replay="skuld-freeze")["incident_id"]
        self.assertEqual(self.tb.group(inc)["replay"], "skuld-freeze")
        out = self.tb.call_tool("kube.get", {"kind": "nodes"}, inc)  # no replay argument
        self.assertEqual((out["replayed"], out["result"]["nodes"][0]["ready"]), (True, False))

    def test_an_unrecorded_call_in_a_replay_incident_is_no_recording_not_live(self):
        inc = self.tb.ingest_zabbix(base.ev(), replay="skuld-freeze")["incident_id"]
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("kube.get", {"kind": "pods"}, inc)
        self.assertEqual(cm.exception.message, "NO_RECORDING")

    def test_an_unknown_or_unsafe_scenario_is_refused_at_ingest(self):
        for bad in ("nope", "../x", "A B"):
            with self.assertRaises(core.Rejected, msg=bad) as cm:
                self.tb.ingest_zabbix(base.ev(host=f"h-{abs(hash(bad)) % 999}"), replay=bad)
            self.assertEqual(cm.exception.status, 400)

    def test_replay_latest_reports_the_run_for_a_harness(self):
        inc = self.tb.ingest_zabbix(base.ev(), replay="skuld-freeze")["incident_id"]
        self.tb.call_tool("kube.get", {"kind": "nodes"}, inc)
        with self.assertRaises(core.Rejected):
            self.tb.call_tool("kube.get", {"kind": "pods"}, inc)  # unrecorded
        out = self.tb.replay_latest("skuld-freeze")
        self.assertEqual((out["incident_id"], out["state"], out["no_recording"], out["diagnosis"]), (inc, "received", 1, None))
        self.assertEqual([(c["tool"], c["args"], c["outcome"]) for c in out["calls"]],
                         [("kube.get", {"kind": "nodes"}, "served"), ("kube.get", {"kind": "pods"}, "no_recording")])

    def test_replay_latest_is_404_for_a_scenario_never_run_and_400_for_a_bad_name(self):
        for name, status in (("skuld-freeze", 404), ("../x", 400)):
            with self.assertRaises(core.Rejected) as cm:
                self.tb.replay_latest(name)
            self.assertEqual(cm.exception.status, status)

    def test_a_database_from_before_the_call_columns_is_migrated(self):
        import sqlite3

        db = self.tmp / "old2.sqlite3"
        c = sqlite3.connect(db)
        c.executescript(core.SCHEMA.replace(",\n  args_json TEXT, outcome TEXT NOT NULL DEFAULT 'served'", "").replace(",\n  replay TEXT", ""))
        c.execute("INSERT INTO tool_calls(incident_id, tool, args_hash, replayed, ts) VALUES (1,'registry.runbooks','x',0,1)")
        c.commit()
        c.close()
        tb = core.Toolbelt(core.Config(db_path=str(db)), normalize.load_routes(), base.KNOWN, clock=base.Clock(), audit=lambda r: None)
        row = tb.db.execute("SELECT outcome, args_json FROM tool_calls").fetchone()
        self.assertEqual((row["outcome"], row["args_json"]), ("served", None))  # old rows keep counting as served
        tb.db.close()

    def test_a_replay_run_never_suppresses_or_joins_a_real_alert(self):
        replayed = self.tb.ingest_zabbix(base.ev(), replay="skuld-freeze")
        real = self.tb.ingest_zabbix(base.ev())  # same event, seconds later, no replay
        self.assertEqual(real["action"], "leader")
        self.assertNotEqual(real["incident_id"], replayed["incident_id"])

    def test_a_real_alert_never_swallows_a_replay_run(self):
        real = self.tb.ingest_zabbix(base.ev())
        replayed = self.tb.ingest_zabbix(base.ev(), replay="skuld-freeze")
        self.assertEqual(replayed["action"], "leader")
        self.assertNotEqual(real["incident_id"], replayed["incident_id"])

    def test_repeated_runs_of_one_scenario_each_get_their_own_incident(self):
        ids = []
        for _ in range(3):
            ids.append(self.tb.ingest_zabbix(base.ev(), replay="skuld-freeze")["incident_id"])
            self.tb.clock.t += 100  # past the correlation window but inside the cooldown: a real repeat would be a duplicate
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(self.tb.replay_latest("skuld-freeze")["incident_id"], ids[-1])

    def test_a_normal_incident_has_no_replay(self):
        inc = self.tb.ingest_zabbix(base.ev())["incident_id"]
        self.assertIsNone(self.tb.group(inc)["replay"])

    def test_a_database_from_before_the_replay_column_is_migrated(self):
        db = self.tmp / "old.sqlite3"
        import sqlite3

        c = sqlite3.connect(db)
        c.executescript(core.SCHEMA.replace(",\n  replay TEXT", ""))
        c.execute("INSERT INTO incidents(group_key, state, opened_at, window_ends_at) VALUES ('unplaced','received',1,2)")
        c.commit()
        c.close()
        tb = core.Toolbelt(core.Config(db_path=str(db)), normalize.load_routes(), base.KNOWN, clock=base.Clock(), audit=lambda r: None)
        self.assertIsNone(tb.group(1)["replay"])  # old row survives, new column readable
        tb.db.close()


class FakeNetbox:
    """NetBox stand-in: bearer check, GET only, VMs placed on hypervisors via `device`."""

    def __init__(self, bearer="nbt_key.secret"):
        self.seen = []
        outer = self
        vms = [{"name": "canary-1", "status": {"value": "active"}, "device": {"name": "urd"}, "cluster": {"name": "niflheim"}, "site": {"name": "home"},
                "role": {"name": "canary"}, "vcpus": 1, "memory": 512, "disk": 4, "primary_ip4": {"address": "10.0.11.190/24"},
                "tags": [{"name": "nonprod"}], "custom_fields": {"VMID": "1190"}},
               {"name": "pbs", "status": {"value": "active"}, "device": {"name": "urd"}, "role": {"name": "backup-server"}, "tags": [],
                "custom_fields": {"VMID": "1101"}},
               {"name": "kvasir", "status": {"value": "active"}, "device": {"name": "skuld"}, "role": {"name": "dns"}, "tags": [],
                "custom_fields": {"VMID": "1112"}},
               {"name": "floating", "status": {"value": "active"}, "device": None, "tags": [], "custom_fields": {}}]
        devices = [{"name": "urd", "status": {"value": "active"}, "role": {"name": "hypervisor"}, "site": {"name": "home"},
                    "primary_ip4": {"address": "10.0.254.11/24"}, "tags": []}]

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_GET(self):  # noqa: N802
                from urllib.parse import parse_qs, urlparse

                u = urlparse(self.path)
                q = parse_qs(u.query)
                outer.seen.append(("GET", u.path))
                if self.headers.get("Authorization") != f"Bearer {bearer}":
                    self.send_response(403)
                    self.end_headers()
                    return
                if u.path == "/api/virtualization/virtual-machines/":
                    res = [v for v in vms if ("name" not in q or v["name"] == q["name"][0])
                           and ("device" not in q or (v["device"] or {}).get("name") == q["device"][0])]
                elif u.path == "/api/dcim/devices/":
                    res = [d for d in devices if "name" not in q or d["name"] == q["name"][0]]
                else:
                    res = []
                raw = json.dumps({"count": len(res), "results": res}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):  # noqa: N802
                outer.seen.append(("POST", self.path))
                self.send_response(500)
                self.end_headers()

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.bearer = bearer

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class NetboxTool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.tmp / "creds").mkdir()
        self.tb, _ = make(self.tmp)
        self.tb.cfg.live.creds_dir = self.tmp / "creds"
        self.inc = self.tb.ingest_zabbix(base.ev())["incident_id"]
        self.nb = FakeNetbox()
        self.addCleanup(self.nb.close)

    def cred(self, bearer=None):
        (self.tmp / "creds" / "netbox.json").write_text(json.dumps({"value": bearer or self.nb.bearer, "url": self.nb.url}))

    def test_host_returns_the_vm_with_its_hypervisor(self):
        self.cred()
        out = self.tb.call_tool("netbox.host", {"name": "canary-1"}, self.inc)["result"]
        self.assertEqual((out["kind"], out["hypervisor"], out["vmid"], out["primary_ip"]), ("vm", "urd", "1190", "10.0.11.190/24"))
        self.assertEqual(out["tags"], ["nonprod"])

    def test_host_falls_back_to_a_device_and_404s_when_unknown(self):
        self.cred()
        self.assertEqual(self.tb.call_tool("netbox.host", {"name": "urd"}, self.inc)["result"]["kind"], "device")
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("netbox.host", {"name": "nope"}, self.inc)
        self.assertEqual(cm.exception.status, 404)

    def test_hypervisor_peers_lists_everything_on_the_same_node(self):
        self.cred()
        out = self.tb.call_tool("netbox.hypervisor_peers", {"host": "canary-1"}, self.inc)["result"]
        self.assertEqual((out["hypervisor"], out["count"], [g["name"] for g in out["guests"]]), ("urd", 2, ["canary-1", "pbs"]))

    def test_peers_accepts_the_hypervisor_itself_and_refuses_an_unplaced_guest(self):
        self.cred()
        self.assertEqual(self.tb.call_tool("netbox.hypervisor_peers", {"host": "urd"}, self.inc)["result"]["count"], 2)
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("netbox.hypervisor_peers", {"host": "floating"}, self.inc)
        self.assertEqual(cm.exception.status, 404)

    def test_only_get_is_ever_sent_and_a_refused_token_is_a_502(self):
        self.cred()
        self.tb.call_tool("netbox.host", {"name": "canary-1"}, self.inc)
        self.assertEqual({m for m, _ in self.nb.seen}, {"GET"})
        self.cred(bearer="wrong")
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("netbox.host", {"name": "canary-1"}, self.inc)
        self.assertEqual((cm.exception.status, "refused" in cm.exception.message), (502, True))

    def test_missing_credential_is_501(self):
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("netbox.host", {"name": "canary-1"}, self.inc)
        self.assertEqual(cm.exception.status, 501)


class FakeKube:
    """Kubernetes API stand-in: bearer check, GET only, records paths, canned objects."""

    def __init__(self, token="kube-short-lived-token"):
        self.seen = []
        outer = self
        pods = {"items": [
            {"metadata": {"name": "web-1", "namespace": "apps"}, "spec": {"nodeName": "einherjar-skuld"},
             "status": {"phase": "Running", "containerStatuses": [{"name": "c", "ready": False, "restartCount": 7,
                                                                  "state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}},
            {"metadata": {"name": "web-2", "namespace": "apps"}, "spec": {"nodeName": "einherjar-urd"},
             "status": {"phase": "Running", "containerStatuses": [{"name": "c", "ready": True, "restartCount": 0, "state": {"running": {}}}]}}]}
        nodes = {"items": [{"metadata": {"name": "einherjar-skuld"}, "spec": {"taints": []},
                            "status": {"nodeInfo": {"kubeletVersion": "v1.36"}, "conditions": [{"type": "Ready", "status": "Unknown", "reason": "NodeStatusUnknown", "message": "kubelet stopped posting"}]}}]}
        hrs = {"items": [{"metadata": {"name": "immich", "namespace": "apps"}, "spec": {"suspend": False},
                          "status": {"lastAttemptedRevision": "1.2.3", "conditions": [{"type": "Ready", "status": "False", "reason": "InstallFailed", "message": "timeout waiting for rollout"}]}}]}
        events = {"items": [
            {"metadata": {"name": "e1", "namespace": "apps"}, "type": "Normal", "reason": "Pulled", "involvedObject": {"kind": "Pod", "name": "web-1"}, "message": "ok", "lastTimestamp": "2026-10-03T00:00:02Z"},
            {"metadata": {"name": "e2", "namespace": "apps"}, "type": "Warning", "reason": "BackOff", "involvedObject": {"kind": "Pod", "name": "web-1"}, "message": "restarting", "lastTimestamp": "2026-10-03T00:00:01Z"}]}
        log = "start\nconnecting with password=hunter2hunter2 to db\nAuthorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789\nready\n"

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_GET(self):  # noqa: N802
                outer.seen.append(self.path)
                if self.headers.get("Authorization") != f"Bearer {token}":
                    self.send_response(401)
                    self.end_headers()
                    return
                path = self.path.split("?")[0]
                if path.endswith("/log"):
                    raw, ctype = log.encode(), "text/plain"
                else:
                    table = {"/api/v1/namespaces/apps/pods": pods, "/api/v1/pods": pods, "/api/v1/nodes": nodes,
                             "/apis/helm.toolkit.fluxcd.io/v2/namespaces/apps/helmreleases": hrs, "/api/v1/namespaces/apps/events": events,
                             "/api/v1/namespaces/apps/pods/web-1": pods["items"][0]}
                    if path not in table:
                        self.send_response(404)
                        self.end_headers()
                        return
                    raw, ctype = json.dumps(table[path]).encode(), "application/json"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):  # noqa: N802
                outer.seen.append("POST " + self.path)
                self.send_response(500)
                self.end_headers()

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.token = token

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class KubeTool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.tmp / "creds").mkdir()
        self.tb, _ = make(self.tmp)
        self.tb.cfg.live.creds_dir = self.tmp / "creds"
        self.inc = self.tb.ingest_zabbix(base.ev())["incident_id"]
        self.k = FakeKube()
        self.addCleanup(self.k.close)
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        self.dead = f"http://127.0.0.1:{dead.getsockname()[1]}"
        dead.close()

    def cred(self, token=None, servers=None):
        (self.tmp / "creds" / "kube.json").write_text(json.dumps({"token": token or self.k.token, "servers": servers or [self.dead, self.k.url]}))

    def test_pods_are_summarised_with_the_signals_that_matter_and_fail_over_past_a_dead_server(self):
        self.cred()
        out = self.tb.call_tool("kube.get", {"kind": "pods", "namespace": "apps"}, self.inc)["result"]
        w1 = next(i for i in out["items"] if i["name"] == "web-1")
        self.assertEqual((w1["ready"], w1["restarts"], w1["waiting"], w1["node"]), ("0/1", 7, ["c: CrashLoopBackOff"], "einherjar-skuld"))

    def test_a_node_that_stopped_reporting_shows_its_problem_condition(self):
        self.cred()
        n = self.tb.call_tool("kube.get", {"kind": "nodes"}, self.inc)["result"]["items"][0]
        self.assertFalse(n["ready"])
        self.assertEqual(n["problems"][0]["reason"], "NodeStatusUnknown")

    def test_flux_release_status_comes_through(self):
        self.cred()
        h = self.tb.call_tool("kube.get", {"kind": "helmreleases", "namespace": "apps"}, self.inc)["result"]["items"][0]
        self.assertEqual((h["ready"], h["reason"], h["revision"]), ("False", "InstallFailed", "1.2.3"))

    def test_events_put_warnings_first(self):
        self.cred()
        ev = self.tb.call_tool("kube.get", {"kind": "events", "namespace": "apps"}, self.inc)["result"]["items"]
        self.assertEqual([e["reason"] for e in ev], ["BackOff", "Pulled"])

    def test_logs_are_redacted_before_they_leave_the_toolbelt(self):
        self.cred()
        lines = self.tb.call_tool("kube.logs", {"namespace": "apps", "pod": "web-1", "tail": 50}, self.inc)["result"]["lines"]
        text = "\n".join(lines)
        self.assertNotIn("hunter2hunter2", text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789", text)
        self.assertIn("[redacted]", text)
        self.assertIn("ready", text)

    def test_only_get_is_sent_and_secrets_are_not_an_askable_kind(self):
        self.cred()
        self.tb.call_tool("kube.get", {"kind": "pods", "namespace": "apps"}, self.inc)
        self.assertFalse(any(p.startswith("POST") for p in self.k.seen))
        for kind in ("secrets", "configmaps", "serviceaccounts"):
            with self.assertRaises(core.Rejected) as cm:
                self.tb.call_tool("kube.get", {"kind": kind, "namespace": "apps"}, self.inc)
            self.assertEqual(cm.exception.status, 400, kind)

    def test_an_expired_or_unauthorised_token_is_a_clear_502_and_unknown_objects_are_404(self):
        self.cred(token="expired")
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("kube.get", {"kind": "nodes"}, self.inc)
        self.assertEqual((cm.exception.status, "refused" in cm.exception.message), (502, True))
        self.cred()
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("kube.get", {"kind": "pods", "namespace": "apps", "name": "nope"}, self.inc)
        self.assertEqual(cm.exception.status, 404)

    def test_a_name_without_a_namespace_is_refused_and_a_missing_credential_is_501(self):
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("kube.get", {"kind": "nodes"}, self.inc)
        self.assertEqual(cm.exception.status, 501)  # a valid call, but no credential yet
        self.cred()
        with self.assertRaises(core.Rejected) as cm:
            self.tb.call_tool("kube.get", {"kind": "pods", "name": "web-1"}, self.inc)
        self.assertEqual(cm.exception.status, 400)


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.tb, _ = make(cls.tmp)
        record(cls.tmp, "demo", "registry.runbooks", {}, {"runbooks": ["x"]})
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.tb, TOKEN, [ipaddress.ip_network("127.0.0.0/8")]))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls._tmp.cleanup()

    def post(self, path, body, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", json.dumps(body).encode(), method="POST")
        req.add_header("Authorization", f"Bearer {TOKEN}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_replay_over_http_and_header_is_what_selects_it(self):
        status, out = self.post("/tool/registry.runbooks", {"args": {}}, {"X-AIOPS-Replay": "demo"})
        self.assertEqual((status, out["result"]), (200, {"runbooks": ["x"]}))
        self.assertEqual(self.post("/tool/registry.runbooks", {"args": {}})[0], 400)  # live, no incident

    def test_write_shaped_tool_names_are_refused_by_the_api(self):
        for name in ("kube.delete", "kube.exec", "shell.bash", "vault.read"):
            self.assertEqual(self.post(f"/tool/{name}", {"args": {}})[0], 404, name)
        self.assertEqual(self.post("/tool/Kube.Get", {"args": {}})[0], 404)  # route pattern is lower-case only

    def test_bad_bodies(self):
        self.assertEqual(self.post("/tool/kube.get", {"args": {"kind": "secrets"}})[0], 400)
        self.assertEqual(self.post("/tool/kube.get", ["not", "an", "object"])[0], 400)


if __name__ == "__main__":
    unittest.main()
