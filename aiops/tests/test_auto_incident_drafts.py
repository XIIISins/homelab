"""Phase 10h3: a resolved incident that crossed the bar gets its write-up request filed by the Toolbelt itself (the operator still
approves it on its card). The bar and the sweep live in aiops/toolbelt/incident_draft.py and core.py."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for sub in ("toolbelt", "tools", "tests", "bot"):
    sys.path.insert(0, str(REPO / "aiops" / sub))

import drafts  # noqa: E402  (the bot's copy of the request text)
import incident_draft as idr  # noqa: E402
import test_change_requests as tcr  # noqa: E402

DAY = 86400


class Estate(unittest.TestCase):
    def setUp(self):
        self.r = tcr.Rig()
        self.tb = self.r.tb
        self.tb.cfg.auto_incident_drafts = True
        self.t0 = int(self.r.clock.t) - 3 * 3600
        self.n = 0

    def tearDown(self):
        self.r.close()

    def incident(self, hosts=("hugin",), minutes=10, state="resolved", replay=None, resolved=True, proposals=(), ago=3600):
        """One incident row with its alerts (direct rows: the bar reads the tables, however they got there)."""
        self.n += 1
        opened = int(self.r.clock.t) - ago - minutes * 60
        res = int(self.r.clock.t) - ago if resolved else None
        cur = self.tb.db.execute("INSERT INTO incidents(group_key, state, opened_at, window_ends_at, resolved_at, replay) VALUES (?,?,?,?,?,?)",
                                 (f"g{self.n}", state, opened, opened + 90, res, replay))
        iid = cur.lastrowid
        for h in hosts:
            labels = {"aiops_canary": "true"} if h.startswith("canary-") else {}
            self.tb.db.execute("INSERT INTO alerts(fingerprint, incident_id, status, severity, first_seen, last_seen, count, host, alert_json) VALUES (?,?,?,?,?,?,?,?,?)",
                               (f"fp{self.n}{h}", iid, "resolved", "critical", opened, opened, 1, h, json.dumps({"labels": labels})))
        for st in proposals:
            self.tb.db.execute("INSERT INTO proposals(incident_id, source, action_id, params_json, params_hash, tier, target, reason, state, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (iid, "diagnosis", "restart-unit", "{}", f"h{self.n}{st}", "T1", hosts[0], "x", st, opened, opened + 100))
        return iid

    def crs(self):
        return [self.tb.cr.view(i) for i in [x["id"] for x in self.tb.db.execute("SELECT id FROM change_requests ORDER BY id")]]


class Bar(Estate):
    def why(self, iid):
        return idr.draft_reason(self.tb.db, iid)

    def test_a_short_single_alert_incident_with_no_action_is_not_worth_a_write_up(self):
        self.assertIsNone(self.why(self.incident(minutes=5)))

    def test_each_part_of_the_plans_bar_qualifies_on_its_own(self):
        self.assertIn("lasted 45 minutes", self.why(self.incident(minutes=45)))
        self.assertIn("3 alerts", self.why(self.incident(hosts=("a", "b", "c"), minutes=2)))
        for state in ("succeeded", "failed", "verify_failed", "rejected"):
            self.assertIn("action", self.why(self.incident(minutes=2, proposals=(state,))), state)
        self.assertIsNone(self.why(self.incident(minutes=2, proposals=("pending", "expired", "cancelled"))))  # nothing ran or was refused

    def test_replays_unresolved_and_the_canary_pools_own_faults_are_never_written_up(self):
        self.assertIsNone(self.why(self.incident(minutes=90, replay="canary-agent-down")))
        self.assertIsNone(self.why(self.incident(minutes=90, state="posted", resolved=False)))
        self.assertIsNone(self.why(self.incident(hosts=("canary-1", "canary-2", "canary-3"), minutes=90)))
        self.assertIn("minutes", self.why(self.incident(hosts=("canary-1", "hugin"), minutes=90)))  # one real host in the mix is real
        self.assertIsNone(idr.draft_reason(self.tb.db, 9999))

    def test_thresholds_are_parameters(self):
        iid = self.incident(minutes=20)
        self.assertIsNone(idr.draft_reason(self.tb.db, iid))
        self.assertIn("20 minutes", idr.draft_reason(self.tb.db, iid, min_minutes=15))


class Sweep(Estate):
    def test_it_files_one_pending_docs_request_naming_the_incident_and_never_refiles(self):
        iid = self.incident(minutes=45)
        self.assertEqual(self.tb.auto_incident_drafts(), [iid])
        (cr,) = self.crs()
        self.assertEqual((cr["state"], cr["class"], cr["source"], cr["source_ref"], cr["created_by"]), ("pending", "docs", "incident", f"incident-{iid}", "toolbelt"))
        self.assertEqual((cr["title"], cr["body"]), idr.incident_request(iid))
        self.assertEqual(self.tb.auto_incident_drafts(), [])
        # an operator who rejected it does not get it again
        self.r.call(tcr.T_APPR, "POST", f"/change-requests/{cr['id']}/decision", {"decision": "reject", "by": tcr.OP})
        self.assertEqual(self.tb.auto_incident_drafts(), [])
        self.assertEqual(len(self.crs()), 1)

    def test_a_manual_draft_for_the_same_incident_also_stops_an_automatic_one(self):
        iid = self.incident(minutes=45)
        title, body = drafts.incident_request(iid)
        self.r.file(token=tcr.T_APPR, **{"class": "docs", "source": "incident", "source_ref": f"incident-{iid}", "title": title, "body": body})
        self.assertEqual(self.tb.auto_incident_drafts(), [])

    def test_only_incidents_that_qualify_are_filed_and_old_ones_are_left_alone(self):
        keep = self.incident(minutes=45)
        self.incident(minutes=3)
        self.incident(minutes=300, ago=9 * DAY)       # resolved nine days ago: outside the 7-day lookback
        self.incident(minutes=60, replay="x")
        self.assertEqual(self.tb.auto_incident_drafts(), [keep])

    def test_the_daily_cap_defers_the_rest_to_the_next_day(self):
        ids = [self.incident(minutes=45 + i) for i in range(5)]
        self.assertEqual(self.tb.auto_incident_drafts(), ids[:3])
        self.assertEqual(self.tb.auto_incident_drafts(), [])
        self.r.clock.t += DAY
        for cr in self.crs():                         # free the pending slots like an operator would
            self.r.call(tcr.T_APPR, "POST", f"/change-requests/{cr['id']}/decision", {"decision": "reject", "by": tcr.OP})
        self.assertEqual(self.tb.auto_incident_drafts(), ids[3:])

    def test_a_request_cap_defers_without_losing_the_incident(self):
        self.r.tb.cr.cfg.max_pending = 1
        a, b = self.incident(minutes=45), self.incident(minutes=50)
        self.assertEqual(self.tb.auto_incident_drafts(), [a])
        self.assertEqual(self.tb.auto_incident_drafts(), [])                       # pending cap: b waits
        self.assertTrue(any(x.get("event") == "incident_draft_deferred" for x in self.r.audit))
        self.r.call(tcr.T_APPR, "POST", f"/change-requests/{self.crs()[0]['id']}/decision", {"decision": "reject", "by": tcr.OP})
        self.assertEqual(self.tb.auto_incident_drafts(), [b])

    def test_off_by_default_and_off_without_change_requests(self):
        self.tb.cfg.auto_incident_drafts = False
        self.incident(minutes=45)
        self.assertEqual(self.tb.auto_incident_drafts(), [])
        self.tb.cfg.auto_incident_drafts = True
        cr, self.tb.cr = self.tb.cr, None
        self.assertEqual(self.tb.auto_incident_drafts(), [])
        self.tb.cr = cr

    def test_the_filed_request_flows_through_the_normal_approval_path(self):
        iid = self.incident(minutes=45)
        self.tb.auto_incident_drafts()
        cid = self.crs()[0]["id"]
        st, out = self.r.call(tcr.T_APPR, "POST", f"/change-requests/{cid}/decision", {"decision": "approve", "by": tcr.OP})
        self.assertEqual((st, out["state"]), (200, "approved"))
        got = self.r.call(tcr.T_AUTH, "POST", "/change-requests/claim")[1]["change_request"]
        self.assertEqual((got["id"], got["source_ref"]), (cid, f"incident-{iid}"))


class Wording(unittest.TestCase):
    def test_the_bots_request_text_is_identical_to_the_toolbelts(self):
        for n in (1, 7, 40, 99999):
            self.assertEqual(drafts.incident_request(n), idr.incident_request(n))
        title, body = idr.incident_request(40)
        self.assertLess(len(body), 4000)
        self.assertIn('{"incident_id": 40}', body)


if __name__ == "__main__":
    unittest.main()
