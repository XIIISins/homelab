"""Phase 10i2: pod rightsizing - the rules (pure), the VictoriaMetrics collection, the `kube.rightsizing` tool, the daily pass in the
forecast job, and the quiet rows in the forecast store."""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))
sys.path.insert(0, str(REPO / "aiops" / "tools"))

import forecast_run  # noqa: E402
import forecast_store  # noqa: E402
import lint  # noqa: E402
import rightsizing as rs  # noqa: E402
import tools  # noqa: E402

MIB = rs.MIB
DAY = rs.DAY
NOW = 1_790_000_000.0
CFG = rs.load_config(REPO / "aiops" / "rightsizing.yml")


def vpa(target, upper, age_days=10, lower=None, drift=0.0):
    return rs.Vpa(lower=lower if lower is not None else target * 0.8, target=target, upper=upper, age_s=age_days * DAY,
                  hi7=target * (1 + drift), lo7=target * (1 - drift))


def ctr(**kw) -> rs.Container:
    """A boring, healthy container: requests match use. Tests override only what they are about."""
    base = dict(namespace="app", kind="Deployment", name="web", container="main", pods=1, youngest_pod_age_s=3 * DAY,
                req_cpu=0.1, req_mem=256 * MIB, lim_cpu=None, lim_mem=512 * MIB, mem_max=200 * MIB, mem_p50=150 * MIB, mem_p95=190 * MIB,
                cpu_p95=0.05, cpu_worst=0.06, oom=False, restarts=0,
                vpa_cpu=vpa(0.05, 0.08), vpa_mem=vpa(190 * MIB, 210 * MIB))
    base.update(kw)
    return rs.Container(**base)


def ev(c, cfg=CFG):
    return rs.evaluate(c, cfg, NOW)


def by_name(found):
    return {f["evidence"]["finding"]: f for f in found}


class Helpers(unittest.TestCase):
    def test_rounding_goes_up_to_the_step(self):
        self.assertEqual(rs.ceil16mi(1 * MIB), 16 * MIB)
        self.assertEqual(rs.ceil16mi(16 * MIB), 16 * MIB)
        self.assertEqual(rs.ceil16mi(16 * MIB + 1), 32 * MIB)
        self.assertEqual(rs.ceil10m(0.011), 0.02)
        self.assertEqual(rs.ceil10m(0.05), 0.05)
        self.assertEqual(rs.ceil10m(0.0501), 0.06)

    def test_patterns_cross_slashes(self):
        self.assertTrue(rs.matches(["immich/*"], "immich/Deployment/immich-server"))
        self.assertTrue(rs.matches(["monitoring/StatefulSet/victoriametrics-*"], "monitoring/StatefulSet/victoriametrics-victoria-metrics-single-server"))
        self.assertFalse(rs.matches(["monitoring/StatefulSet/victoriametrics-*"], "monitoring/Deployment/vmagent"))

    def test_theil_sen_ignores_one_spike_and_sees_a_steady_rise(self):
        steady = [(i * DAY, 100.0 + 2 * i) for i in range(20)]
        slope, rising = rs.theil_sen(steady)
        self.assertAlmostEqual(slope * DAY, 2.0)
        self.assertEqual(rising, 1.0)
        spiky = [(i * DAY, 100.0) for i in range(20)]
        spiky[10] = (10 * DAY, 900.0)
        slope, rising = rs.theil_sen(spiky)
        self.assertAlmostEqual(slope * DAY, 0.0)
        self.assertLess(rising, 0.5)

    def test_vpa_validity_needs_age_stability_and_a_recommendation(self):
        self.assertEqual(rs.vpa_valid(rs.Vpa(), CFG), (False, "no-vpa-recommendation"))
        self.assertEqual(rs.vpa_valid(vpa(1, 2, age_days=3), CFG), (False, "vpa-immature"))
        self.assertEqual(rs.vpa_valid(vpa(1, 2, drift=0.5), CFG), (False, "vpa-unstable"))
        self.assertEqual(rs.vpa_valid(vpa(1, 2, drift=0.25), CFG), (True, ""))


class MemoryRules(unittest.TestCase):
    def test_a_right_sized_container_has_no_findings(self):
        self.assertEqual(ev(ctr()), ([], []))

    def test_under_request_uses_the_30_day_max_and_raises_the_limit_when_it_is_close(self):
        c = ctr(mem_max=480 * MIB, vpa_mem=vpa(300 * MIB, 500 * MIB))
        f = by_name(ev(c)[0])["memory-under-request"]
        e = f["evidence"]
        self.assertEqual(f["fingerprint"], "rightsizing:app/Deployment/web/main/memory-request")
        self.assertEqual(f["kind"], "rightsizing")
        self.assertEqual(e["proposed_request_mib"], 608.0)       # ceil16Mi(1.2 x max(upper 500, max 480)) = 600 -> 608
        self.assertEqual(e["proposed_limit_mib"], 912.0)         # 480 is above 80 % of the 512 limit: 1.5 x 608
        self.assertEqual(f["confidence"], "medium")              # a valid recommendation, but under two weeks old
        c = ctr(mem_max=480 * MIB, vpa_mem=vpa(300 * MIB, 500 * MIB, age_days=20))
        self.assertEqual(by_name(ev(c)[0])["memory-under-request"]["confidence"], "high")

    def test_under_request_does_not_wait_for_a_mature_recommendation(self):
        c = ctr(mem_max=300 * MIB, vpa_mem=vpa(190 * MIB, 210 * MIB, age_days=2))
        f = by_name(ev(c)[0])["memory-under-request"]
        self.assertEqual(f["confidence"], "low")
        self.assertEqual(f["evidence"]["proposed_request_mib"], 368.0)   # 1.2 x 300 = 360 -> 368; the immature upper bound is ignored

    def test_an_oom_with_a_peak_far_below_the_limit_is_reported_without_a_number(self):
        f = by_name(ev(ctr(oom=True, mem_max=100 * MIB, lim_mem=None))[0])["memory-under-request"]
        self.assertTrue(f["evidence"]["oomkilled"])
        self.assertIsNone(f["evidence"]["proposed_request_mib"])
        self.assertIn("no number is proposed", f["evidence"]["note"])

    def test_an_oom_far_below_a_big_limit_does_not_raise_the_limit(self):
        f = by_name(ev(ctr(oom=True, mem_max=361 * MIB, req_mem=512 * MIB, lim_mem=2048 * MIB))[0])["memory-under-request"]   # the real sabnzbd of 2026-10-10
        self.assertIsNone(f["evidence"]["proposed_limit_mib"])
        self.assertIsNone(f["evidence"]["proposed_request_mib"])
        self.assertIn("no number is proposed", f["evidence"]["note"])

    def test_an_oom_with_a_limit_proposes_a_higher_limit_and_never_lowers_the_request(self):
        f = by_name(ev(ctr(oom=True, mem_max=500 * MIB))[0])["memory-under-request"]
        e = f["evidence"]
        self.assertGreaterEqual(e["proposed_request_mib"], 256.0)
        self.assertGreater(e["proposed_limit_mib"], 512.0)

    def test_over_request_needs_a_valid_recommendation_and_keeps_a_margin(self):
        c = ctr(req_mem=1024 * MIB, mem_max=200 * MIB, vpa_mem=vpa(190 * MIB, 210 * MIB))
        e = by_name(ev(c)[0])["memory-over-request"]["evidence"]
        self.assertEqual(e["proposed_request_mib"], 288.0)       # 1.3 x max(upper 210, max 200) = 273 -> 288
        self.assertEqual(e["freed_bytes"], (1024 - 288) * MIB)
        # immature: no finding, and the reason is reported
        found, why = ev(ctr(req_mem=1024 * MIB, vpa_mem=vpa(190 * MIB, 210 * MIB, age_days=2)))
        self.assertEqual(found, [])
        self.assertEqual(why, ["vpa-immature"])

    def test_over_request_that_frees_little_is_not_a_finding(self):
        found, _ = ev(ctr(req_mem=100 * MIB, mem_max=30 * MIB, lim_mem=None, vpa_mem=vpa(28 * MIB, 30 * MIB)))
        self.assertEqual(found, [])   # 3x over, but 100 -> 48 frees 52 Mi (< 64)

    def test_over_request_is_floored_at_32mi(self):
        found, _ = ev(ctr(req_mem=200 * MIB, mem_max=5 * MIB, vpa_mem=vpa(4 * MIB, 6 * MIB)))
        self.assertEqual(by_name(found)["memory-over-request"]["evidence"]["proposed_request_mib"], 32.0)

    def test_a_rolled_controller_gets_no_findings_at_all(self):
        found, why = ev(ctr(youngest_pod_age_s=3600, mem_max=900 * MIB))
        self.assertEqual((found, why), ([], ["recently-rolled"]))

    def test_ignored_controllers_get_nothing(self):
        cfg = copy.deepcopy(CFG)
        cfg["ignore"] = ["app/*"]
        self.assertEqual(ev(ctr(mem_max=900 * MIB), cfg), ([], ["ignored"]))

    def test_a_rare_peak_workload_is_judged_on_its_p95_not_its_max(self):
        cfg = copy.deepcopy(CFG)
        cfg["rare_peaks"] = ["app/*"]
        c = ctr(req_mem=1000 * MIB, lim_mem=None, mem_max=1300 * MIB, mem_p95=900 * MIB, vpa_mem=vpa(800 * MIB, 1200 * MIB))
        self.assertEqual(ev(c, cfg)[0], [])                      # the peak above the request is the design, not a finding
        self.assertIn("memory-under-request", by_name(ev(c)[0]))  # without the marker the same numbers are one

    def test_the_limit_cut_has_a_switch_and_never_touches_rare_peaks(self):
        c = ctr(req_mem=200 * MIB, lim_mem=4096 * MIB, mem_max=190 * MIB)
        cfg = copy.deepcopy(CFG)
        cfg["memory"]["over_limit"]["enabled"] = False
        self.assertNotIn("memory-over-limit", by_name(ev(c, cfg)[0]))
        cfg["memory"]["over_limit"]["enabled"] = True
        f = by_name(ev(c, cfg)[0])["memory-over-limit"]
        self.assertEqual(f["evidence"]["proposed_limit_mib"], 384.0)   # max(2 x max 190, 1.5 x upper 210, request 200) = 380 -> 384
        cfg["rare_peaks"] = ["app/*"]
        self.assertNotIn("memory-over-limit", by_name(ev(c, cfg)[0]))

    def test_creep_needs_a_steady_rise_and_ignores_restarting_pods(self):
        rising = [(NOW - (20 - i) * DAY, (100 + 6 * i) * MIB) for i in range(20)]
        f = by_name(ev(ctr(daily_mem_max=rising, mem_max=216 * MIB))[0])["memory-creep"]
        self.assertAlmostEqual(f["evidence"]["slope_mib_per_day"], 6.0, places=1)
        self.assertEqual(f["fingerprint"], "rightsizing:app/Deployment/web/main/memory-creep")
        self.assertNotIn("memory-creep", by_name(ev(ctr(daily_mem_max=rising, mem_max=216 * MIB, restarts=5))[0]))
        flat = [(NOW - (20 - i) * DAY, 150 * MIB) for i in range(20)]
        self.assertNotIn("memory-creep", by_name(ev(ctr(daily_mem_max=flat))[0]))
        self.assertNotIn("memory-creep", by_name(ev(ctr(daily_mem_max=rising[:8], mem_max=150 * MIB))[0]))  # not enough days


class CpuRules(unittest.TestCase):
    def test_over_request_follows_the_normal_day_and_keeps_the_worst_day_as_a_floor(self):
        c = ctr(req_cpu=1.0, cpu_p95=0.05, cpu_worst=0.08, vpa_cpu=vpa(0.04, 0.06))
        e = by_name(ev(c)[0])["cpu-over-request"]["evidence"]
        self.assertEqual(e["proposed_request_millicores"], 80.0)         # max(1.5 x 0.05 = 0.075 -> 80m, worst day 80m)
        self.assertEqual(e["freed_millicores"], 920.0)
        c = ctr(req_cpu=1.0, cpu_p95=0.05, cpu_worst=0.3, vpa_cpu=vpa(0.04, 0.06))
        self.assertEqual(by_name(ev(c)[0])["cpu-over-request"]["evidence"]["proposed_request_millicores"], 300.0)

    def test_small_savings_and_close_requests_are_not_findings(self):
        self.assertEqual(ev(ctr(req_cpu=0.06, cpu_p95=0.01, cpu_worst=0.02, vpa_cpu=vpa(0.01, 0.02)))[0], [])    # 3x over, but frees only 40m (< 50m)
        self.assertEqual(ev(ctr(req_cpu=0.1, cpu_p95=0.05, cpu_worst=0.06))[0], [])

    def test_under_request_means_a_normal_day_needs_more_than_the_request(self):
        c = ctr(req_cpu=0.05, cpu_p95=0.2, cpu_worst=0.25, vpa_cpu=vpa(0.15, 0.3))
        e = by_name(ev(c)[0])["cpu-under-request"]["evidence"]
        self.assertEqual(e["proposed_request_millicores"], 300.0)        # 1.5 x max(target 150m, p95 200m)
        # a worse worst day floors it
        c = ctr(req_cpu=0.05, cpu_p95=0.06, cpu_worst=0.5, vpa_cpu=vpa(0.05, 0.3))
        self.assertEqual(by_name(ev(c)[0])["cpu-under-request"]["evidence"]["proposed_request_millicores"], 500.0)

    def test_a_cpu_limit_is_never_proposed(self):
        for c in (ctr(req_cpu=1.0), ctr(req_cpu=0.01, cpu_p95=0.5, cpu_worst=0.6)):
            for f in ev(c)[0]:
                self.assertFalse([k for k in f["evidence"] if "limit" in k and "cpu" in k], f)

    def test_cpu_over_request_needs_a_valid_recommendation(self):
        found, why = ev(ctr(req_cpu=1.0, vpa_cpu=vpa(0.04, 0.06, age_days=1)))
        self.assertEqual(found, [])
        self.assertEqual(why, ["vpa-immature"])


# ---- the collection against a canned VictoriaMetrics ------------------------------------------------------------------------------------

def vec(*rows):
    return [{"metric": m, "value": [NOW, str(v)]} for m, v in rows]


def key(pod, container="main", ns="app", **kw):
    return {"namespace": ns, "pod": pod, "container": container, **kw}


class FakeVM:
    """Answers the collector's queries by their shape. The scenario: a Deployment `web` that rolled (old pod `web-old-1` is gone but its
    usage is still in the window; `web-new-1` is the pod now), and a StatefulSet `db` with a recommendation but nothing wrong."""

    def __init__(self):
        self.queries = []
        self.fail: str | None = None
        vk = {"namespace": "app", "target_kind": "Deployment", "target_name": "web", "container": "main"}
        dk = {"namespace": "app", "target_kind": "StatefulSet", "target_name": "db", "container": "db"}
        self.vpa = {  # (stat, resource, controller) -> value
            "lowerbound": {("memory", "web"): 400 * MIB, ("cpu", "web"): 0.04, ("memory", "db"): 90 * MIB, ("cpu", "db"): 0.02},
            "target": {("memory", "web"): 500 * MIB, ("cpu", "web"): 0.05, ("memory", "db"): 100 * MIB, ("cpu", "db"): 0.02},
            "upperbound": {("memory", "web"): 600 * MIB, ("cpu", "web"): 0.08, ("memory", "db"): 110 * MIB, ("cpu", "db"): 0.03}}
        self.vk, self.dk = vk, dk

    def vpa_raw(self, stat):
        return [({**(self.vk if ctl == "web" else self.dk), "resource": res}, v) for (res, ctl), v in self.vpa[stat].items()]

    def vpa_rows(self, stat):
        return vec(*self.vpa_raw(stat))

    def answer(self, promql: str, path: str):
        p = promql
        if path.endswith("query_range"):
            return [{"metric": key("web-old-1"), "values": [[NOW - 3 * DAY, "400000000"], [NOW - 2 * DAY, "900000000"]]},
                    {"metric": key("web-new-1"), "values": [[NOW - 1 * DAY, "500000000"], [NOW, "520000000"]]}]
        if "kube_replicaset_owner" in p:
            return vec(({"namespace": "app", "replicaset": "web-old", "owner_name": "web"}, 1), ({"namespace": "app", "replicaset": "web-new", "owner_name": "web"}, 1))
        if "kube_pod_owner" in p:
            return vec(({"namespace": "app", "pod": "web-old-1", "owner_kind": "ReplicaSet", "owner_name": "web-old"}, 1),
                       ({"namespace": "app", "pod": "web-new-1", "owner_kind": "ReplicaSet", "owner_name": "web-new"}, 1),
                       ({"namespace": "app", "pod": "db-0", "owner_kind": "StatefulSet", "owner_name": "db"}, 1),
                       ({"namespace": "app", "pod": "job-x", "owner_kind": "Job", "owner_name": "job"}, 1))
        if "kube_pod_start_time" in p:
            return vec(({"namespace": "app", "pod": "web-new-1"}, 2 * DAY), ({"namespace": "app", "pod": "db-0"}, 9 * DAY))
        if "sum by (node)" in p:
            if "allocatable{resource=\"memory\"}" in p:
                return vec(({"node": "einherjar-urd"}, 14000 * MIB), ({"node": "gondul"}, 3000 * MIB))
            if "allocatable{resource=\"cpu\"}" in p:
                return vec(({"node": "einherjar-urd"}, 2.0))
            if "requests{resource=\"memory\"}" in p:
                return vec(({"node": "einherjar-urd"}, 7000 * MIB))
            if "requests{resource=\"cpu\"}" in p:
                return vec(({"node": "einherjar-urd"}, 0.5))
            if "limits{resource=\"memory\"}" in p:
                return vec(({"node": "einherjar-urd"}, 9000 * MIB))
            return vec(({"node": "einherjar-urd"}, 3500 * MIB))
        if "kube_pod_container_resource_requests" in p:
            return vec((key("web-new-1", resource="memory"), 256 * MIB), (key("web-new-1", resource="cpu"), 0.1), (key("db-0", "db", resource="memory"), 300 * MIB),
                       (key("db-0", "db", resource="cpu"), 0.05))
        if "kube_pod_container_resource_limits" in p:
            return vec((key("web-new-1", resource="memory"), 512 * MIB), (key("db-0", "db", resource="memory"), 600 * MIB))
        if "last_terminated_reason" in p:
            return []
        if "restarts_total" in p:
            return vec((key("web-new-1"), 1), (key("db-0", "db"), 0))
        if "quantile_over_time(0.5, (quantile_over_time(0.95" in p:
            return vec((key("web-new-1"), 0.05), (key("db-0", "db"), 0.02))
        if "max_over_time((quantile_over_time(0.95" in p:
            return vec((key("web-new-1"), 0.06), (key("db-0", "db"), 0.03))
        if "quantile_over_time(0.95, container_memory" in p:
            return vec((key("web-old-1"), 850 * MIB), (key("web-new-1"), 500 * MIB), (key("db-0", "db"), 95 * MIB))
        if "quantile_over_time(0.5, container_memory" in p:
            return vec((key("web-old-1"), 600 * MIB), (key("web-new-1"), 480 * MIB), (key("db-0", "db"), 90 * MIB))
        if "max_over_time(container_memory_working_set_bytes" in p:
            return vec((key("web-old-1"), 900 * MIB), (key("web-new-1"), 520 * MIB), (key("db-0", "db"), 100 * MIB), (key("job-x"), 5 * MIB))
        if "lifetime(" in p:
            return vec(*[(m, 10 * DAY) for m, _ in self.vpa_raw("target")])
        if "max_over_time(kube_customresource" in p or "min_over_time(kube_customresource" in p:
            return self.vpa_rows("target")
        for stat in ("lowerbound", "target", "upperbound"):
            if f"containerrecommendations_{stat}" in p:
                return self.vpa_rows(stat)
        raise AssertionError("unexpected query: " + p)

    def fetch(self, url: str) -> str:
        u = urllib.parse.urlparse(url)
        promql = urllib.parse.parse_qs(u.query)["query"][0]
        self.queries.append(promql)
        if self.fail and self.fail in promql:
            raise OSError("boom")
        res = self.answer(promql, u.path)
        # the matrix endpoint returns "values", the vector one "value"
        return json.dumps({"status": "success", "data": {"resultType": "matrix" if u.path.endswith("query_range") else "vector", "result": [
            {**s, "metric": {**s["metric"], "cluster": "asgard"}} for s in res]}})


class Collect(unittest.TestCase):
    def setUp(self):
        self.fake = FakeVM()
        self.vm = rs.VM("http://vm.invalid", self.fake.fetch)

    def test_history_of_a_replaced_pod_counts_but_its_template_does_not(self):
        f = rs.collect(self.vm, CFG, NOW)
        web = f.containers["app/Deployment/web/main"]
        self.assertEqual(web.mem_max, 900 * MIB)                 # from web-old-1, which no longer runs
        self.assertEqual(web.req_mem, 256 * MIB)                 # from web-new-1, which does
        self.assertEqual(web.lim_mem, 512 * MIB)
        self.assertEqual(web.pods, 1)
        self.assertEqual(web.youngest_pod_age_s, 2 * DAY)
        self.assertEqual(web.mem_p95, 850 * MIB)
        self.assertEqual(web.cpu_p95, 0.05)
        self.assertEqual(web.cpu_worst, 0.06)
        self.assertEqual(web.restarts, 1)
        self.assertEqual(len(web.daily_mem_max), 4)              # four distinct days across the old and the new pod
        self.assertEqual(f.errors, [])

    def test_vpa_series_land_on_the_right_container_and_resource(self):
        f = rs.collect(self.vm, CFG, NOW)
        web = f.containers["app/Deployment/web/main"]
        self.assertEqual((web.vpa_mem.lower, web.vpa_mem.target, web.vpa_mem.upper), (400 * MIB, 500 * MIB, 600 * MIB))
        self.assertEqual(web.vpa_cpu.target, 0.05)
        self.assertEqual(web.vpa_mem.age_s, 10 * DAY)
        self.assertEqual(f.covered, {"app/Deployment/web", "app/StatefulSet/db"})

    def test_jobs_and_bare_pods_are_not_controllers_of_interest(self):
        f = rs.collect(self.vm, CFG, NOW)
        self.assertEqual(f.controllers, {"app/Deployment/web", "app/StatefulSet/db"})
        self.assertFalse([k for k in f.containers if "job" in k])

    def test_workers_are_summarised_and_control_planes_left_out(self):
        f = rs.collect(self.vm, CFG, NOW)
        self.assertEqual([n["node"] for n in f.nodes], ["einherjar-urd"])
        n = f.nodes[0]
        self.assertEqual((n["memory_requested_pct"], n["memory_limits_mib"], n["memory_used_mib"]), (50.0, 9000.0, 3500.0))
        self.assertEqual(n["cpu_requested_pct"], 25.0)

    def test_a_failing_query_is_reported_and_never_hides_the_rest(self):
        self.fake.fail = "kube_pod_container_status_last_terminated_reason"
        f = rs.collect(self.vm, CFG, NOW)
        self.assertEqual(len(f.errors), 1)
        self.assertIn("oomkilled", f.errors[0])
        self.assertEqual(f.containers["app/Deployment/web/main"].mem_max, 900 * MIB)

    def test_a_namespace_narrows_every_query(self):
        rs.collect(self.vm, CFG, NOW, namespace="app")
        scoped = [q for q in self.fake.queries if not q.startswith("sum by (node)") and ("kube_pod" in q or "container_" in q or "kube_customresource" in q)]
        self.assertTrue(scoped and all('namespace="app"' in q for q in scoped), [q for q in scoped if 'namespace="app"' not in q])

    def test_the_findings_use_the_history_and_say_what_they_could_not_do(self):
        found, stats = rs.findings(self.vm, CFG, NOW)
        names = {f["evidence"]["finding"] for f in found}
        self.assertIn("memory-under-request", names)       # web: 900 Mi peak (old pod) against a 256 Mi request
        w = next(f for f in found if f["target"] == "app/Deployment/web/main" and f["metric"] == "memory-request")
        self.assertEqual(w["evidence"]["proposed_request_mib"], 1088.0)   # 1.2 x max(upper 600, max 900) = 1080 -> 1088
        self.assertEqual(stats["containers"], 2)
        self.assertEqual(stats["controllers_with_vpa"], 2)
        self.assertEqual(stats["errors"], 0)
        self.assertTrue(all(f["kind"] == "rightsizing" and f["fingerprint"].startswith("rightsizing:") for f in found))
        json.dumps(found)  # plain JSON all the way down

    def test_summary_and_detail_shapes(self):
        f = rs.collect(self.vm, CFG, NOW)
        s = rs.summary(f, CFG, NOW)
        self.assertEqual(s["controllers"], 2)
        self.assertEqual(s["controllers_without_vpa"], [])
        self.assertEqual(s["oldest_vpa_sample_days"], 10.0)
        self.assertTrue(s["findings"] and s["findings"][0]["target"].startswith("app/"))
        d = rs.detail(f, CFG, NOW, "app", "deployments", "web")
        self.assertTrue(d["found"])
        c = d["controllers"][0]["containers"][0]
        self.assertEqual(c["requests"]["memory_mib"], 256.0)
        self.assertEqual(c["usage_30d"]["memory_max_mib"], 900.0)
        self.assertTrue(c["vpa"]["memory"]["valid"])
        self.assertFalse(rs.detail(f, CFG, NOW, "app", "deployments", "nope")["found"])
        self.assertEqual(len(rs.detail(f, CFG, NOW, "app")["controllers"]), 2)

    def test_controllers_without_a_vpa_are_listed_unless_their_namespace_is_exempt(self):
        f = rs.collect(self.vm, CFG, NOW)
        f.controllers |= {"app/Deployment/new-app", "kube-system/Deployment/coredns"}
        self.assertEqual(rs.uncovered(f, CFG), ["app/Deployment/new-app"])


# ---- the tool -----------------------------------------------------------------------------------------------------------------------------

class ToolTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeVM()
        self.cfg = tools.LiveConfig(root=REPO)
        p = mock.patch.object(tools, "_obs_get", lambda cfg, url, what: self.fake.fetch(url))
        p.start()
        self.addCleanup(p.stop)

    def call(self, args):
        return tools.live(self.cfg, "kube.rightsizing", tools.validate("kube.rightsizing", args))

    def test_no_arguments_is_the_cluster_summary(self):
        out = self.call({})
        self.assertEqual(out["controllers"], 2)
        self.assertEqual(out["workers"][0]["node"], "einherjar-urd")

    def test_a_workload_is_the_detail_view(self):
        out = self.call({"namespace": "app", "kind": "deployments", "name": "web"})
        self.assertEqual(out["controllers"][0]["controller"], "app/Deployment/web")

    def test_unknown_workload_is_404_and_odd_argument_combinations_are_400(self):
        with self.assertRaises(tools.ToolError) as cm:
            self.call({"namespace": "app", "kind": "deployments", "name": "nope"})
        self.assertEqual(cm.exception.status, 404)
        for bad in ({"name": "web"}, {"kind": "deployments"}, {"namespace": "app", "name": "web"}):
            with self.assertRaises(tools.ToolError) as cm:
                self.call(bad)
            self.assertEqual(cm.exception.status, 400, bad)

    def test_arguments_are_closed_and_the_name_is_read_shaped(self):
        for bad in ({"kind": "pods"}, {"namespace": "App;drop"}, {"x": 1}, {"namespace": 'a" or 1==1'}):
            with self.assertRaises(tools.ToolError):
                tools.validate("kube.rightsizing", bad)
        self.assertIn("kube.rightsizing", tools.SPEC)

    def test_it_only_ever_reads_through_the_two_query_endpoints(self):
        self.call({})
        self.assertTrue(self.fake.queries)  # every request went through fetch(): the read routes' GET


# ---- the daily pass in the forecast job -----------------------------------------------------------------------------------------------------

class JobPass(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.fake = FakeVM()
        self.cfgp = str(REPO / "aiops" / "rightsizing.yml")

    def run_job(self, now, **kw):
        args = ["--log", str(self.tmp / "log.jsonl"), "--current", str(self.tmp / "cur.json"), "--rightsizing", self.cfgp]
        return forecast_run.main(args, query=lambda *a: [], now=now, fetch=self.fake.fetch, **kw)

    def current(self):
        return json.loads((self.tmp / "cur.json").read_text())

    def test_findings_ride_in_the_current_file_as_quiet_rows(self):
        self.assertEqual(self.run_job(NOW), 0)
        cur = self.current()
        rows = [f for f in cur["findings"] if f["kind"] == "rightsizing"]
        self.assertTrue(rows)
        self.assertEqual(cur["rightsizing"]["ts"], NOW)
        self.assertEqual(len(cur["rightsizing"]["findings"]), len(rows))

    def test_the_pass_runs_once_a_day_and_carries_the_findings_between(self):
        self.run_job(NOW)
        n = len(self.fake.queries)
        self.run_job(NOW + 3600)
        self.assertEqual(len(self.fake.queries), n)               # not due: no query at all
        self.assertTrue([f for f in self.current()["findings"] if f["kind"] == "rightsizing"])  # ...but the rows are still there
        self.run_job(NOW + 86400 + 60)
        self.assertGreater(len(self.fake.queries), n)             # due again

    def test_a_pass_with_query_errors_keeps_the_previous_findings_and_retries(self):
        self.run_job(NOW)
        before = self.current()["rightsizing"]["findings"]
        self.fake.fail = "kube_pod_container_status_last_terminated_reason"
        self.run_job(NOW + 86400 + 60)
        after = self.current()["rightsizing"]
        self.assertEqual(after["findings"], before)
        self.assertEqual(after["ts"], NOW)                        # the old timestamp: the next hourly run is due again
        self.assertEqual(after["stats"]["errors"], 1)

    def test_without_the_flag_the_job_is_exactly_the_old_one(self):
        forecast_run.main(["--log", str(self.tmp / "log.jsonl"), "--current", str(self.tmp / "cur.json")], query=lambda *a: [], now=NOW)
        self.assertNotIn("rightsizing", self.current())
        self.assertEqual(self.fake.queries, [])

    def test_a_first_pass_that_cannot_reach_anything_stores_no_findings(self):
        def boom(url):
            raise OSError("down")
        out = forecast_run.rightsizing_pass(self.cfgp, "http://vm.invalid", NOW, 86400, None, fetch=boom)
        self.assertEqual(out.get("findings", []), [])
        self.assertNotIn("ts", out)


# ---- the store keeps them quiet -------------------------------------------------------------------------------------------------------------

class Quiet(unittest.TestCase):
    def setUp(self):
        self.t = [1_790_000_000]
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.row_factory = sqlite3.Row
        self.audit = []
        self.fc = forecast_store.Forecasts(db, threading.RLock(), lambda: self.t[0], lambda e, **kw: self.audit.append((e, kw)),
                                           forecast_store.ForecastConfig(max_new_per_sync=2))

    def doc(self, findings):
        return {"ts": self.t[0], "findings": findings}

    def quiet(self, i):
        return {"fingerprint": f"rightsizing:a/Deployment/d{i}/c/memory-request", "kind": "rightsizing", "metric": "memory-request", "target": f"a/Deployment/d{i}/c",
                "confidence": "low", "days_to_full": None, "ratio": None, "evidence": {"finding": "memory-under-request"}}

    def loud(self, i):
        return {"fingerprint": f"forecast:slow-fill:m:t{i}", "kind": "slow-fill", "metric": "m", "target": f"t{i}", "confidence": "high", "days_to_full": 5.0, "evidence": {}}

    def test_quiet_rows_never_reach_the_card_feed_the_default_list_or_the_summary(self):
        self.fc.sync(self.doc([self.quiet(i) for i in range(5)] + [self.loud(1)]))
        feed = self.fc.feed()["events"]
        self.assertEqual([e["forecast"]["kind"] for e in feed], ["slow-fill"])
        self.assertEqual([f["kind"] for f in self.fc.list()], ["slow-fill"])
        self.assertEqual(self.fc.summary()["by_state"], {"open": 1})
        rows = self.fc.list(kind="rightsizing")
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["evidence"]["evidence"]["finding"], "memory-under-request")

    def test_quiet_rows_do_not_eat_the_budget_for_real_forecasts(self):
        out = self.fc.sync(self.doc([self.quiet(i) for i in range(10)] + [self.loud(1), self.loud(2)]))
        self.assertEqual(out["created"], 2)
        self.assertEqual(out["deferred"], 0)
        self.assertEqual(out["quiet_created"], 10)

    def test_they_resolve_like_any_other_row_when_the_pass_stops_producing_them(self):
        self.fc.sync(self.doc([self.quiet(1)]))
        self.t[0] += 3600
        self.fc.sync(self.doc([]))
        self.t[0] += 3600
        out = self.fc.sync(self.doc([]))
        self.assertEqual(out["resolved"], 1)
        self.assertEqual(self.fc.list(kind="rightsizing"), [])


class Config(unittest.TestCase):
    def test_the_shipped_file_is_clean_and_a_broken_one_is_caught(self):
        self.assertEqual(lint.check_rightsizing(CFG), [])
        bad = copy.deepcopy(CFG)
        bad["digest"]["cadence"] = "daily"
        self.assertTrue(lint.check_rightsizing(bad))
        bad = copy.deepcopy(CFG)
        bad["memory"]["over_request"]["floor_mib"] = 8
        self.assertTrue(any("floor_mib" in e for e in lint.check_rightsizing(bad)))
        bad = copy.deepcopy(CFG)
        bad["allow"].append("bad pattern with spaces")
        self.assertTrue(lint.check_rightsizing(bad))
        bad = copy.deepcopy(CFG)
        bad["unknown_key"] = 1
        self.assertTrue(lint.check_rightsizing(bad))

    def test_every_allowed_pattern_names_a_namespace_that_has_a_vpa(self):
        vpa_dir = REPO / "k8s" / "asgard" / "vpa-config"
        text = "\n".join(p.read_text() for p in vpa_dir.glob("*.yaml"))
        for pat in CFG["allow"]:
            self.assertIn(f"namespace: {pat.split('/')[0]}", text, pat)


if __name__ == "__main__":
    unittest.main()
