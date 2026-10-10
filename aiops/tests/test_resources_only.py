"""Phase 10i4: `.github/scripts/ci-resources-only.py`, the gate that lets a rightsizing PR change container resources and nothing else.
Runs against throwaway git repositories, in both of its modes (the staged index, as the dispatcher uses it; two commits, as CI does)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ci_resources_only", REPO / ".github" / "scripts" / "ci-resources-only.py")
cro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cro)

HR = """\
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: netbox
  namespace: netbox
spec:
  chart:
    spec:
      chart: netbox
      version: "1.2.3"
  values:
    image:
      tag: v4.1.0
    proxy:
      resourcesPreset: medium
    server:
      resources:
        requests:
          cpu: 500m
          memory: 1Gi
        limits:
          memory: 2Gi
    worker:
      resources:
        requests:
          cpu: 100m
          memory: 256Mi
        limits:
          cpu: 500m
          memory: 512Mi
    sidecar:
      resources:
        requests:
          memory: 16Mi
    env:
      - {name: A, value: "1"}
      - {name: B, value: "2"}
"""


def sub(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


class Rig:
    def __init__(self, files: dict | None = None):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        for name, text in (files or {"app/helmrelease.yaml": HR}).items():
            self.write(name, text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *a):
        p = subprocess.run(["git", "-C", str(self.root), *a], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        return p.stdout

    def write(self, name, text):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def edit(self, name="app/helmrelease.yaml", **kw):
        text = (self.root / name).read_text()
        for old, new in kw.get("subs", []):
            text = sub(text, old, new)
        self.write(name, text)

    def verdict(self, mode="index"):
        """The violations, from the staged index and again from two commits: they must agree."""
        self.git("add", "-A")
        staged = cro.check(self.root, "HEAD", "INDEX")
        self.git("commit", "-q", "-m", "change", "--allow-empty")
        committed = cro.check(self.root, self.base, "HEAD")
        assert staged == committed, (staged, committed)
        return staged

    def close(self):
        self.tmp.cleanup()


class Verdicts(unittest.TestCase):
    def run_case(self, subs=(), files=None, rig_files=None):
        r = Rig(rig_files)
        self.addCleanup(r.close)
        for name, text in (files or {}).items():
            r.write(name, text)
        r.edit(subs=list(subs)) if subs else None
        return r.verdict()

    def test_resources_values_may_change(self):
        self.assertEqual(self.run_case([("cpu: 500m\n          memory: 1Gi", "cpu: 50m\n          memory: 640Mi"), ("memory: 2Gi", "memory: 1Gi")]), [])

    def test_a_type_change_in_a_quantity_is_still_just_a_value(self):
        self.assertEqual(self.run_case([("cpu: 500m\n          memory: 1Gi", "cpu: 1\n          memory: 1Gi")]), [])

    def test_a_preset_can_become_none_beside_an_explicit_block_and_nothing_else(self):
        ok = [("      resourcesPreset: medium\n", '      resourcesPreset: "none"\n      resources:\n        requests:\n          cpu: 50m\n          memory: 128Mi\n        limits:\n          memory: 256Mi\n')]
        self.assertEqual(self.run_case(ok), [])
        bad = self.run_case([("resourcesPreset: medium", "resourcesPreset: small")])
        self.assertTrue(any("resourcesPreset" in b for b in bad), bad)
        # "none" with no explicit resources next to it is not a rightsizing change
        bad = self.run_case([("resourcesPreset: medium", 'resourcesPreset: "none"')])
        self.assertTrue(any("explicit `resources:` block" in b for b in bad), bad)

    def test_a_whole_resources_block_may_be_added(self):
        base = HR.replace("    sidecar:\n      resources:\n        requests:\n          memory: 16Mi\n", "    sidecar:\n      name: x\n")
        r = Rig({"app/helmrelease.yaml": base})
        self.addCleanup(r.close)
        r.edit(subs=[("    sidecar:\n      name: x\n", "    sidecar:\n      name: x\n      resources:\n        requests:\n          memory: 64Mi\n          cpu: 20m\n")])
        self.assertEqual(r.verdict(), [])

    def test_an_image_tag_change_is_the_probe_that_must_fail_even_with_a_resources_change_beside_it(self):
        bad = self.run_case([("tag: v4.1.0", "tag: v4.2.0"), ("memory: 1Gi", "memory: 640Mi")])
        self.assertEqual(len(bad), 1)
        self.assertIn("image.tag", bad[0])

    def test_other_keys_chart_versions_and_new_keys_fail(self):
        self.assertTrue(any("version" in b for b in self.run_case([('version: "1.2.3"', 'version: "1.2.4"')])))
        self.assertTrue(any("was added" in b for b in self.run_case([("    env:\n", "    replicaCount: 3\n    env:\n")])))

    def test_removing_anything_or_changing_a_list_fails(self):
        self.assertTrue(any("was removed" in b for b in self.run_case([("        limits:\n          memory: 2Gi\n", "")])))
        self.assertTrue(any("changed shape" in b for b in self.run_case([("      - {name: B, value: \"2\"}\n", "")])))
        self.assertTrue(any("changed (" in b for b in self.run_case([('value: "1"', 'value: "9"')])))

    def test_a_cpu_limit_is_never_added_or_changed_but_an_existing_one_may_stay(self):
        added = self.run_case([("          memory: 2Gi", "          memory: 2Gi\n          cpu: 1")])
        self.assertTrue(any("CPU limit" in b for b in added), added)
        changed = self.run_case([("cpu: 500m\n          memory: 512Mi", "cpu: 800m\n          memory: 512Mi")])   # worker.limits.cpu (the second block with 500m)
        self.assertTrue(any("CPU limit" in b or "limits.cpu" in b for b in changed), changed)
        self.assertEqual(self.run_case([("memory: 512Mi", "memory: 384Mi")]), [])   # the existing worker CPU limit is untouched

    def test_a_memory_limit_below_its_request_fails(self):
        bad = self.run_case([("memory: 2Gi", "memory: 512Mi")])
        self.assertTrue(any("limit is below its request" in b for b in bad), bad)

    def test_floors_apply_to_changed_blocks_only(self):
        self.assertTrue(any("32 MiB floor" in b for b in self.run_case([("memory: 256Mi", "memory: 20Mi"), ("memory: 512Mi", "memory: 20Mi")])))
        self.assertTrue(any("10m floor" in b for b in self.run_case([("cpu: 100m", "cpu: 5m")])))
        # the sidecar's 16Mi request is below the floor but this change does not touch it
        self.assertEqual(self.run_case([("cpu: 500m\n          memory: 1Gi", "cpu: 400m\n          memory: 1Gi")]), [])

    def test_quantities_must_be_plain(self):
        bad = self.run_case([("memory: 1Gi", "memory: lots")])
        self.assertTrue(any("plain Kubernetes quantity" in b for b in bad), bad)

    def test_only_modified_yaml_files_are_allowed(self):
        bad = self.run_case(files={"app/new.yaml": "a: 1\n"})
        self.assertTrue(any("only modifies existing files" in b for b in bad), bad)
        r = Rig({"app/helmrelease.yaml": HR, "app/README.md": "hi\n"})
        self.addCleanup(r.close)
        r.write("app/README.md", "changed\n")
        self.assertTrue(any("not a YAML file" in b for b in r.verdict()))
        r2 = Rig()
        self.addCleanup(r2.close)
        (r2.root / "app/helmrelease.yaml").unlink()
        self.assertTrue(any("only modifies existing files" in b for b in r2.verdict()))

    def test_an_empty_change_and_broken_yaml_say_so(self):
        r = Rig()
        self.addCleanup(r.close)
        self.assertEqual(cro.check(r.root, "HEAD", "INDEX"), ["no changed files: there is nothing for a rightsizing PR to say"])
        r.write("app/helmrelease.yaml", "a: [unclosed\n")
        r.git("add", "-A")
        self.assertTrue(any("does not parse" in b for b in cro.check(r.root, "HEAD", "INDEX")))

    def test_multi_document_files_are_judged_document_by_document(self):
        two = HR + "---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\ndata:\n  k: v\n"
        ok = self.run_case([("memory: 1Gi", "memory: 640Mi")], rig_files={"app/helmrelease.yaml": two})
        self.assertEqual(ok, [])
        r = Rig({"app/helmrelease.yaml": two})
        self.addCleanup(r.close)
        r.edit(subs=[("  k: v", "  k: w")])
        self.assertTrue(any("(document 2)" in b for b in r.verdict()))

    def test_the_floors_come_from_the_base_commits_rightsizing_yml(self):
        yml = "floors:\n  cpu_millicores: 50\n  memory_mib: 128\n"
        bad = self.run_case([("memory: 256Mi", "memory: 100Mi"), ("memory: 512Mi", "memory: 300Mi")], rig_files={"app/helmrelease.yaml": HR, "aiops/rightsizing.yml": yml})
        self.assertTrue(any("128 MiB floor" in b for b in bad), bad)


class Cli(unittest.TestCase):
    def test_exit_codes_and_output(self):
        r = Rig()
        self.addCleanup(r.close)
        r.edit(subs=[("memory: 1Gi", "memory: 640Mi")])
        r.git("add", "-A")
        self.assertEqual(cro.main(["--root", str(r.root)]), 0)
        r.edit(subs=[("tag: v4.1.0", "tag: v9")])
        r.git("add", "-A")
        self.assertEqual(cro.main(["--root", str(r.root)]), 1)
        self.assertEqual(cro.main(["--root", "/nonexistent-dir-for-test"]), 2)

    def test_the_script_runs_as_the_dispatcher_runs_it(self):
        """No arguments, bare environment, cwd = the patched clone."""
        r = Rig()
        self.addCleanup(r.close)
        r.edit(subs=[("memory: 1Gi", "memory: 640Mi")])
        r.git("add", "-A")
        p = subprocess.run([sys.executable, str(REPO / ".github/scripts/ci-resources-only.py")], cwd=r.root, env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "HOME": "/nonexistent"},
                           capture_output=True, text=True)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "resources-only: ok"), p.stderr)


class Quantities(unittest.TestCase):
    def test_parsing(self):
        self.assertEqual(cro.quantity("512Mi"), 512 * 1024**2)
        self.assertEqual(cro.quantity("250m"), 0.25)
        self.assertEqual(cro.quantity(1), 1.0)
        self.assertEqual(cro.quantity("1.5Gi"), 1.5 * 1024**3)
        for bad in ("lots", "", "1Zi", "-1", True, None, "12 Mi"):
            self.assertIsNone(cro.quantity(bad), bad)


if __name__ == "__main__":
    unittest.main()
