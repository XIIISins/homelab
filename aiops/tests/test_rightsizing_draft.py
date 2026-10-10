"""Phase 10i4: the `rightsizing` author class - the request the Draft button files (aiops/toolbelt/rightsizing_draft.py + the Toolbelt route), the
class in author-classes.yml, the evidence the dispatcher copies into the PR, and the burst-test branch patterns it relies on."""
from __future__ import annotations

import copy
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests", "author", "bot", "runner"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import burst_exec  # noqa: E402
import burst_runner  # noqa: E402
import dispatcher  # noqa: E402
import fcast  # noqa: E402
import pr_test  # noqa: E402
import rightsizing_draft as rdr  # noqa: E402
import rightsizing_watch as rw  # noqa: E402
import scope  # noqa: E402
import test_change_requests as tcr  # noqa: E402
import test_rightsizing_digest as trd  # noqa: E402
import yaml  # noqa: E402

MIB = 1024 * 1024
REAL = scope.load_classes((REPO / "aiops" / "author-classes.yml").read_text())


def f(name, status="modified"):
    return {"filename": name, "status": status, "additions": 2, "deletions": 2}


def row(target, **ev):
    """A forecast row as the store serves it, for `collect`."""
    return {"id": 1, "target": target, "kind": "rightsizing", "evidence": {"evidence": ev}}


def web_rows():
    return [
        row("app/Deployment/web/main", finding="memory-under-request", request_mib=1792.0, limit_mib=2560.0, max_working_set_mib=1621.6, vpa_upper_mib=2364.5, oomkilled=True,
            proposed_request_mib=1952.0, proposed_limit_mib=3840.0, added_bytes=160 * MIB),
        row("app/Deployment/web/main", finding="cpu-over-request", request_millicores=500.0, proposed_request_millicores=100.0, freed_millicores=400.0, median_daily_p95_millicores=60.0),
        row("app/Deployment/web/sidecar", finding="memory-over-request", request_mib=512.0, limit_mib=None, max_working_set_mib=40.0, proposed_request_mib=64.0, freed_bytes=448 * MIB),
        row("app/Deployment/web/leaky", finding="memory-creep", slope_mib_per_day=6.0),
        row("app/Deployment/other/main", finding="memory-over-request", request_mib=512.0, proposed_request_mib=64.0, freed_bytes=448 * MIB),
    ]


class Collect(unittest.TestCase):
    def test_every_numbered_finding_of_the_workload_and_nothing_else(self):
        c = rdr.collect(web_rows(), "app/Deployment/web", held=False)
        self.assertEqual(sorted(c), ["main", "sidecar"])                      # creep has no number; `other` is another workload
        self.assertEqual(c["main"]["old"], {"request_mib": 1792.0, "limit_mib": 2560.0, "request_millicores": 500.0})
        self.assertEqual(c["main"]["new"], {"request_mib": 1952.0, "limit_mib": 3840.0, "request_millicores": 100.0})
        self.assertTrue(c["main"]["why"]["oomkilled"])
        self.assertEqual(c["sidecar"]["new"], {"request_mib": 64.0})

    def test_a_limit_cut_needs_the_held_flag(self):
        rows = [row("app/Deployment/web/main", finding="memory-over-limit", limit_mib=4096.0, proposed_limit_mib=1024.0, freed_bytes=3072 * MIB, max_working_set_mib=300.0)]
        self.assertEqual(rdr.collect(rows, "app/Deployment/web", held=False), {})
        self.assertEqual(rdr.collect(rows, "app/Deployment/web", held=True)["main"]["new"], {"limit_mib": 1024.0})


class Build(unittest.TestCase):
    workers = [{"node": "einherjar-urd", "memory_requested_mib": 11000.0, "memory_allocatable_mib": 14000.0},
               {"node": "einherjar-verd", "memory_requested_mib": 6000.0, "memory_allocatable_mib": 14000.0}]

    def test_title_body_evidence_and_a_spec_the_watch_accepts(self):
        c = rdr.collect(web_rows(), "app/Deployment/web", held=False)
        title, body, spec = rdr.build("app/Deployment/web", c, self.workers, {"einherjar-urd": 2})
        self.assertEqual(title, "Rightsize web")
        self.assertLessEqual(len(body), 4000)
        self.assertEqual(rw.parse_spec(body), spec)
        self.assertEqual(spec["kind"], "trim")
        ev = re.search(r"<!-- evidence -->\n(.*?)\n<!-- /evidence -->", body, re.S).group(1)
        self.assertIn("| `main` | 1792 → 1952 MiB | 2560 → 3840 MiB | 500 → 100m |", ev)
        self.assertIn("OOMKilled", ev)
        # the request cut is 0 for main (a raise) and 448 MiB for sidecar, on two pods on urd: 896 MiB, urd 11000 -> 10104
        self.assertIn("urd 11000 → 10104 MiB (79 % → 72 %)", ev)
        self.assertEqual(spec["freed_mib"], 896.0 - 0.0)
        for rule in ("never a CPU limit", "never an image", "resourcesPreset"):
            self.assertIn(rule, body)

    def test_the_body_stays_inside_the_request_limit_even_for_a_big_workload(self):
        rows = [row(f"app/Deployment/web/c{i:02d}", finding="memory-over-request", request_mib=2048.0, limit_mib=4096.0, max_working_set_mib=100.0, vpa_upper_mib=120.0,
                    proposed_request_mib=160.0, proposed_limit_mib=None, freed_bytes=1888 * MIB) for i in range(18)]
        c = rdr.collect(rows, "app/Deployment/web", held=False)
        _, body, spec = rdr.build("app/Deployment/web", c, self.workers, {"einherjar-urd": 1})
        self.assertLessEqual(len(body), 4000)
        self.assertEqual(len(spec["containers"]), 18)

    def test_a_missing_worker_picture_is_not_an_error(self):
        c = rdr.collect(web_rows(), "app/Deployment/web", held=False)
        _, body, _ = rdr.build("app/Deployment/web", c, [], {})
        self.assertNotIn("Worker memory requested", body)


class Route(unittest.TestCase):
    def classes(self, enabled=True):
        c = copy.deepcopy(tcr.CLASSES)
        c["classes"]["rightsizing"] = copy.deepcopy(REAL["classes"]["rightsizing"])
        c["classes"]["rightsizing"]["enabled"] = enabled
        return c

    def rig(self, enabled=True):
        r = trd.Rig(classes=self.classes(enabled))
        self.addCleanup(r.close)
        return r

    def feed_web(self, r, name="netbox", ns="netbox"):
        return r.feed(trd.rs_finding(name, ns=ns, finding="memory-under-request", request_mib=1792.0, limit_mib=2560.0, max_working_set_mib=1621.6, proposed_request_mib=1952.0,
                                     proposed_limit_mib=None, added_bytes=160 * MIB, oomkilled=False),
                      trd.rs_finding(name, ns=ns, container="worker", finding="memory-over-request", request_mib=1024.0, limit_mib=None, proposed_request_mib=128.0, freed_bytes=896 * MIB))

    def fid(self, r, name="netbox"):
        return next(x["id"] for x in r.tb.fc.list(kind="rightsizing") if f"/{name}/" in x["target"])

    def test_the_button_files_one_request_for_the_whole_workload(self):
        r = self.rig()
        self.feed_web(r)
        st, cr = r.appr("POST", f"/forecasts/{self.fid(r)}/draft", {"by": trd.tcr.OP})
        self.assertEqual(st, 200, cr)
        self.assertEqual((cr["class"], cr["state"], cr["source"], cr["source_ref"]), ("rightsizing", "pending", "forecast", "rightsizing-netbox/Deployment/netbox"))
        self.assertEqual(sorted(rw.parse_spec(cr["body"])["containers"]), ["main", "worker"])
        self.assertIn("<!-- evidence -->", cr["body"])
        self.assertTrue(all(p for p in cr["allowed_paths"]))

    def test_a_second_press_while_the_pr_is_in_flight_is_refused_and_the_card_shows_it(self):
        r = self.rig()
        self.feed_web(r)
        fid = self.fid(r)
        self.assertEqual(r.appr("POST", f"/forecasts/{fid}/draft", {"by": trd.tcr.OP})[0], 200)
        st, out = r.appr("POST", f"/forecasts/{fid}/draft", {"by": trd.tcr.OP})
        self.assertEqual(st, 409)
        self.assertIn("in flight", out["error"])
        fc = r.tb.fc.get(fid)
        self.assertFalse(fc["draftable"])
        self.assertEqual(fc["pr"]["state"], "pending")
        self.assertEqual(fcast.buttons(fc), ["useful", "noise"])

    def test_at_most_three_requests_at_a_time(self):
        r = self.rig()
        r.tb.cfg.rs_max_open_prs = 2
        for ns in ("netbox", "outline", "immich"):
            self.feed_web(r, name=ns, ns=ns) if ns == "netbox" else None
        r.feed(*[trd.rs_finding(n, ns=n, finding="memory-over-request", request_mib=1024.0, proposed_request_mib=128.0, freed_bytes=896 * MIB) for n in ("netbox", "outline", "immich")])
        codes = [r.appr("POST", f"/forecasts/{self.fid(r, n)}/draft", {"by": trd.tcr.OP})[0] for n in ("netbox", "outline", "immich")]
        self.assertEqual(codes, [200, 200, 429])

    def test_only_an_operator_and_only_an_enabled_allow_listed_workload_with_a_number(self):
        r = self.rig()
        self.feed_web(r)
        self.assertEqual(r.appr("POST", f"/forecasts/{self.fid(r)}/draft", {"by": "999"})[0], 403)
        r.feed(trd.rs_finding("vault", ns="vault", kind="StatefulSet", finding="memory-over-request", request_mib=1024.0, proposed_request_mib=128.0, freed_bytes=896 * MIB))
        self.assertEqual(r.appr("POST", f"/forecasts/{self.fid(r, 'vault')}/draft", {"by": trd.tcr.OP})[0], 409)       # not on the allow-list
        off = self.rig(enabled=False)
        self.feed_web(off)
        st, out = off.appr("POST", f"/forecasts/{self.fid(off)}/draft", {"by": trd.tcr.OP})
        self.assertEqual(st, 409)
        self.assertIn("not enabled", out["error"])


class ClassFile(unittest.TestCase):
    def test_the_class_ships_disabled_with_the_check_and_the_burst_test(self):
        c = REAL["classes"]["rightsizing"]
        self.assertIn(c["enabled"], (False, True))
        self.assertTrue(c["burst_test"])
        self.assertEqual([x["script"] for x in c["checks"]], [".github/scripts/ci-resources-only.py"])
        self.assertTrue((REPO / ".github/scripts/ci-resources-only.py").is_file())

    def test_the_paths_cover_helm_and_plain_manifests_and_nothing_wider(self):
        allow = REAL["classes"]["rightsizing"]["allow"]
        for ok in ("k8s/asgard/apps/netbox/helmrelease.yaml", "k8s/asgard/apps/outline/deployment.yaml", "k8s/asgard/apps/semaphore/statefulset.yaml",
                   "k8s/asgard/apps/outline/redis.yaml", "k8s/asgard/infrastructure/authentik/helmrelease.yaml"):
            self.assertTrue(scope.matches(ok, allow), ok)
        for bad in ("k8s/asgard/apps/netbox/externalsecret.yaml", "k8s/asgard/apps/netbox/httproute.yaml", "k8s/asgard/flux-system/kustomization.yaml",
                    "k8s/asgard/infrastructure/vault/helmrelease.yaml", "aiops/rightsizing.yml", ".github/scripts/ci-resources-only.py", "terraform/x.tf"):
            self.assertFalse(scope.matches(bad, allow), bad)
        on = {**REAL, "classes": {**REAL["classes"], "rightsizing": {**REAL["classes"]["rightsizing"], "enabled": True}}}
        self.assertEqual(scope.check(on, "agent/rightsizing/9-trim-netbox", [f("k8s/asgard/apps/netbox/helmrelease.yaml")]), [])
        self.assertTrue(scope.check(on, "agent/rightsizing/9-trim-netbox", [f("k8s/asgard/apps/netbox/httproute.yaml")]))
        self.assertTrue(scope.check(on, "agent/rightsizing/9-x", [f("aiops/rightsizing.yml")]))                       # the policy file is the agent's to read, never to edit
        self.assertTrue(scope.check(on, "agent/rightsizing/9-x", [f("k8s/asgard/apps/x/helmrelease.yaml", status="added")]) == [])   # (new files are the resources-only check's job)

    def test_the_allow_list_of_the_digest_is_inside_the_class(self):
        cfg = yaml.safe_load((REPO / "aiops" / "rightsizing.yml").read_text())
        self.assertTrue(cfg["allow"])


class BurstBranches(unittest.TestCase):
    def test_a_rightsizing_branch_reaches_the_burst_test_and_nothing_stranger_does(self):
        for rx in (burst_exec.BRANCH, burst_runner.BRANCH):
            for ok in ("agent/k8s/21-outline-resources", "agent/rightsizing/9-trim-netbox"):
                self.assertTrue(rx.match(ok), ok)
            for bad in ("agent/docs/3-x", "agent/rightsizing/x-y", "agent/rightsizing/3-UP", "agent/rightsizing/3-x; id", "agent/rightsizing/3-x\n", "agent/other/3-x"):
                self.assertFalse(rx.match(bad), bad)
        self.assertTrue(pr_test.BRANCH.match("agent/rightsizing/9-trim-netbox"))
        reg = yaml.safe_load((REPO / "aiops" / "actions.yml").read_text())["actions"]["pr-burst-test"]
        self.assertEqual(reg["guard"]["pr_scope"]["classes"], ["k8s", "rightsizing"])
        self.assertTrue(re.fullmatch(reg["extra_vars"]["pr_branch"]["pattern"], "agent/rightsizing/9-trim-netbox"))

    def test_an_authentik_pr_is_honestly_not_burst_testable(self):
        fetch = lambda url, timeout=8.0: (200, {"object": {"sha": "a" * 40}} if "git/ref" in url else {"status": "ahead", "ahead_by": 1, "total_commits": 1, "files": [
            {"filename": "k8s/asgard/infrastructure/authentik/helmrelease.yaml", "status": "modified", "patch": "@@\n+x", "additions": 1, "deletions": 1}]})   # noqa: E731
        out = pr_test.inspect_k8s_pr(fetch, "agent/rightsizing/9-trim-authentik", ["k8s", "rightsizing"])
        self.assertFalse(out["ok"])
        self.assertIn("not an app manifest", out["reason"])


class PrBody(unittest.TestCase):
    cr = {"id": 9, "class": "rightsizing", "source": "forecast", "source_ref": "forecast-4", "decided_by": "111", "title": "Rightsize netbox",
          "body": "Do it.\n<!-- evidence -->\n| container | memory request |\n|---|---|\n| `main` | 1792 → 1952 MiB |\n<!-- /evidence -->\nmore"}

    def test_the_evidence_is_copied_verbatim_and_the_rollback_names_flux(self):
        body = dispatcher.pr_body(self.cr, "I changed it.", {"resources only": "pass"}, [{"filename": "k8s/asgard/apps/netbox/helmrelease.yaml", "additions": 2, "deletions": 2}])
        self.assertIn("## Evidence\n| container | memory request |", body)
        self.assertIn("| `main` | 1792 → 1952 MiB |", body)
        self.assertLess(body.index("## Summary"), body.index("## Evidence"))
        self.assertLess(body.index("## Evidence"), body.index("## Files"))
        self.assertIn("`git revert`", body)
        self.assertIn("Flux rolls the workload back", body)
        self.assertIn("resources only: pass", body)

    def test_other_classes_are_unchanged(self):
        body = dispatcher.pr_body({**self.cr, "class": "docs", "body": "plain"}, "s", None, [])
        self.assertNotIn("## Evidence", body)
        self.assertIn("Revert this PR; it only touches the files above.", body)


if __name__ == "__main__":
    unittest.main()
