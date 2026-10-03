"""Phase 10g slice B: the rebuild runner (aiops/runner/rebuild_runner.py).

Everything runs against a FAKE terraform (a small script generated per test that prints canned plan JSON and logs its
argv) and a REAL local git "origin" (a bare repo holding the real registry), so the checkout sync, the origin-moved
check and the argv the runner builds are all exercised for real. The socket tests use a real unix socket and the real
peer-credential lookup (the test process is the peer).
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "runner"))
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))

import rebuild_runner as rr  # noqa: E402

ADDR = 'proxmox_virtual_environment_container.canary["canary-2"]'
GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def lxc(name="canary-2", vmid=1191, ip="10.0.11.191/24", node="urd"):
    return {"node_name": node, "vm_id": vmid, "initialization": [{"hostname": name, "ip_config": [{"ipv4": [{"address": ip}]}]}],
            "network_interface": [{"vlan_id": 11}]}


def rc(addr=ADDR, actions=("delete", "create"), before=None, after=None, typ="proxmox_virtual_environment_container"):
    return {"address": addr, "type": typ, "change": {"actions": list(actions), "before": before if before is not None else lxc(),
                                                       "after": after if after is not None else lxc()}}


def good_plan():
    return {"format_version": "1.2", "resource_changes": [rc()]}


FAKE_TF = r'''#!{python}
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
sc = json.load(open(os.path.join(here, "scenario.json")))
with open(os.path.join(here, "calls.jsonl"), "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(), "env_keys": sorted(os.environ)}}) + "\n")
cmd = sys.argv[1]
if cmd == "init":
    sys.exit(sc.get("init_rc", 0))
if cmd == "plan":
    out = [a for a in sys.argv if a.startswith("-out=")][0][5:]
    if sc.get("fail_with_replace") and any(a.startswith("-replace=") for a in sys.argv):
        sys.stderr.write("Error: no such resource instance in state"); sys.exit(1)
    if sc.get("plan_rc", 0):
        sys.stderr.write("Error: boom"); sys.exit(sc["plan_rc"])
    open(out, "w").write(json.dumps(sc.get("plan_json")) + str(sc.get("nonce", "")))
    sys.exit(0)
if cmd == "show":
    print(json.dumps(sc["plan_json"])); sys.exit(0)
if cmd == "apply":
    open(os.path.join(here, "apply.started"), "w").write("1")
    time.sleep(sc.get("apply_sleep", 0))
    if sc.get("apply_rc", 0):
        sys.stderr.write("Error: apply boom"); sys.exit(sc["apply_rc"])
    sys.exit(0)
sys.exit(9)
'''


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True, capture_output=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp", prefix="rr"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # a bare origin with the real registry and a stub for the canary module
        seed = self.tmp / "seed"
        (seed / "aiops").mkdir(parents=True)
        shutil.copy(REPO / "aiops" / "actions.yml", seed / "aiops" / "actions.yml")
        (seed / "terraform/proxmox/asgard-lxcs").mkdir(parents=True)
        (seed / "terraform/proxmox/asgard-lxcs/main.tf").write_text("# stub\n")
        git("init", "-q", "-b", "main", cwd=seed)
        git("add", "-A", cwd=seed)
        git("commit", "-q", "-m", "seed", cwd=seed)
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(self.origin)], check=True, capture_output=True)
        self.seed = seed
        git("remote", "add", "origin", str(self.origin), cwd=seed)
        # the fake terraform
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.tf = self.bin / "terraform"
        self.tf.write_text(FAKE_TF.format(python=sys.executable))
        self.tf.chmod(0o755)
        self.scenario({"plan_json": good_plan()})
        self.out = _Sink()
        self.clock = [1_000_000.0]
        cfg = rr.Config(checkout=self.tmp / "work" / "checkout", plan_dir=self.tmp / "work" / "plans", terraform=str(self.tf),
                        repo_url=str(self.origin), data_dir=self.tmp / "work" / "tfdata")
        self.runner = rr.Runner(cfg, clock=lambda: self.clock[0], out=self.out)

    def scenario(self, d):
        (self.bin / "scenario.json").write_text(json.dumps(d))

    def calls(self):
        p = self.bin / "calls.jsonl"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def ask(self, **req):
        req.setdefault("v", 1)
        req.setdefault("request_id", "11111111-2222-3333-4444-555555555555")
        return self.runner.handle_line(json.dumps(req).encode())

    def plan(self, target="canary-2", cls="canary"):
        return self.ask(op="plan", **{"class": cls, "target": target})

    def move_origin(self):
        (self.seed / "x.txt").write_text(str(time.time()))
        git("add", "-A", cwd=self.seed)
        git("commit", "-q", "-m", "more", cwd=self.seed)
        git("push", "-q", "origin", "main", cwd=self.seed)

    def events(self):
        return [json.loads(line) for line in self.out.lines()]


class _Sink:
    def __init__(self):
        self.buf, self.lock = [], threading.Lock()

    def write(self, s):
        with self.lock:
            self.buf.append(s)

    def flush(self):
        pass

    def lines(self):
        return "".join(self.buf).splitlines()


class PlanApplyTests(Base):
    def test_a_destroyed_guest_is_planned_without_replace(self):
        """A guest deleted behind Terraform's back: `-replace` of an address not in state fails, so the runner retries
        once with a plain single-resource plan (a create); the plan checker still decides."""
        self.scenario({"plan_json": good_plan(), "fail_with_replace": True})
        r = self.plan()
        self.assertTrue(r["ok"], r)
        plans = [c["argv"] for c in self.calls() if c["argv"][0] == "plan"]
        self.assertEqual(len(plans), 2)
        self.assertTrue(any(a.startswith("-replace=") for a in plans[0]))
        self.assertFalse(any(a.startswith("-replace=") for a in plans[1]))
        self.assertEqual(sum(1 for a in plans[1] if a.startswith("-target=")), 1)  # still exactly one target

    def test_a_plan_that_fails_both_ways_is_refused(self):
        self.scenario({"plan_json": good_plan(), "plan_rc": 1})
        r = self.plan()
        self.assertFalse(r["ok"])
        self.assertIn("terraform-failed", r["error"])
    def test_happy_plan_then_apply(self):
        r = self.plan()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["address"], ADDR)
        self.assertEqual(r["summary"], {"action": "replace", "changes": 1,
                                        "identity": {"name": "canary-2", "vmid": 1191, "node": "urd", "ip": "10.0.11.191"}})
        self.assertRegex(r["plan_id"], r"^[0-9a-f]{64}$")
        self.assertEqual(r["expires_at"], 1_000_000 + 900)
        self.assertRegex(r["origin_main"], r"^[0-9a-f]{40}$")
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["request_id"], "11111111-2222-3333-4444-555555555555")
        # the argv terraform saw: fixed list, the validated address only, in the module directory
        plan_call = [c for c in self.calls() if c["argv"][0] == "plan"][0]
        self.assertTrue(plan_call["cwd"].endswith("terraform/proxmox/asgard-lxcs"))
        self.assertEqual(plan_call["argv"][:3], ["plan", f"-replace={ADDR}", f"-target={ADDR}"])
        self.assertIn("-input=false", plan_call["argv"])
        self.assertIn("-lock-timeout=60s", plan_call["argv"])
        a = self.ask(op="apply", plan_id=r["plan_id"])
        self.assertEqual(a["ok"], True, a)
        self.assertEqual(a["result"], {"applied": True, "resources": 1})
        self.assertEqual(a["origin_main"], r["origin_main"])
        apply_call = [c for c in self.calls() if c["argv"][0] == "apply"][0]
        self.assertEqual(apply_call["argv"][-1], str(self.runner.cfg.plan_dir / f"{r['plan_id']}.tfplan"))
        self.assertFalse(any(self.runner.cfg.plan_dir.glob("*.tfplan")), "the planfile is single use")
        self.assertEqual(self.ask(op="status")["last"]["ok"], True)
        events = [e["event"] for e in self.events()]
        self.assertIn("plan-ok", events)
        self.assertIn("apply-ok", events)

    def test_a_plan_cannot_be_applied_twice(self):
        r = self.plan()
        self.assertTrue(self.ask(op="apply", plan_id=r["plan_id"])["ok"])
        again = self.ask(op="apply", plan_id=r["plan_id"])
        self.assertEqual(again["error"].split(":")[0], "plan-unknown")

    def test_create_of_a_missing_guest_is_accepted(self):
        change = rc(actions=("create",), before=None, after=lxc())
        change["change"]["before"] = None
        self.scenario({"plan_json": {"resource_changes": [change]}})
        r = self.plan()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["summary"]["action"], "create")

    def test_check_plan_rejection_stores_nothing(self):
        bad = {"resource_changes": [rc(), rc(addr="proxmox_virtual_environment_container.hugin", before=lxc("hugin", 1121), after=lxc("hugin", 1121))]}
        self.scenario({"plan_json": bad})
        r = self.plan()
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"].startswith("plan-rejected"))
        self.assertTrue(r["problems"])
        self.assertNotIn("plan_id", r)
        self.assertFalse(any(self.runner.cfg.plan_dir.glob("*.tfplan")))

    def test_identity_drift_is_rejected(self):
        self.scenario({"plan_json": {"resource_changes": [rc(after=lxc(ip="10.0.11.99/24"))]}})
        r = self.plan()
        self.assertFalse(r["ok"])
        self.assertTrue(any("identity" in p for p in r["problems"]))

    def test_expired_plan(self):
        r = self.plan()
        self.clock[0] += 901
        a = self.ask(op="apply", plan_id=r["plan_id"])
        self.assertEqual(a["error"].split(":")[0], "plan-expired")
        self.assertEqual(self.ask(op="apply", plan_id=r["plan_id"])["error"].split(":")[0], "plan-unknown")
        self.assertNotIn("apply", [c["argv"][0] for c in self.calls()])

    def test_unknown_plan_id(self):
        a = self.ask(op="apply", plan_id="0" * 64)
        self.assertEqual(a["error"].split(":")[0], "plan-unknown")

    def test_origin_moved(self):
        r = self.plan()
        self.move_origin()
        a = self.ask(op="apply", plan_id=r["plan_id"])
        self.assertEqual(a["error"].split(":")[0], "origin-moved")
        self.assertNotIn("apply", [c["argv"][0] for c in self.calls()])
        # the plan is spent; planning again picks up the new origin/main
        self.assertEqual(self.ask(op="apply", plan_id=r["plan_id"])["error"].split(":")[0], "plan-unknown")
        r2 = self.plan()
        self.assertTrue(r2["ok"])
        self.assertNotEqual(r2["origin_main"], r["origin_main"])

    def test_dirty_checkout_refuses_apply(self):
        r = self.plan()
        (self.runner.cfg.checkout / "stray.txt").write_text("x")
        a = self.ask(op="apply", plan_id=r["plan_id"])
        self.assertEqual(a["error"].split(":")[0], "origin-moved")

    def test_tampered_planfile_refuses_apply(self):
        r = self.plan()
        (self.runner.cfg.plan_dir / f"{r['plan_id']}.tfplan").write_text("tampered")
        a = self.ask(op="apply", plan_id=r["plan_id"])
        self.assertEqual(a["error"].split(":")[0], "denied")
        self.assertNotIn("apply", [c["argv"][0] for c in self.calls()])

    def test_terraform_failures(self):
        self.scenario({"plan_json": good_plan(), "plan_rc": 1})
        self.assertEqual(self.plan()["error"].split(":")[0], "terraform-failed")
        self.scenario({"plan_json": good_plan(), "init_rc": 1})
        self.assertEqual(self.plan()["error"].split(":")[0], "terraform-failed")
        self.scenario({"plan_json": good_plan(), "apply_rc": 1})
        r = self.plan()
        a = self.ask(op="apply", plan_id=r["plan_id"])
        self.assertEqual(a["error"].split(":")[0], "terraform-failed")
        self.assertFalse(self.ask(op="status")["last"]["ok"])
        self.assertFalse(any(self.runner.cfg.plan_dir.glob("*.tfplan")), "a failed apply spends the plan too")

    def test_secrets_pass_only_by_name_and_nothing_else_is_inherited(self):
        os.environ["TF_VAR_proxmox_api_token"] = "x"
        os.environ["SOME_OTHER_SECRET"] = "y"
        self.addCleanup(os.environ.pop, "TF_VAR_proxmox_api_token", None)
        self.addCleanup(os.environ.pop, "SOME_OTHER_SECRET", None)
        self.assertTrue(self.plan()["ok"])
        keys = self.calls()[0]["env_keys"]
        self.assertIn("TF_VAR_proxmox_api_token", keys)
        self.assertNotIn("SOME_OTHER_SECRET", keys)
        self.assertIn("TF_DATA_DIR", keys)
        for e in self.events():
            self.assertNotIn("proxmox_api_token", json.dumps(e))


class RefusalTests(Base):
    def assertDenied(self, r):
        self.assertFalse(r["ok"], r)
        self.assertEqual(r["error"].split(":")[0], "denied", r)
        self.assertEqual(self.calls(), [], "a denied request must never reach terraform")

    def test_bad_class(self):
        self.assertDenied(self.plan(cls="nonsense"))

    def test_class_not_enabled_in_runner(self):
        self.assertDenied(self.plan(target="mimir", cls="adguard-replica"))
        self.assertDenied(self.plan(target="einherjar-urd", cls="worker"))
        self.assertDenied(self.plan(target="do1", cls="offsite"))

    def test_class_mismatch(self):
        self.assertDenied(self.plan(target="canary-2", cls="adguard-replica"))

    def test_deny_listed_targets(self):
        for t in ("saga", "pbs", "frigg", "gondul", "hugin", "gna"):
            self.assertDenied(self.plan(target=t, cls="canary"))

    def test_target_not_in_registry(self):
        self.assertDenied(self.plan(target="canary-9"))

    def test_deny_list_rechecked_from_the_checked_out_registry(self):
        # a registry (on origin/main) that moved a canary's VMID onto the deny list is refused even though it is in a class
        import yaml
        reg = yaml.safe_load((self.seed / "aiops/actions.yml").read_text())
        reg["rebuild"]["deny"]["names"].append("canary-2")
        (self.seed / "aiops/actions.yml").write_text(yaml.safe_dump(reg))
        git("add", "-A", cwd=self.seed)
        git("commit", "-q", "-m", "deny", cwd=self.seed)
        git("push", "-q", "origin", "main", cwd=self.seed)
        self.assertDenied(self.plan())

    def test_runner_allow_list_is_independent_of_the_registry(self):
        import yaml
        reg = yaml.safe_load((self.seed / "aiops/actions.yml").read_text())
        reg["rebuild"]["classes"]["canary"]["hosts"]["canary-2"]["vmid"] = 1111  # mimir's VMID
        (self.seed / "aiops/actions.yml").write_text(yaml.safe_dump(reg))
        git("add", "-A", cwd=self.seed)
        git("commit", "-q", "-m", "vmid", cwd=self.seed)
        git("push", "-q", "origin", "main", cwd=self.seed)
        self.assertDenied(self.plan())

    def test_malformed_and_unexpected_input(self):
        h = self.runner.handle_line
        for raw in (b"not json", b"[1,2]", b'"x"', b"\xff\xfe", b"{}", json.dumps({"v": 2, "op": "status"}).encode(),
                    json.dumps({"v": 1, "op": "shell"}).encode(),
                    json.dumps({"v": 1, "op": "plan", "class": "canary", "target": "canary-2; rm -rf /"}).encode(),
                    json.dumps({"v": 1, "op": "plan", "class": "canary", "target": "canary-2", "cmd": "id"}).encode(),
                    json.dumps({"v": 1, "op": "apply", "plan_id": "../../etc/passwd"}).encode(),
                    json.dumps({"v": 1, "op": "apply", "plan_id": "A" * 64}).encode(),
                    json.dumps({"v": 1, "op": "plan", "request_id": "bad id!", "class": "canary", "target": "canary-2"}).encode()):
            r = h(raw)
            self.assertFalse(r["ok"], raw)
            self.assertTrue(r["error"].startswith("bad-request"), (raw, r))
        self.assertEqual(self.calls(), [])

    def test_oversize_line(self):
        r = self.runner.handle_line(b"x" * (rr.MAX_LINE + 1))
        self.assertTrue(r["error"].startswith("bad-request"))


class BusyTests(Base):
    def test_busy_while_an_apply_runs_and_status_reports_it(self):
        self.scenario({"plan_json": good_plan(), "apply_sleep": 2})
        r = self.plan()
        res = {}
        t = threading.Thread(target=lambda: res.update(self.ask(op="apply", plan_id=r["plan_id"])))
        t.start()
        for _ in range(100):
            if (self.bin / "apply.started").exists():
                break
            time.sleep(0.05)
        self.assertTrue((self.bin / "apply.started").exists())
        self.assertTrue(self.ask(op="status")["busy"])
        b1 = self.plan(target="canary-3")
        b2 = self.ask(op="apply", plan_id=r["plan_id"])
        self.assertEqual(b1["error"].split(":")[0], "busy")
        self.assertEqual(b2["error"].split(":")[0], "busy")
        t.join(10)
        self.assertTrue(res["ok"], res)
        self.assertFalse(self.ask(op="status")["busy"])

    def test_concurrent_plans_are_serialised_not_parallel(self):
        self.scenario({"plan_json": good_plan(), "apply_sleep": 0})
        results = []

        def go():
            results.append(self.plan())

        threads = [threading.Thread(target=go) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        ok = [r for r in results if r["ok"]]
        busy = [r for r in results if not r["ok"] and r["error"].startswith("busy")]
        self.assertEqual(len(ok) + len(busy), 6, results)
        self.assertGreaterEqual(len(ok), 1)


class SocketTests(Base):
    def serve(self, uids):
        path = str(self.tmp / "r.sock")
        srv = rr.RunnerServer(path, self.runner, uids)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return path

    def call(self, path, payload: bytes, wait=True):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(30)
        s.connect(path)
        try:
            s.sendall(payload)
            if not wait:
                return None
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            return json.loads(buf) if buf else None
        finally:
            s.close()

    def test_socket_mode_and_roundtrip(self):
        path = self.serve([os.getuid()])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o660)
        r = self.call(path, b'{"v":1,"op":"status"}\n')
        self.assertTrue(r["ok"])
        r = self.call(path, json.dumps({"v": 1, "request_id": "abc", "op": "plan", "class": "canary", "target": "canary-1"}).encode() + b"\n")
        self.assertFalse(r["ok"])  # the fake plan is for canary-2 only, so canary-1 fails the shape check
        self.assertEqual(r["request_id"], "abc")

    def test_peer_uid_not_allowed(self):
        path = self.serve([os.getuid() + 1])
        r = self.call(path, b'{"v":1,"op":"status"}\n')
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"].startswith("denied"))
        self.assertIn("peer-refused", [e["event"] for e in self.events()])
        self.assertEqual(self.calls(), [])

    def test_oversize_and_unterminated_lines_over_the_socket(self):
        path = self.serve([os.getuid()])
        r = self.call(path, b"x" * (rr.MAX_LINE + 50) + b"\n")
        self.assertTrue(r["error"].startswith("bad-request"))

    def test_malformed_json_over_the_socket(self):
        path = self.serve([os.getuid()])
        r = self.call(path, b"{not json}\n")
        self.assertTrue(r["error"].startswith("bad-request"))

    def test_concurrent_clients_one_wins_the_rest_are_busy(self):
        self.scenario({"plan_json": good_plan(), "apply_sleep": 1.5})
        path = self.serve([os.getuid()])
        plan = self.call(path, json.dumps({"v": 1, "op": "plan", "class": "canary", "target": "canary-2"}).encode() + b"\n")
        self.assertTrue(plan["ok"], plan)
        out = []

        def go():
            out.append(self.call(path, json.dumps({"v": 1, "op": "apply", "plan_id": plan["plan_id"]}).encode() + b"\n"))

        ts = [threading.Thread(target=go) for _ in range(4)]
        for t in ts:
            t.start()
            time.sleep(0.15)
        for t in ts:
            t.join(30)
        self.assertEqual(sum(1 for r in out if r["ok"]), 1, out)
        self.assertEqual(sum(1 for r in out if not r["ok"] and r["error"].startswith("busy")), 3, out)
        self.assertEqual(len([c for c in self.calls() if c["argv"][0] == "apply"]), 1)


class TableTests(unittest.TestCase):
    def test_only_canary_has_a_class_table_row_and_address_is_for_each_key(self):
        self.assertEqual(set(rr.CLASS_TABLE), {"canary"})
        text = (REPO / "terraform/proxmox/asgard-lxcs/lxcs.tf").read_text()
        self.assertIn('resource "proxmox_virtual_environment_container" "canary"', text)
        self.assertIn("for_each = local.canary_nodes", text)
        row = rr.CLASS_TABLE["canary"]
        self.assertEqual(row["address"].format(target="canary-3"), 'proxmox_virtual_environment_container.canary["canary-3"]')
        self.assertTrue((REPO / row["module_dir"]).is_dir())

    def test_registry_canaries_match_the_table(self):
        import yaml
        reg = yaml.safe_load((REPO / "aiops/actions.yml").read_text())
        hosts = reg["rebuild"]["classes"]["canary"]["hosts"]
        self.assertEqual({int(h["vmid"]) for h in hosts.values()}, set(rr.CLASS_TABLE["canary"]["vmids"]))
        self.assertTrue(all(rr.CLASS_TABLE["canary"]["target_re"].fullmatch(n) for n in hosts))


if __name__ == "__main__":
    unittest.main()
