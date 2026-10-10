"""Phase 10i5: the 72-hour post-merge watch of a rightsizing PR (aiops/toolbelt/rightsizing_watch.py), its hook on a merged change request,
the revert route, and the bot's verdict notice. VictoriaMetrics is a canned world that the tests move forward in time."""
from __future__ import annotations

import copy
import json
import sqlite3
import sys
import threading
import unittest
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests", "author", "bot"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import drafts  # noqa: E402
import rightsizing  # noqa: E402
import rightsizing_watch as rw  # noqa: E402
import test_change_requests as tcr  # noqa: E402
import test_rightsizing_digest as trd  # noqa: E402

MIB = rightsizing.MIB
H = 3600
NOW = 1_800_000_000

SPEC = {"v": 1, "controller": "app/Deployment/web", "kind": "trim", "freed_mib": 512.0,
        "containers": {"main": {"old": {"request_mib": 1024.0, "limit_mib": 2048.0, "request_millicores": 200.0},
                                "new": {"request_mib": 512.0, "limit_mib": 1024.0, "request_millicores": 100.0}}}}


def cr_view(spec=SPEC, cid=7, **kw):
    return {"id": cid, "class": "rightsizing", "body": "Trim it.\n" + rw.spec_block(spec), "pr_url": "https://github.com/XIIISins/homelab/pull/300", **kw}


class World:
    """A Deployment `web` as VictoriaMetrics would report it. `t` is the clock the tests advance; pods are plain dicts."""

    def __init__(self):
        self.t = NOW
        self.pods = [self.pod("web-old-1", started=NOW - 5 * 86400, req=1024, lim=2048, cpu=0.2)]
        self.avail, self.desired = 1, 1
        self.fail_on: str | None = None

    def pod(self, name, started, req, lim, cpu, ws=300.0, restarts=0.0, oom_at=None, loop=False, ready=True):
        return {"name": name, "started": started, "req": req, "lim": lim, "cpu": cpu, "ws": ws, "restarts": restarts, "oom_at": oom_at, "loop": loop, "ready": ready}

    def roll(self):
        """The merge's rollout finished: one new pod on the new values."""
        self.pods = [self.pod("web-new-1", started=self.t + 1, req=512, lim=1024, cpu=0.1)]   # a second after "now", i.e. after the merge

    def vec(self, rows):
        return json.dumps({"status": "success", "data": {"resultType": "vector", "result": [{"metric": m, "value": [self.t, str(v)]} for m, v in rows]}})

    def fetch(self, url: str) -> str:
        p = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["query"][0]
        if self.fail_on and self.fail_on in p:
            raise OSError("boom")
        ns = {"namespace": "app"}
        pods = self.pods
        if "kube_replicaset_owner" in p:
            return self.vec([({**ns, "replicaset": "web-rs", "owner_name": "web"}, 1)])
        if "kube_pod_owner" in p:
            return self.vec([({**ns, "pod": x["name"], "owner_kind": "ReplicaSet", "owner_name": "web-rs"}, 1) for x in pods])
        if "kube_pod_start_time" in p:
            return self.vec([({"pod": x["name"]}, self.t - x["started"]) for x in pods])
        if "kube_pod_status_ready" in p:
            return self.vec([({"pod": x["name"]}, 1 if x["ready"] else 0) for x in pods])
        if "kube_pod_container_resource_requests" in p:
            return self.vec([({"pod": x["name"], "container": "main", "resource": "memory"}, x["req"] * MIB) for x in pods]
                            + [({"pod": x["name"], "container": "main", "resource": "cpu"}, x["cpu"]) for x in pods])
        if "kube_pod_container_resource_limits" in p:
            return self.vec([({"pod": x["name"], "container": "main", "resource": "memory"}, x["lim"] * MIB) for x in pods])
        if "container_memory_working_set_bytes" in p:
            return self.vec([({"pod": x["name"], "container": "main"}, x["ws"] * MIB) for x in pods])
        if "restarts_total" in p:
            return self.vec([({"pod": x["name"], "container": "main"}, x["restarts"]) for x in pods])
        if "last_terminated_reason" in p:
            return self.vec([({"pod": x["name"], "container": "main"}, 1) for x in pods if x["oom_at"]])
        if "last_terminated_timestamp" in p:
            return self.vec([({"pod": x["name"], "container": "main"}, x["oom_at"]) for x in pods if x["oom_at"]])
        if "waiting_reason" in p:
            return self.vec([({"pod": x["name"], "container": "main"}, 1) for x in pods if x["loop"]])
        if "kube_deployment_status_replicas_available" in p:
            return self.vec([({}, self.avail)])
        if "kube_deployment_spec_replicas" in p:
            return self.vec([({}, self.desired)])
        raise AssertionError("unexpected query: " + p)


class Env:
    """A Watches object over an in-memory database and the canned world."""

    def __init__(self):
        self.world = World()
        db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        db.row_factory = sqlite3.Row
        self.notes, self.audit, self.alerts = [], [], []
        self.w = rw.Watches(db, threading.RLock(), lambda: self.world.t, lambda e, **kw: self.audit.append((e, kw)),
                            lambda: rightsizing.VM("http://vm.invalid", self.world.fetch), lambda cid, kind, data: self.notes.append((cid, kind, data)),
                            lambda name, since: list(self.alerts))

    def go_live(self):
        self.w.start(cr_view())
        self.world.roll()
        self.world.t += 120
        self.w.poll()
        assert self.w.get(1)["state"] == "watching", self.w.get(1)

    def poll_at(self, hours):
        self.world.t = NOW + 120 + int(hours * H)
        self.w.poll()
        return self.w.get(1)


class Spec(unittest.TestCase):
    def test_a_valid_block_round_trips_and_garbage_is_ignored(self):
        self.assertEqual(rw.parse_spec(cr_view()["body"]), SPEC)
        self.assertIsNone(rw.parse_spec("no block here"))
        self.assertIsNone(rw.parse_spec("<!-- rightsizing-spec {not json} -->"))
        bad = copy.deepcopy(SPEC)
        bad["controller"] = 'app/Deployment/web"} or vector(1) #'
        self.assertIsNone(rw.parse_spec(rw.spec_block(bad)))
        bad = copy.deepcopy(SPEC)
        bad["containers"]["main"]["new"]["image"] = 1
        self.assertIsNone(rw.parse_spec(rw.spec_block(bad)))
        bad = copy.deepcopy(SPEC)
        bad["containers"]["main"]["new"]["limit_mib"] = -5
        self.assertIsNone(rw.parse_spec(rw.spec_block(bad)))

    def test_the_description_and_the_revert_swap_old_and_new(self):
        self.assertIn("memory request 1024 MiB → 512 MiB", rw.describe_spec(SPEC))
        self.assertIn("CPU request 200m → 100m", rw.describe_spec(SPEC))
        rev = rw.revert_spec(SPEC)
        self.assertEqual(rev["kind"], "revert")
        self.assertEqual(rev["containers"]["main"]["new"]["limit_mib"], 2048.0)
        self.assertEqual(rev["freed_mib"], -512.0)
        title, body = rw.revert_request({"cr_id": 7, "spec": SPEC, "verdict": {"reasons": ["OOMKilled since the new values went live: main"]}})
        self.assertEqual(title, "Revert rightsizing of web")
        self.assertIn("resources only", body)
        self.assertIn("OOMKilled", body)
        self.assertEqual(rw.parse_spec(body)["kind"], "revert")


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def test_it_waits_while_the_old_pod_still_runs_then_goes_live_on_the_new_values(self):
        self.e.w.start(cr_view())
        self.e.world.t += 300
        self.e.w.poll()
        self.assertEqual(self.e.w.get(1)["state"], "waiting")         # the pod from before the merge is the only one
        self.e.world.roll()
        self.e.world.pods[0]["req"] = 600                              # new pod, but not the values we asked for
        self.e.world.t += 300
        self.e.w.poll()
        self.assertEqual(self.e.w.get(1)["state"], "waiting")
        self.e.world.pods[0]["req"] = 512
        self.e.world.t += 300
        self.e.w.poll()
        w = self.e.w.get(1)
        self.assertEqual(w["state"], "watching")
        self.assertEqual(w["until_at"], w["live_at"] + 72 * H)

    def test_a_pod_that_is_not_ready_or_a_short_controller_is_not_live(self):
        self.e.w.start(cr_view())
        self.e.world.roll()
        self.e.world.pods[0]["ready"] = False
        self.e.world.t += 300
        self.e.w.poll()
        self.assertEqual(self.e.w.get(1)["state"], "waiting")
        self.e.world.pods[0]["ready"] = True
        self.e.world.avail = 0
        self.e.w.poll()
        self.assertEqual(self.e.w.get(1)["state"], "waiting")

    def test_values_that_never_appear_are_inconclusive_after_24_hours(self):
        self.e.w.start(cr_view())
        self.e.world.t += 25 * H
        self.e.w.poll()
        w = self.e.w.get(1)
        self.assertEqual((w["state"], w["verdict"]["verdict"]), ("inconclusive", "inconclusive"))
        self.assertEqual(self.e.notes[-1][1:2], ("watch",))

    def test_72_quiet_hours_hold_and_the_change_request_is_told(self):
        self.e.go_live()
        self.assertEqual(self.e.poll_at(36)["state"], "watching")
        w = self.e.poll_at(73)
        self.assertEqual((w["state"], w["verdict"]["verdict"]), ("held", "held"))
        cid, kind, data = self.e.notes[-1]
        self.assertEqual((cid, kind, data["state"], data["watch"]), (7, "watch", "held", 1))
        self.assertTrue(self.e.w.held("app/Deployment/web"))
        self.assertEqual(self.e.w.list(), [])                           # no longer active

    def test_an_oom_after_going_live_regresses_at_once_but_an_old_one_does_not(self):
        self.e.go_live()
        self.e.world.pods[0]["oom_at"] = NOW - 10 * H                   # before the new values: not this change's doing
        self.assertEqual(self.e.poll_at(1)["state"], "watching")
        self.e.world.pods[0]["oom_at"] = NOW + 120 + 2 * H
        w = self.e.poll_at(3)
        self.assertEqual(w["state"], "regressed")
        self.assertIn("OOMKilled", w["verdict"]["reasons"][0])
        self.assertFalse(self.e.w.held("app/Deployment/web"))
        self.assertEqual(self.e.notes[-1][2]["state"], "regressed")

    def test_restarts_one_is_a_warning_two_is_a_regression(self):
        self.e.go_live()
        self.e.world.pods[0]["restarts"] = 1
        w = self.e.poll_at(1)
        self.assertEqual(w["state"], "watching")
        self.assertIn("1 container restart", w["checks"]["warnings"][0])
        self.e.world.pods[0]["restarts"] = 2
        self.assertEqual(self.e.poll_at(2)["state"], "regressed")

    def test_a_working_set_near_the_new_limit_regresses(self):
        self.e.go_live()
        self.e.world.pods[0]["ws"] = 900.0                              # 88 % of 1024: fine
        self.assertEqual(self.e.poll_at(1)["state"], "watching")
        self.e.world.pods[0]["ws"] = 950.0                              # 93 %
        w = self.e.poll_at(2)
        self.assertEqual(w["state"], "regressed")
        self.assertIn("above 90 %", w["verdict"]["reasons"][0])

    def test_crashloop_and_a_lasting_loss_of_availability_regress_but_a_blip_does_not(self):
        self.e.go_live()
        self.e.world.avail = 0
        self.assertEqual(self.e.poll_at(1)["state"], "watching")        # one check: a blip
        self.e.world.avail = 1
        self.assertEqual(self.e.poll_at(2)["state"], "watching")        # recovered: the count resets
        self.e.world.avail = 0
        self.e.poll_at(3)
        w = self.e.poll_at(4)
        self.assertEqual(w["state"], "regressed")
        self.assertIn("not fully available", w["verdict"]["reasons"][0])

    def test_crashloop_regresses(self):
        self.e.go_live()
        self.e.world.pods[0]["loop"] = True
        w = self.e.poll_at(1)
        self.assertEqual(w["state"], "regressed")
        self.assertIn("CrashLoopBackOff", w["verdict"]["reasons"][0])

    def test_an_alert_that_names_the_workload_regresses(self):
        self.e.go_live()
        self.e.alerts.append("alert: web pods not ready")
        w = self.e.poll_at(1)
        self.assertEqual(w["state"], "regressed")
        self.assertIn("an alert names this workload", w["verdict"]["reasons"][0])

    def test_a_failing_query_leaves_the_watch_alone_and_says_so(self):
        self.e.go_live()
        self.e.world.fail_on = "kube_pod_start_time"
        self.e.world.t += H
        self.assertEqual(self.e.w.poll(), 0)
        self.assertEqual(self.e.w.get(1)["state"], "watching")
        self.assertEqual(self.e.audit[-1][0], "rightsizing_watch_error")

    def test_a_request_without_a_spec_starts_no_watch_and_a_second_merge_report_does_not_duplicate(self):
        self.assertIsNone(self.e.w.start({"id": 9, "body": "Trim it by hand."}))
        self.e.w.start(cr_view())
        self.e.w.start(cr_view())
        self.assertEqual(len(self.e.w.list()), 1)

    def test_results_and_the_limit_gate(self):
        self.e.go_live()
        self.assertEqual(self.e.w.results()[0]["verdict"], "watching")
        self.e.poll_at(73)
        r = self.e.w.results()[0]
        self.assertEqual((r["verdict"], r["freed_mib"], r["target"]), ("held", 512.0, "app/Deployment/web"))
        self.assertFalse(self.e.w.held("app/Deployment/other"))


class Wired(unittest.TestCase):
    """Through the real Toolbelt: the merge hook, the digest's results and limit gate, the revert route."""

    def classes(self):
        c = copy.deepcopy(tcr.CLASSES)
        c["classes"]["rightsizing"] = {"enabled": True, "summary": "x", "allow": ["k8s/asgard/apps/*/helmrelease.yaml"]}
        return c

    def setUp(self):
        self.r = trd.Rig(classes=self.classes())
        self.world = World()
        self.world.t = int(self.r.clock.t)
        self.r.tb.watches.make_vm = lambda: rightsizing.VM("http://vm.invalid", self.world.fetch)

    def tearDown(self):
        self.r.close()

    def merged(self, spec=SPEC):
        cr = self.r.tb.cr.create(source="operator", class_="rightsizing", title="Trim web", body="Trim web.\n" + rw.spec_block(spec),
                                 source_ref="rightsizing-app/Deployment/web", created_by=tcr.OP)
        self.r.appr("POST", f"/change-requests/{cr['id']}/decision", {"decision": "approve", "by": tcr.OP})
        self.assertEqual(self.r.call(tcr.T_AUTH, "POST", "/change-requests/claim", {})[0], 200)
        self.assertEqual(self.r.call(tcr.T_AUTH, "POST", f"/change-requests/{cr['id']}/report", {"state": "pr-open", "pr_url": tcr.PR, "branch": f"agent/rightsizing/{cr['id']}-trim-web"})[0], 200)
        st, out = self.r.call(tcr.T_AUTH, "POST", f"/change-requests/{cr['id']}/report", {"state": "merged"})
        self.assertEqual(st, 200, out)
        return cr["id"]

    def test_a_merged_rightsizing_request_starts_a_watch_and_other_classes_do_not(self):
        cid = self.merged()
        ws = self.r.tb.watches.list()
        self.assertEqual([(w["cr_id"], w["state"], w["controller"]) for w in ws], [(cid, "waiting", "app/Deployment/web")])
        st, cr = self.r.file(token=tcr.T_APPR)
        self.r.appr("POST", f"/change-requests/{cr['id']}/decision", {"decision": "approve", "by": tcr.OP})
        self.r.call(tcr.T_AUTH, "POST", "/change-requests/claim", {})
        self.r.call(tcr.T_AUTH, "POST", f"/change-requests/{cr['id']}/report", {"state": "pr-open", "pr_url": tcr.PR, "branch": f"agent/docs/{cr['id']}-x"})
        self.r.call(tcr.T_AUTH, "POST", f"/change-requests/{cr['id']}/report", {"state": "merged"})
        self.assertEqual(len(self.r.tb.watches.list()), 1)

    def test_the_verdict_lands_on_the_change_request_feed_and_in_the_digest_results(self):
        self.merged()
        self.world.roll()
        self.world.t += 300
        self.r.clock.t = self.world.t
        self.r.tb.watches.poll()
        self.world.pods[0]["oom_at"] = self.world.t + 60
        self.world.t += 7200
        self.r.clock.t = self.world.t
        self.r.tb.watches.poll()
        self.assertEqual(self.r.tb.watches.get(1)["state"], "regressed")
        feed = self.r.appr("GET", "/change-requests/feed?after=0")[1]["events"]
        watch = [e for e in feed if e["kind"] == "watch"]
        self.assertEqual(len(watch), 1)
        self.assertEqual(watch[0]["data"]["state"], "regressed")
        self.r.feed(trd.over())
        d = self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})[1]["digest"]
        self.assertEqual((d["results"][0]["verdict"], d["results"][0]["target"]), ("regressed", "app/Deployment/web"))

    def test_a_limit_cut_is_only_suggested_after_the_request_cut_held(self):
        limit = trd.rs_finding("web", ns="app", finding="memory-over-limit", metric="memory-limit", limit_mib=4096.0, proposed_limit_mib=1024.0, freed_bytes=3072 * MIB)
        self.r.feed(limit)
        self.assertEqual(self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})[1]["digest"]["suggestions"], [])
        fc = self.r.tb.fc.list(kind="rightsizing")[0]
        self.assertFalse(fc["draftable"])
        # the request cut held through its watch
        self.merged()
        self.world.roll()
        self.world.t += 300
        self.r.clock.t = self.world.t
        self.r.tb.watches.poll()
        self.world.t += 73 * H
        self.r.clock.t = self.world.t
        self.r.tb.watches.poll()
        self.assertTrue(self.r.tb.watches.held("app/Deployment/web"))
        self.r.feed(limit)
        digest = self.r.tb.rs.latest()
        self.r.appr("POST", f"/rightsizing/digest/{digest['id']}/message", {"message_ref": "1"})
        d = self.r.appr("POST", "/rightsizing/digest", {"by": tcr.OP})[1]["digest"]
        self.assertEqual([s["finding"] for s in d["suggestions"]], ["memory-over-limit"])

    def test_revert_files_a_request_once_for_an_operator_and_only_while_it_makes_sense(self):
        self.merged()
        self.assertEqual(self.r.appr("POST", "/rightsizing/watches/1/revert", {"by": tcr.OP})[0], 409)   # still waiting: nothing changed yet
        self.world.roll()
        self.world.t += 300
        self.r.clock.t = self.world.t
        self.r.tb.watches.poll()
        self.assertEqual(self.r.appr("POST", "/rightsizing/watches/1/revert", {"by": "999"})[0], 403)
        st, cr = self.r.appr("POST", "/rightsizing/watches/1/revert", {"by": tcr.OP})
        self.assertEqual((st, cr["class"], cr["state"]), (200, "rightsizing", "pending"))
        self.assertEqual(rw.parse_spec(cr["body"])["kind"], "revert")
        self.assertEqual(self.r.appr("POST", "/rightsizing/watches/1/revert", {"by": tcr.OP})[0], 409)   # already filed
        self.assertEqual(self.r.appr("GET", "/rightsizing/watches?state=watching")[1]["watches"][0]["reverted_by"], cr["id"])

    def test_the_watch_routes_are_approver_only(self):
        for tok in (tcr.T_AGENT, tcr.T_AUTH):
            self.assertEqual(self.r.call(tok, "GET", "/rightsizing/watches")[0], 403)


class BotText(unittest.TestCase):
    def test_a_verdict_event_becomes_one_notice_and_a_regression_carries_the_revert_button_id(self):
        state = drafts.State(Path("/tmp/none.json"))
        cr = {"id": 7, "state": "merged", "pr_url": "https://github.com/XIIISins/homelab/pull/300", "message_ref": "5", "thread_id": "9"}
        ev = {"id": 1, "kind": "watch", "ts": 1, "data": {"watch": 3, "state": "regressed", "reasons": ["OOMKilled since the new values went live: main"]}, "change_request": cr}
        acts = [a for a in drafts.plan([ev], state) if a.kind == "watch"]
        self.assertEqual(len(acts), 1)
        self.assertIn("**regressed**", drafts.watch_text(acts[0].data, cr["pr_url"]))
        self.assertIn("Nothing was reverted", drafts.watch_text(acts[0].data))
        self.assertEqual(drafts.REVERT_ID.match(drafts.revert_custom_id(3)).group(1), "3")
        state.announced.add(acts[0].text)
        self.assertEqual([a for a in drafts.plan([ev], state) if a.kind == "watch"], [])        # announced once
        held = {**ev, "data": {"watch": 3, "state": "held", "warnings": []}}
        self.assertIn("**held**", drafts.watch_text(held["data"]))
        self.assertEqual([a.kind for a in drafts.plan([{**ev, "data": {"watch": 3, "state": "watching"}}], state) if a.kind == "watch"], [])


if __name__ == "__main__":
    unittest.main()
