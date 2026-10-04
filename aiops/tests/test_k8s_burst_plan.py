"""10h burst-cluster test, slice 1: the planning logic in aiops/runner/k8s_burst_plan.py, against both a tiny fixture tree and the real repo."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "runner"))

import k8s_burst_plan as kb  # noqa: E402

ES = """\
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata: {name: app, namespace: demo}
spec:
  secretStoreRef: {name: vault, kind: ClusterSecretStore}
  data:
    - secretKey: a
      remoteRef: {key: k8s/demo/app, property: token}
    - secretKey: b
      remoteRef: {key: k8s/demo/app, property: password}
  dataFrom:
    - extract: {key: k8s/demo/whole}
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata: {name: other, namespace: demo}
spec:
  secretStoreRef: {name: not-vault, kind: ClusterSecretStore}
  data:
    - secretKey: x
      remoteRef: {key: elsewhere/x, property: y}
"""
SS = """\
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata: {name: sealed-thing, namespace: demo}
spec:
  encryptedData: {B_KEY: AgAAAA, A_KEY: AgBBBB}
  template: {metadata: {name: sealed-thing, namespace: demo}, type: kubernetes.io/basic-auth}
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        d = Path(self.t.name) / "k8s" / "asgard" / "apps" / "demo"
        d.mkdir(parents=True)
        (d / "externalsecret.yaml").write_text(ES)
        (d / "sealed.yaml").write_text(SS)
        (d / "broken.yaml").write_text("key: [unclosed")        # a file that does not parse must not stop the scan
        self.s = kb.scan(self.t.name)

    def test_scan_finds_both_kinds_and_survives_a_broken_file(self):
        self.assertEqual([e["name"] for e in self.s["externalsecrets"]], ["app", "other"])
        self.assertEqual(self.s["sealedsecrets"][0]["keys"], ["A_KEY", "B_KEY"])

    def test_a_key_with_the_kv_mount_prefix_is_the_same_secret_as_without_it(self):
        self.assertEqual(kb._key("secret/k8s/x/y"), "k8s/x/y")
        self.assertEqual(kb._key("k8s/x/y"), "k8s/x/y")
        self.assertEqual(kb._key("/secret/k8s/x/y"), "k8s/x/y")

    def test_the_seed_plan_covers_every_vault_reference_and_only_the_vault_store(self):
        plan = kb.seed_plan(self.s)
        self.assertEqual(plan, {"k8s/demo/app": ["password", "token"], "k8s/demo/whole": ["value"]})
        self.assertNotIn("elsewhere/x", plan)

    def test_unknown_paths_are_reported_against_the_inventory(self):
        plan = kb.seed_plan(self.s)
        self.assertEqual(kb.check_paths(plan, {"k8s/demo/app"}), ["k8s/demo/whole"])
        self.assertEqual(kb.check_paths(plan, set(plan)), [])

    def test_a_sealed_secret_becomes_a_stub_with_the_same_identity_and_key_names(self):
        (stub,) = kb.sealed_stubs(self.s)
        self.assertEqual((stub["namespace"], stub["name"], stub["keys"], stub["type"]), ("demo", "sealed-thing", ["A_KEY", "B_KEY"], "kubernetes.io/basic-auth"))


class Components(unittest.TestCase):
    def test_changed_paths_map_to_components_and_skips_say_why(self):
        out = kb.components(["k8s/asgard/apps/netbox/helmrelease.yaml", "k8s/asgard/infrastructure/traefik/values.yaml", "k8s/asgard/metallb-config/pool.yaml",
                             "k8s/asgard/infrastructure/synology-csi/hr.yaml", "docs/x.md", "k8s/asgard/flux-system/apps.yaml"])
        self.assertEqual(out["touched"], ["netbox", "traefik"])
        self.assertEqual(sorted(out["skipped"]), ["metallb-config", "synology-csi"])
        self.assertEqual(out["other"], ["k8s/asgard/flux-system/apps.yaml"])

    def test_storage_classes_that_only_exist_in_asgard_are_remapped(self):
        text, hit = kb.storage_remap("a: synology-csi-iscsi-retain-vol2\nb: nfs-client\nc: local-path\n")
        self.assertEqual(text, "a: local-path\nb: local-path\nc: local-path\n")
        self.assertEqual(hit, ["nfs-client", "synology-csi-iscsi-retain-vol2"])


class RealRepo(unittest.TestCase):
    """The scanner against what is actually committed: if a manifest shape changes under it, this fails before a burst cluster is built."""

    @classmethod
    def setUpClass(cls):
        cls.s = kb.scan(REPO)
        cls.plan = kb.seed_plan(cls.s)

    def test_the_known_externalsecrets_are_found_and_use_the_vault_store(self):
        names = {(e["namespace"], e["name"]) for e in self.s["externalsecrets"]}
        self.assertIn(("netbox", "netbox-app"), names)
        self.assertGreaterEqual(len(names), 10)
        self.assertTrue(all(e["store"] == "vault" for e in self.s["externalsecrets"]), "an ExternalSecret uses a store the burst Vault does not model")

    def test_every_seed_path_is_under_the_k8s_namespace_of_the_vault_kv(self):
        self.assertIn("k8s/netbox/app", self.plan)
        self.assertEqual(self.plan["k8s/netbox/app"][0], "secret_key")
        self.assertTrue(all(k.startswith("k8s/") for k in self.plan), [k for k in self.plan if not k.startswith("k8s/")])

    def test_the_sealed_secrets_that_exist_are_both_in_skipped_components(self):
        files = {s["file"] for s in self.s["sealedsecrets"]}
        self.assertEqual(files, {"k8s/asgard/synology-csi-config/synology-secret.yaml", "k8s/asgard/vault-config/vault-unseal-secret.yaml"})
        for f in files:
            self.assertIn(kb.components([f])["skipped"].keys().__iter__().__next__(), kb.SKIPPED)


if __name__ == "__main__":
    unittest.main()
