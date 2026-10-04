"""Phase 10h2: a drift-check run that would change hosts becomes an incident (aiops/toolbelt/drift.py + Toolbelt.ingest_drift).

The hand-off from the Ansible callback is a hint: every case here checks that the Toolbelt believes Semaphore's own recap, not
the message."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import core  # noqa: E402
import drift  # noqa: E402
import normalize  # noqa: E402
import test_toolbelt as base  # noqa: E402
import tools  # noqa: E402

ROUTES = normalize.load_routes()
RECAP_DRIFT = [
    {"output": "\x1b[0;33mfrigg\x1b[0m                      : \x1b[0;32mok=121 \x1b[0m \x1b[0;33mchanged=2   \x1b[0m unreachable=0    failed=0    skipped=58"},
    {"output": "mimir                      : ok=50  changed=0 unreachable=0 failed=0"},
]
RECAP_CLEAN = [{"output": "frigg : ok=121 changed=0 unreachable=0 failed=0"}]
RECAP_TWO = RECAP_DRIFT + [{"output": "hugin : ok=60 changed=1 unreachable=0 failed=0"}]


class FakeSem:
    """tasks: {id: (status, tpl_alias, output)}; `sequence` lets a task read as running for N polls before it finishes."""

    def __init__(self, tasks):
        self.tasks, self.hits, self.running_polls = tasks, [], 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_GET(self):  # noqa: N802
                outer.hits.append(self.path)
                p = self.path.split("/project/1")[-1]
                if p == "/tasks/last":
                    body = [{"id": i, "tpl_alias": t[1], "status": t[0]} for i, t in sorted(outer.tasks.items(), reverse=True)]
                elif p.endswith("/output"):
                    body = outer.tasks[int(p.split("/")[2])][2]
                elif p.startswith("/tasks/"):
                    i = int(p.split("/")[2])
                    if i not in outer.tasks:
                        self.send_response(404)
                        self.end_headers()
                        return
                    status = outer.tasks[i][0]
                    if outer.running_polls > 0:
                        outer.running_polls -= 1
                        status = "running"
                    body = {"id": i, "tpl_alias": outer.tasks[i][1], "status": status, "commit_hash": "fd8dd4f1234"}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/api"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class Pure(unittest.TestCase):
    def test_validate_accepts_the_hand_off_and_rejects_nonsense(self):
        ok = drift.validate({"template": "asgard-drift-check", "task_id": 7, "changed": {"frigg": 2}})
        self.assertEqual((ok["task_id"], ok["claimed"]), (7, {"frigg": 2}))
        self.assertIsNone(drift.validate({})["task_id"])
        for bad in ([], {"template": "asgard-nonprod-drift-check"}, {"task_id": True}, {"task_id": "7"}, {"task_id": -1},
                    {"changed": {"Bad Host": 1}}, {"changed": {"frigg": -1}}, {"changed": {"frigg": True}}, {"changed": []},
                    {"changed": {f"h{i}": 1 for i in range(201)}}):
            with self.assertRaises(drift.DriftError, msg=str(bad)[:60]):
                drift.validate(bad)

    def test_recap_reads_ansi_colored_play_recap_lines(self):
        r = drift.recap([o["output"] for o in RECAP_TWO] + ["TASK [x] ****", "changed: [frigg]"])
        self.assertEqual({h: v["changed"] for h, v in r.items()}, {"frigg": 2, "mimir": 0, "hugin": 1})

    def test_one_drifted_host_is_its_own_problem_and_many_are_the_fleet(self):
        one = drift.build_alert(ROUTES, {"task_id": 1, "changed": {"frigg": 2}, "commit": "abc"}, "2026-10-04T08:00:00Z", "asgard-drift-check")
        two = drift.build_alert(ROUTES, {"task_id": 2, "changed": {"frigg": 2, "hugin": 1}, "commit": ""}, "2026-10-04T08:00:00Z", "asgard-drift-check")
        other = drift.build_alert(ROUTES, {"task_id": 3, "changed": {"hugin": 1}, "commit": ""}, "2026-10-04T08:00:00Z", "asgard-drift-check")
        self.assertEqual((one["host"], one["source"], one["status"], one["runbook_id"]), ("frigg", "semaphore", "event", "RB-LXC-BOOT-DRIFT"))
        self.assertEqual(two["host"], "fleet")
        self.assertNotEqual(one["fingerprint"], other["fingerprint"])
        self.assertEqual(one["labels"]["semaphore_task"], "1")
        self.assertIsNone(drift.build_alert(ROUTES, {"task_id": 4, "changed": {}, "commit": ""}, "2026-10-04T08:00:00Z", "asgard-drift-check"))

    def test_the_built_alert_is_schema_valid(self):
        import jsonschema
        schema = json.loads((REPO / "aiops/schema/alert.v1.schema.json").read_text())
        a = drift.build_alert(ROUTES, {"task_id": 1, "changed": {"frigg": 2}, "commit": "abc"}, "2026-10-04T08:00:00Z", "asgard-drift-check")
        jsonschema.validate(a, schema)


class Ingest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.tmp / "creds").mkdir()
        self.slept = []

    def rig(self, tasks, **cfg):
        self.sem = FakeSem(tasks)
        self.addCleanup(self.sem.close)
        live = tools.LiveConfig(root=REPO, creds_dir=self.tmp / "creds")
        (self.tmp / "creds" / "semaphore.json").write_text(json.dumps({"value": "tok", "url": self.sem.url}))
        self.audit = []
        c = core.Config(replay_dir=self.tmp / "replays", live=live, drift_sleep=self.slept.append, **cfg)
        self.tb = core.Toolbelt(c, ROUTES, base.KNOWN, clock=base.Clock(), audit=self.audit.append)
        return self.tb

    def hint(self, **kw):
        return {"template": "asgard-drift-check", "task_id": 1918, "changed": {"frigg": 2}, **kw}

    def test_a_verified_drift_becomes_a_leader_incident_for_that_host(self):
        tb = self.rig({1918: ("success", "asgard-drift-check", RECAP_DRIFT)})
        out = tb.ingest_drift(self.hint())
        self.assertEqual(out["action"], "leader")
        self.assertEqual(out["drift"]["changed"], {"frigg": 2})
        g = tb.group(out["incident_id"])
        self.assertEqual((g["alerts"][0]["host"], g["alerts"][0]["check"]), ("frigg", "drift-detected"))

    def test_the_claim_is_not_believed_a_clean_run_starts_nothing(self):
        tb = self.rig({1918: ("success", "asgard-drift-check", RECAP_CLEAN)})
        out = tb.ingest_drift(self.hint(changed={"frigg": 99}))
        self.assertEqual((out["action"], out["reason"]), ("none", "no-drift-in-run"))
        self.assertEqual(tb.stats()["open_incidents"] if "open_incidents" in tb.stats() else 0, 0)

    def test_a_task_of_another_template_is_refused(self):
        tb = self.rig({1918: ("success", "asgard-apply", RECAP_DRIFT)})
        with self.assertRaises(core.Rejected) as cm:
            tb.ingest_drift(self.hint())
        self.assertEqual(cm.exception.status, 400)

    def test_without_a_task_id_the_newest_run_of_the_template_is_used_and_a_running_one_is_waited_for(self):
        tb = self.rig({1900: ("success", "asgard-drift-check", RECAP_CLEAN), 1918: ("success", "asgard-drift-check", RECAP_DRIFT),
                       1919: ("success", "asgard-apply", RECAP_CLEAN)})
        self.sem.running_polls = 2
        out = tb.ingest_drift(self.hint(task_id=None))
        self.assertEqual((out["action"], out["drift"]["task_id"]), ("leader", 1918))
        self.assertEqual(len(self.slept), 2)

    def test_a_run_that_never_finishes_is_a_504_not_a_hang(self):
        tb = self.rig({1918: ("success", "asgard-drift-check", RECAP_DRIFT)}, drift_wait_seconds=0)
        self.sem.running_polls = 99
        with self.assertRaises(core.Rejected) as cm:
            tb.ingest_drift(self.hint())
        self.assertEqual(cm.exception.status, 504)

    def test_the_same_task_twice_and_the_same_drift_within_a_day_are_duplicates(self):
        tb = self.rig({1918: ("success", "asgard-drift-check", RECAP_DRIFT), 1925: ("success", "asgard-drift-check", RECAP_DRIFT)})
        first = tb.ingest_drift(self.hint())
        again = tb.ingest_drift(self.hint())
        self.assertEqual((again["action"], again["reason"]), ("duplicate", "task-already-handled"))
        later = tb.ingest_drift(self.hint(task_id=1925))
        self.assertEqual((later["action"], later["reason"], later["incident_id"]), ("duplicate", "same-drift-recent", first["incident_id"]))

    def test_the_same_drift_a_day_later_raises_a_new_incident(self):
        tb = self.rig({1918: ("success", "asgard-drift-check", RECAP_DRIFT), 1925: ("success", "asgard-drift-check", RECAP_DRIFT)})
        first = tb.ingest_drift(self.hint())
        tb.clock.t += 86400 + 5
        later = tb.ingest_drift(self.hint(task_id=1925))
        self.assertEqual(later["action"], "leader")
        self.assertNotEqual(later["incident_id"], first["incident_id"])

    def test_bad_hand_offs_are_rejected_before_any_semaphore_call(self):
        tb = self.rig({1918: ("success", "asgard-drift-check", RECAP_DRIFT)})
        for bad in ({"template": "x"}, {"task_id": "1"}, []):
            with self.assertRaises(core.Rejected):
                tb.ingest_drift(bad)
        self.assertEqual(self.sem.hits, [])

    def test_two_drifted_hosts_make_one_fleet_incident(self):
        tb = self.rig({1918: ("success", "asgard-drift-check", RECAP_TWO)})
        out = tb.ingest_drift(self.hint(changed={"frigg": 2, "hugin": 1}))
        self.assertEqual(tb.group(out["incident_id"])["alerts"][0]["host"], "fleet")


if __name__ == "__main__":
    unittest.main()
