"""Phase 10g1: the pure rebuild-loop logic (aiops/toolbelt/rebuild.py) and the registry `rebuild:` section it reads.

Eligibility is table-driven: one base case that is a clean `go` (a dead canary), then each rule is exercised by changing
exactly the facts that rule reads. Plan-checker cases use the bpg/proxmox `terraform show -json` shape. Manifest cases
use kubectl PV / pod JSON. Lint cases prove the registry cannot name a control-plane node, PBS, or a quorum member.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))
sys.path.insert(0, str(REPO / "aiops" / "tools"))

import lint  # noqa: E402
import rebuild  # noqa: E402

REGISTRY = yaml.safe_load((REPO / "aiops" / "actions.yml").read_text())
RUNBOOKS = yaml.safe_load((REPO / "aiops" / "runbooks.yml").read_text())
POLICY = rebuild.RebuildPolicy.from_registry(REGISTRY)

T0 = 1_000_000.0


def dead_probes(n=4, span=900):
    return [{"t": T0 + i * span / (n - 1), "ok": False} for i in range(n)]


def facts(**over):
    """A dead canary on a healthy node: the clean `go` baseline."""
    f = {
        "target": "canary-2",
        "mode": "approval",
        "guest_state": "running",
        "probes": dead_probes(),
        "agent_silent": True,
        "node_online": True,
        "node_guests_ok": True,
        "last_backup_age_hours": None,
        "restart_breaker_tripped": False,
        "peers_healthy": None,
        "is_leader": False,
        "inflight": 0,
        "flags": {"maintenance": False, "kill_switch": False, "autonomy_rebuild": False},
        "history": {"target_day": 0, "target_week": 0, "class_day": 0, "fleet_day": 0, "breaker_open": False,
                    "peer_rebuilt_24h": False, "clean_rebuilds": 0},
    }
    for k, v in over.items():
        if k in ("flags", "history"):
            f[k] = {**f[k], **v}
        else:
            f[k] = v
    return f


def replica(**over):
    base = dict(target="mimir", peers_healthy=True, last_backup_age_hours=5.0)
    base.update(over)
    return facts(**base)


class EligibilityTests(unittest.TestCase):
    CASES = [
        # (label, facts, verdict, reason)
        ("dead canary: unreachable and agent silent", facts(), "go", "unreachable-agent-silent"),
        ("canary deleted behind Terraform's back", facts(guest_state="missing", probes=[]), "go", "guest-missing"),
        ("stopped, start tried and failed", facts(guest_state="stopped", probes=[], start_attempted=True, start_failed=True), "go", "start-failed"),
        ("stopped, start not tried yet: the ladder says start first", facts(guest_state="stopped", probes=[]), "skip", "ladder-start-first"),
        ("hung, start not tried yet", facts(guest_state="hung", probes=[]), "skip", "ladder-start-first"),
        ("stopped, start succeeded", facts(guest_state="stopped", probes=[], start_attempted=True, start_failed=False), "skip", "start-recovered"),
        ("running and reachable", facts(probes=[{"t": T0, "ok": True}]), "skip", "not-dead-or-broken"),
        ("only two failed probes", facts(probes=dead_probes(n=2)), "skip", "not-dead-or-broken"),
        ("three failed probes but under ten minutes", facts(probes=dead_probes(n=3, span=300)), "skip", "not-dead-or-broken"),
        ("failures followed by a success are not trailing", facts(probes=dead_probes() + [{"t": T0 + 5000, "ok": True}]), "skip", "not-dead-or-broken"),
        ("unreachable but the Zabbix agent still reports", facts(agent_silent=False), "skip", "agent-still-reporting"),
        ("broken: the 10f restart breaker tripped", facts(probes=[{"t": T0, "ok": True}], restart_breaker_tripped=True), "go", "restart-breaker-tripped"),
        ("broken: drift too large and not convergeable", facts(probes=[{"t": T0, "ok": True}], drift_changed=11, replay_converges=False), "go", "drift-not-convergeable"),
        ("drift large but a replay converges it", facts(probes=[{"t": T0, "ok": True}], drift_changed=40, replay_converges=True), "skip", "not-dead-or-broken"),
        ("drift small", facts(probes=[{"t": T0, "ok": True}], drift_changed=10, replay_converges=False), "skip", "not-dead-or-broken"),
        ("unknown guest state", facts(guest_state="unknown"), "stop", "state-unknown"),
        # scope
        ("denied name", facts(target="saga"), "stop", "denied-target"),
        ("vault-adjacent quorum name", facts(target="hlin"), "stop", "denied-target"),
        ("control plane", facts(target="gondul"), "stop", "denied-target"),
        ("PBS", facts(target="pbs"), "stop", "denied-target"),
        ("a name in no class", facts(target="somethingelse"), "stop", "not-in-allow-list"),
        ("caller claims a different class", facts(**{"class": "worker"}), "stop", "class-mismatch"),
        ("state-bearing flag", facts(state_bearing=True), "stop", "hard-limit"),
        ("quorum member flag", facts(quorum_member=True), "stop", "hard-limit"),
        ("agent host flag", facts(agent_host=True), "stop", "hard-limit"),
        # switches and node health
        ("kill switch", facts(flags={"kill_switch": True}), "stop", "kill-switch"),
        ("maintenance suppresses", facts(flags={"maintenance": True}), "skip", "maintenance"),
        ("node offline", facts(node_online=False), "stop", "node-unhealthy"),
        ("node's other guests failing", facts(node_guests_ok=False), "stop", "node-unhealthy"),
        # rate limits, breaker, queue
        ("rebuild breaker open", facts(history={"breaker_open": True}), "stop", "breaker-open"),
        ("second rebuild of the target inside a day", facts(history={"target_day": 1}), "stop", "rate-target-day"),
        ("weekly cap", facts(history={"target_week": 2}), "stop", "rate-target-week"),
        ("class daily cap", facts(history={"class_day": 2}), "stop", "rate-class-day"),
        ("fleet daily cap", facts(history={"fleet_day": 3}), "stop", "rate-fleet-day"),
        ("another rebuild in flight", facts(inflight=1), "skip", "rebuild-in-flight"),
        ("bad inflight fact", facts(inflight=None), "stop", "facts-incomplete"),
        # replicas: peers, leader, backup
        ("dead replica, peers healthy, fresh backup", replica(probes=dead_probes()), "go", "unreachable-agent-silent"),
        ("peers unknown", replica(peers_healthy=None), "stop", "peers-unhealthy"),
        ("peers unhealthy", replica(peers_healthy=False), "stop", "peers-unhealthy"),
        ("peer rebuilt in the last 24 h", replica(history={"peer_rebuilt_24h": True}), "stop", "peer-rebuilt-recently"),
        ("dead replica, no backup known", replica(last_backup_age_hours=None), "stop", "backup-stale"),
        ("dead replica, backup 37 h old", replica(last_backup_age_hours=37), "stop", "backup-stale"),
        ("dead replica, backup 36 h old", replica(last_backup_age_hours=36), "go", "unreachable-agent-silent"),
        ("dead replica, nonsense backup age", replica(last_backup_age_hours=float("nan")), "stop", "backup-stale"),
        ("live VRRP master that is merely broken", replica(probes=[{"t": T0, "ok": True}], restart_breaker_tripped=True, is_leader=True), "stop", "leader-alive"),
        ("dead master is fine to rebuild", replica(is_leader=True), "go", "unreachable-agent-silent"),
        ("broken non-leader replica needs an on-demand backup first", replica(probes=[{"t": T0, "ok": True}], restart_breaker_tripped=True), "go", "restart-breaker-tripped"),
        # who may press the button
        ("auto on a canary while the switch is off", facts(mode="auto"), "skip", "autonomy-rebuild-off"),
        ("auto with the switch on but no enabled policy (all disabled today)", facts(mode="auto", flags={"autonomy_rebuild": True}), "stop", "no-enabled-policy"),
        ("auto on a worker", facts(target="einherjar-urd", mode="auto", flags={"autonomy_rebuild": True}), "stop", "class-approval-only"),
        ("auto on a Tailscale router", facts(target="bifrost", mode="auto", last_backup_age_hours=5, flags={"autonomy_rebuild": True}), "stop", "class-approval-only"),
        ("auto on a replica before two clean approvals", replica(mode="auto", flags={"autonomy_rebuild": True}), "stop", "no-enabled-policy"),
        ("bad mode", facts(mode="yolo"), "stop", "bad-mode"),
        # inputs
        ("facts missing the node fields", {"target": "canary-2", "guest_state": "running", "flags": {}, "history": {}}, "stop", "facts-incomplete"),
        ("no target", facts(target=""), "stop", "no-target"),
    ]

    def test_table(self):
        for label, f, verdict, reason in self.CASES:
            with self.subTest(label):
                v = rebuild.eligible(f, POLICY)
                self.assertEqual((v.verdict, v.reason), (verdict, reason), v.as_dict())

    def test_no_policy_never_eligible(self):
        self.assertEqual(rebuild.eligible(facts(), None).reason, "no-rebuild-policy")

    def test_go_detail(self):
        v = rebuild.eligible(facts(guest_state="missing", probes=[]), POLICY)
        self.assertTrue(v.detail["create"])
        self.assertEqual(v.detail["class"], "canary")
        self.assertNotIn("backup", v.detail)
        self.assertEqual(rebuild.eligible(replica(), POLICY).detail["backup"], "nightly-ok")
        broken = replica(probes=[{"t": T0, "ok": True}], restart_breaker_tripped=True)
        self.assertEqual(rebuild.eligible(broken, POLICY).detail["backup"], "on-demand-first")

    def test_auto_path_with_an_enabled_policy(self):
        data = copy.deepcopy(REGISTRY)
        data["rebuild"]["policies"]["rebuild-dead-canary"]["enabled"] = True
        p = rebuild.RebuildPolicy.from_registry(data)
        self.assertEqual(rebuild.eligible(facts(mode="auto", flags={"autonomy_rebuild": True}), p).verdict, "go")
        self.assertEqual(rebuild.eligible(facts(mode="auto"), p).reason, "autonomy-rebuild-off")

    def test_replica_needs_two_approvals_then_auto(self):
        data = copy.deepcopy(REGISTRY)
        data["rebuild"]["policies"]["rebuild-dead-replica"]["enabled"] = True
        p = rebuild.RebuildPolicy.from_registry(data)
        on = {"autonomy_rebuild": True}
        r = rebuild.eligible(replica(mode="auto", flags=on, history={"clean_rebuilds": 1}), p)
        self.assertEqual((r.verdict, r.reason), ("stop", "approvals-first"))
        self.assertEqual(rebuild.eligible(replica(mode="auto", flags=on, history={"clean_rebuilds": 2}), p).verdict, "go")

    def test_reachability_window(self):
        f = rebuild.unreachable
        self.assertTrue(f(dead_probes(), 3, 600))
        self.assertFalse(f([], 3, 600))
        self.assertFalse(f("x", 3, 600))
        self.assertFalse(f([{"t": 1, "ok": "no"}], 1, 0))  # malformed proves nothing
        self.assertTrue(f(list(reversed(dead_probes())), 3, 600))  # order independent

    def test_operator_approval_does_not_bypass_hard_rules(self):
        for t in ("saga", "pbs", "gondul", "fulla"):
            self.assertEqual(rebuild.eligible(facts(target=t, mode="approval"), POLICY).verdict, "stop")


# --- plan checker ---------------------------------------------------------------------------------------------------

ADDR = 'proxmox_virtual_environment_container.canary["canary-2"]'


def lxc(**o):
    d = {
        "node_name": "urd", "vm_id": 1191, "description": "x",
        "initialization": [{"hostname": "canary-2", "ip_config": [{"ipv4": [{"address": "10.0.11.191/24", "gateway": "10.0.11.1"}]}]}],
        "network_interface": [{"name": "eth0", "vlan_id": 11}],
        "operating_system": [{"template_file_id": "local:vztmpl/debian-13.tar.zst", "type": "debian"}],
    }
    d.update(o)
    return d


def rc(addr=ADDR, actions=("delete", "create"), before=None, after=None, type_="proxmox_virtual_environment_container", unknown=None):
    return {"address": addr, "type": type_, "change": {"actions": list(actions), "before": before, "after": after, "after_unknown": unknown or {}}}


def plan(*changes, **extra):
    return {"format_version": "1.2", "resource_changes": list(changes), **extra}


EXPECT = {"address": ADDR, "type": "proxmox_virtual_environment_container",
          "identity": {"name": "canary-2", "vmid": 1191, "node": "urd", "ip": "10.0.11.191/24", "vlan": 11}}


class PlanCheckTests(unittest.TestCase):
    def test_clean_replace(self):
        p = plan(rc(before=lxc(), after=lxc()), rc("random_password.canary_root[\"canary-2\"]", ["no-op"], {}, {}, "random_password"))
        self.assertEqual(rebuild.check_plan(p, EXPECT), [])

    def test_clean_replace_either_action_order(self):
        self.assertEqual(rebuild.check_plan(plan(rc(actions=("create", "delete"), before=lxc(), after=lxc())), EXPECT), [])

    def test_create_of_a_missing_guest(self):
        p = plan(rc(actions=("create",), before=None, after=lxc()))
        self.assertEqual(rebuild.check_plan(p, EXPECT), [])

    def test_create_with_deleted_behind_our_back_drift_on_the_target_only(self):
        p = plan(rc(actions=("create",), before=None, after=lxc()), resource_drift=[{"address": ADDR, "change": {"actions": ["delete"]}}])
        self.assertEqual(rebuild.check_plan(p, EXPECT), [])

    def test_extra_resource_change(self):
        p = plan(rc(before=lxc(), after=lxc()), rc('proxmox_virtual_environment_container.canary["canary-3"]', ["update"], lxc(), lxc()))
        probs = rebuild.check_plan(p, EXPECT)
        self.assertTrue(any("another resource" in x and "canary-3" in x for x in probs), probs)

    def test_destroy_of_something_else(self):
        p = plan(rc(before=lxc(), after=lxc()), rc("proxmox_virtual_environment_container.pbs", ["delete"], lxc(), None))
        probs = rebuild.check_plan(p, EXPECT)
        self.assertTrue(any("destroys another resource" in x for x in probs), probs)

    def test_wrong_address(self):
        p = plan(rc('proxmox_virtual_environment_container.canary["canary-3"]', before=lxc(), after=lxc()))
        probs = rebuild.check_plan(p, EXPECT)
        self.assertTrue(any("does not touch the expected address" in x for x in probs), probs)
        self.assertTrue(any("another resource" in x for x in probs), probs)

    def test_identity_changes(self):
        cases = {
            "node": lxc(node_name="verd"),
            "vmid": lxc(vm_id=1195),
            "name": lxc(initialization=[{"hostname": "canary-9", "ip_config": [{"ipv4": [{"address": "10.0.11.191/24"}]}]}]),
            "ip": lxc(initialization=[{"hostname": "canary-2", "ip_config": [{"ipv4": [{"address": "10.0.11.99/24"}]}]}]),
            "vlan": lxc(network_interface=[{"name": "eth0", "vlan_id": 21}]),
            "template": lxc(operating_system=[{"template_file_id": "local:vztmpl/other.tar.zst"}]),
        }
        for attr, after in cases.items():
            with self.subTest(attr):
                probs = rebuild.check_plan(plan(rc(before=lxc(), after=after)), EXPECT)
                self.assertTrue(any(f"identity attribute {attr} changes" in x for x in probs), probs)

    def test_identity_must_match_the_registry_on_create(self):
        probs = rebuild.check_plan(plan(rc(actions=("create",), before=None, after=lxc(vm_id=1192))), EXPECT)
        self.assertTrue(any("identity vmid" in x for x in probs), probs)

    def test_unknown_after_apply_attributes_are_not_compared(self):
        after = lxc()
        del after["vm_id"]
        p = plan(rc(before=lxc(), after=after, unknown={"vm_id": True}))
        self.assertEqual(rebuild.check_plan(p, {"address": ADDR}), [])

    def test_vm_shape(self):
        vm = {"node_name": "urd", "vm_id": 2101, "name": "einherjar-urd", "clone": [{"vm_id": 10006}],
              "initialization": [{"ip_config": [{"ipv4": [{"address": "10.0.21.21/24"}]}]}], "network_device": [{"vlan_id": 21}]}
        addr = 'proxmox_virtual_environment_vm.worker["einherjar-urd"]'
        ok = plan(rc(addr, before=vm, after=vm, type_="proxmox_virtual_environment_vm"))
        self.assertEqual(rebuild.check_plan(ok, {"address": addr, "identity": {"name": "einherjar-urd", "vmid": 2101, "template": 10006}}), [])
        moved = dict(vm, clone=[{"vm_id": 10002}])
        probs = rebuild.check_plan(plan(rc(addr, before=vm, after=moved, type_="proxmox_virtual_environment_vm")), {"address": addr})
        self.assertTrue(any("identity attribute template" in x for x in probs), probs)

    def test_wrong_type(self):
        probs = rebuild.check_plan(plan(rc(before=lxc(), after=lxc())), dict(EXPECT, type="proxmox_virtual_environment_vm"))
        self.assertTrue(any("resource type" in x for x in probs), probs)

    def test_destroy_only(self):
        probs = rebuild.check_plan(plan(rc(actions=("delete",), before=lxc(), after=None)), EXPECT)
        self.assertTrue(any("not replace or create" in x for x in probs), probs)

    def test_in_place_update(self):
        probs = rebuild.check_plan(plan(rc(actions=("update",), before=lxc(), after=lxc())), EXPECT)
        self.assertTrue(any("not replace or create" in x for x in probs), probs)

    def test_noop_and_empty(self):
        self.assertTrue(rebuild.check_plan(plan(rc(actions=("no-op",), before=lxc(), after=lxc())), EXPECT))
        self.assertTrue(rebuild.check_plan(plan(), EXPECT))

    def test_drift_on_another_resource(self):
        p = plan(rc(before=lxc(), after=lxc()), resource_drift=[{"address": 'proxmox_virtual_environment_container.canary["canary-1"]'}])
        self.assertTrue(any("resource drift" in x for x in rebuild.check_plan(p, EXPECT)))

    def test_errored_and_malformed(self):
        self.assertTrue(any("errored" in x for x in rebuild.check_plan(plan(rc(before=lxc(), after=lxc()), errored=True), EXPECT)))
        self.assertTrue(rebuild.check_plan([], EXPECT))
        self.assertTrue(rebuild.check_plan({"resource_changes": "x"}, EXPECT))
        self.assertTrue(rebuild.check_plan(plan(), {}))

    def test_hostile_address_text_is_sanitised_in_problems(self):
        bad = 'x.y["a\n@everyone`rm`"]'
        probs = rebuild.check_plan(plan(rc(before=lxc(), after=lxc()), rc(bad, ["delete"], {}, None)), EXPECT)
        joined = "\n".join(probs)
        self.assertNotIn("@", joined)
        self.assertNotIn("`", joined)
        self.assertEqual(len(joined.splitlines()), len(probs))


# --- worker data manifest -------------------------------------------------------------------------------------------


def pv(name, ns, claim, node=None, sc="local-path", size="10Gi", csi=None):
    spec = {"storageClassName": sc, "capacity": {"storage": size}, "claimRef": {"namespace": ns, "name": claim}}
    if node:
        spec["nodeAffinity"] = {"required": {"nodeSelectorTerms": [{"matchExpressions": [{"key": "kubernetes.io/hostname", "operator": "In", "values": [node]}]}]}}
    if csi:
        spec["csi"] = {"driver": csi}
    return {"metadata": {"name": name}, "spec": spec}


def pod(name, ns, claims, node, labels=None):
    return {"metadata": {"name": name, "namespace": ns, "labels": labels or {}},
            "spec": {"nodeName": node, "volumes": [{"persistentVolumeClaim": {"claimName": c}} for c in claims]}}


def lst(*items):
    return {"items": list(items)}


NODE = "einherjar-urd"
VAULT = {"app.kubernetes.io/name": "vault"}


class ManifestTests(unittest.TestCase):
    def test_vault_member_is_replicated_and_never_blocks(self):
        m = rebuild.worker_data_manifest(lst(pv("pv1", "vault", "data-vault-0", NODE)), lst(pod("vault-0", "vault", ["data-vault-0"], NODE, VAULT)), None, NODE)
        self.assertEqual(m["local_path"][0]["kind"], "replicated")
        self.assertFalse(m["blocks"])
        self.assertIn("Raft resyncs", m["summary"])

    def test_single_instance_without_backup_blocks(self):
        pvs, pods = lst(pv("pv2", "apps", "db", NODE)), lst(pod("app-0", "apps", ["db"], NODE))
        for age in (None, 30, float("nan"), True, -1):
            with self.subTest(age=age):
                m = rebuild.worker_data_manifest(pvs, pods, age, NODE)
                self.assertTrue(m["blocks"], m)
                self.assertEqual(m["local_path"][0]["kind"], "single-instance")
                self.assertIn("BLOCKED", m["summary"])

    def test_single_instance_with_recent_backup_is_clean(self):
        pvs, pods = lst(pv("pv2", "apps", "db", NODE)), lst(pod("app-0", "apps", ["db"], NODE))
        for age in (0, 23.9, 24):
            m = rebuild.worker_data_manifest(pvs, pods, age, NODE)
            self.assertFalse(m["blocks"], m)
        self.assertIn("SINGLE-INSTANCE", m["summary"])
        self.assertIn("Manifest clean", m["summary"])

    def test_pv_with_no_consumer_is_conservatively_single_instance(self):
        m = rebuild.worker_data_manifest(lst(pv("pv3", "apps", "orphan", NODE)), lst(), None, NODE)
        self.assertEqual((m["local_path"][0]["kind"], m["local_path"][0]["owner"], m["blocks"]), ("single-instance", "none", True))

    def test_pending_vault_pod_on_another_node_still_marks_replicated(self):
        m = rebuild.worker_data_manifest(lst(pv("pv1", "vault", "data-vault-0", NODE)), lst(pod("vault-0", "vault", ["data-vault-0"], "", VAULT)), None, NODE)
        self.assertEqual(m["local_path"][0]["kind"], "replicated")

    def test_other_nodes_pvs_are_ignored(self):
        m = rebuild.worker_data_manifest(lst(pv("pv4", "apps", "x", "einherjar-verd")), lst(pod("p", "apps", ["x"], "einherjar-verd")), None, NODE)
        self.assertEqual((m["local_path"], m["blocks"]), ([], False))
        self.assertIn("no local-path volumes", m["summary"])

    def test_iscsi_pvcs_are_listed_not_blocking(self):
        iscsi = pv("pv5", "authentik", "pg", sc="synology-csi-iscsi-retain-vol2", csi="csi.san.synology.com")
        other = pv("pv6", "x", "elsewhere", sc="synology-csi-iscsi-retain-vol2", csi="csi.san.synology.com")
        pods = lst(pod("pg-0", "authentik", ["pg"], NODE), pod("e-0", "x", ["elsewhere"], "einherjar-verd"))
        m = rebuild.worker_data_manifest(lst(iscsi, other), pods, None, NODE)
        self.assertEqual([e["pvc"] for e in m["iscsi"]], ["pg"])
        self.assertFalse(m["blocks"])
        self.assertIn("iSCSI volumes that detach: authentik/pg", m["summary"])

    def test_hostile_text_is_sanitised(self):
        evil = "evil\n@everyone `curl x|sh` **bold** [x](http://e)"
        pvs = lst(pv("pv\x1b[31m", evil, evil, NODE))
        pods = lst(pod(evil, evil, [evil], NODE, {"app.kubernetes.io/name": evil}))
        m = rebuild.worker_data_manifest(pvs, pods, None, evil)
        text = m["summary"]
        for bad in ("@", "`", "*", "[", "(", "\x1b", " | "):
            self.assertNotIn(bad, text)
        self.assertNotIn("\n@", text)
        for line in text.splitlines():
            self.assertLess(len(line), 200)

    def test_large_manifest_is_bounded(self):
        pvs = lst(*[pv(f"pv{i}", "a", f"c{i}", NODE) for i in range(40)])
        m = rebuild.worker_data_manifest(pvs, lst(), 1, NODE)
        self.assertLessEqual(len(m["summary"].splitlines()), 20)
        self.assertIn("more local-path volume", m["summary"])

    def test_garbage_input(self):
        m = rebuild.worker_data_manifest(None, "x", None, NODE)
        self.assertEqual((m["local_path"], m["iscsi"], m["blocks"]), ([], [], False))


# --- registry + lint ------------------------------------------------------------------------------------------------


class RegistryAndLintTests(unittest.TestCase):
    def reg(self):
        return copy.deepcopy(REGISTRY)

    def lint(self, reg):
        return lint.check_rebuild(reg, RUNBOOKS)

    def test_repository_registry_is_clean(self):
        self.assertEqual(self.lint(self.reg()), [])
        self.assertEqual(lint.run(), [])

    def test_schema_validates_and_rejects_bad_rebuild(self):
        schema = lint.load_schema("actions.v1.schema.json")
        self.assertEqual(lint.schema_errors(self.reg(), schema), [])
        bad = self.reg()
        bad["rebuild"]["autonomy_rebuild_default"] = True
        self.assertTrue(lint.schema_errors(bad, schema))
        bad = self.reg()
        del bad["rebuild"]["deny"]
        self.assertTrue(lint.schema_errors(bad, schema))
        bad = self.reg()
        bad["rebuild"]["classes"]["canary"]["hosts"]["canary-1"]["vmid"] = "x"
        self.assertTrue(lint.schema_errors(bad, schema))

    def test_policy_loads(self):
        self.assertFalse(POLICY.autonomy_rebuild_default)
        self.assertEqual(POLICY.limits.breaker_failures, 1)
        self.assertEqual(POLICY.class_of("mimir").name, "adguard-replica")
        self.assertEqual(POLICY.vmid_of("canary-3"), 1192)
        self.assertIsNone(POLICY.class_of("saga"))
        self.assertIsNone(rebuild.RebuildPolicy.from_registry({"actions": {}}))
        self.assertTrue(all(not p.enabled for p in POLICY.policies.values()), "no policy may be enabled in this slice")

    def test_a_control_plane_node_in_any_list_fails(self):
        for cname in ("canary", "adguard-replica", "worker"):
            with self.subTest(cname):
                r = self.reg()
                r["host_tiers"]["T2"].append("gondul")
                r["rebuild"]["classes"][cname]["hosts"]["gondul"] = {"vmid": 2001, "node": "urd"}
                errs = self.lint(r)
                self.assertTrue(any("control-plane" in e for e in errs), errs)
                self.assertTrue(any("deny list" in e for e in errs), errs)

    def test_hostname_only_cp_is_caught_without_the_vmid(self):
        r = self.reg()
        r["host_tiers"]["T2"].append("rota")
        r["rebuild"]["classes"]["worker"]["hosts"]["rota"] = {"vmid": 3999, "node": "urd"}
        self.assertTrue(any("control-plane" in e for e in self.lint(r)))

    def test_quorum_members_cannot_be_added(self):
        for name, vmid in (("fulla", 1130), ("hlin", 1133)):
            r = self.reg()
            r["host_tiers"]["T1"].append(name)
            r["rebuild"]["classes"]["adguard-replica"]["hosts"][name] = {"vmid": vmid, "node": "skuld"}
            self.assertTrue(any("deny list" in e for e in self.lint(r)), name)

    def test_deny_list_cannot_shrink(self):
        r = self.reg()
        r["rebuild"]["deny"]["names"].remove("fulla")
        r["rebuild"]["deny"]["vmids"].remove(1101)
        errs = self.lint(r)
        self.assertTrue(any("'fulla'" in e for e in errs), errs)
        self.assertTrue(any("1101" in e for e in errs), errs)

    def test_pbs_is_never_a_target(self):
        r = self.reg()
        r["host_tiers"]["T1"].append("pbs")
        r["rebuild"]["classes"]["adguard-replica"]["hosts"]["pbs"] = {"vmid": 1101, "node": "skuld"}
        errs = self.lint(r)
        self.assertTrue(any("PBS is never rebuilt" in e for e in errs), errs)

    def test_canaries_only_in_stage_a(self):
        r = self.reg()
        r["rebuild"]["classes"]["adguard-replica"]["hosts"]["canary-1"] = {"vmid": 1190, "node": "urd"}
        self.assertTrue(any("belongs only in stage A" in e for e in self.lint(r)))
        r = self.reg()
        r["rebuild"]["classes"]["canary"]["hosts"]["mimir"] = {"vmid": 1111, "node": "verd"}
        errs = self.lint(r)
        self.assertTrue(any("must be a canary-N guest on urd" in e for e in errs), errs)

    def test_canary_must_stay_on_urd(self):
        r = self.reg()
        r["rebuild"]["classes"]["canary"]["hosts"]["canary-1"]["node"] = "skuld"
        self.assertTrue(any("on urd" in e for e in self.lint(r)))

    def test_only_stage_a_may_be_auto(self):
        r = self.reg()
        r["rebuild"]["stages"]["C"]["autonomy"] = "auto"
        self.assertTrue(any("only stage A" in e for e in self.lint(r)))

    def test_unknown_host_and_breaker(self):
        r = self.reg()
        r["rebuild"]["classes"]["canary"]["hosts"]["canary-9"] = {"vmid": 1199, "node": "urd"}
        self.assertTrue(any("not in host_tiers" in e for e in self.lint(r)))
        r = self.reg()
        r["rebuild"]["limits"]["breaker_failures"] = 3
        self.assertTrue(any("breaker_failures must be 1" in e for e in self.lint(r)))

    def test_action_tiers_pinned(self):
        r = self.reg()
        r["actions"]["rebuild-worker"]["max_autonomy"] = "auto"
        self.assertTrue(any("rebuild-worker must be T2" in e for e in self.lint(r)))
        r = self.reg()
        del r["actions"]["rebuild-verify"]
        self.assertTrue(any("rebuild-verify is missing" in e for e in self.lint(r)))

    def test_enabled_policy_rules(self):
        r = self.reg()
        r["rebuild"]["policies"]["rebuild-dead-canary"]["enabled"] = True
        errs = self.lint(r)  # rebuild-guest caps at approval, so an enabled policy is refused until a PR raises it
        self.assertTrue(any("needs auto" in e for e in errs), errs)
        self.assertFalse(any("is not in runbooks.yml" in e for e in errs), errs)  # RB-GUEST-DEAD exists since 10g slice C
        r = self.reg()
        r["rebuild"]["policies"]["rebuild-worker-auto"] = {"enabled": True, "action": "rebuild-worker", "class": "worker", "runbook": "RB-GUEST-DEAD",
                                                           "layers": ["host"], "min_confidence": "high", "precheck": "guest-dead"}
        errs = self.lint(r)
        self.assertTrue(any("only start-guest and rebuild-guest" in e for e in errs), errs)
        self.assertTrue(any("approval-only" in e for e in errs), errs)

    def test_planned_actions_are_never_applied(self):
        r = self.reg()
        r["actions"]["rebuild-worker"]["semaphore"]["applied"] = True
        self.assertTrue(any("planned needs applied: false" in e for e in lint.check_actions(r, REPO)))
        for name in rebuild_actions():
            sem = REGISTRY["actions"][name]["semaphore"]
            self.assertFalse(sem.get("planned") and sem["applied"], name)  # never both planned and applied
            self.assertEqual(bool(sem.get("planned")), name == "rebuild-worker", name)

    def test_a_runner_only_action_needs_runner_steps_and_cannot_be_planned(self):
        r = self.reg()
        self.assertEqual([e for e in lint.check_actions(r, REPO) if "runner_only" in e], [])
        r["actions"]["rebuild-plan"]["steps"] = [{"name": "plan", "backend": "semaphore"}]
        self.assertTrue(any("runner_only needs steps that all use backend: runner" in e for e in lint.check_actions(r, REPO)))
        r = self.reg()
        r["actions"]["rebuild-plan"]["semaphore"]["planned"] = True
        self.assertTrue(any("runner_only cannot also be planned" in e for e in lint.check_actions(r, REPO)))

    def test_start_guest_auto_ceiling_needs_a_policy(self):
        r = self.reg()
        r["rebuild"]["policies"] = {k: v for k, v in r["rebuild"]["policies"].items() if v["action"] != "start-guest"}
        self.assertTrue(any("start-guest" in e and "policy covers" in e for e in lint.check_actions(r, REPO)))

    def test_engine_still_loads_the_registry_and_refuses_unapplied(self):
        import actions

        reg = actions.Registry(REGISTRY)
        for name in rebuild_actions():
            clean, probs = reg.validate_params(name, {"target": "canary-2", **({"plan_hash": "0" * 64} if name in ("rebuild-guest", "rebuild-worker") else {})})
            if name == "rebuild-worker":
                clean, probs = reg.validate_params(name, {"target": "einherjar-urd", "plan_hash": "0" * 64})
            self.assertEqual(probs, [], name)
            # only rebuild-worker (stage C, no playbook yet) is still refused by the applied gate; the canary-stage four are
            # applied since 2026-10-03 (rebuild-plan is runner-only: it needs no Semaphore template)
            gated = any("not applied" in p for p in reg.guard(name, clean))
            self.assertEqual(gated, name == "rebuild-worker", name)
        self.assertIsNotNone(reg.autonomy)


def rebuild_actions():
    return ("rebuild-plan", "rebuild-guest", "rebuild-worker", "rebuild-verify", "start-guest")


if __name__ == "__main__":
    unittest.main()
