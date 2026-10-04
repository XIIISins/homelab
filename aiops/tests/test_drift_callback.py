"""Phase 10h2: the drift hand-off to Gná in ansible/callback_plugins/hermod_summary.py, without Ansible installed (the plugin's one
import, CallbackBase, is stubbed)."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]


def load():
    for name in ("ansible", "ansible.plugins", "ansible.plugins.callback"):
        sys.modules.setdefault(name, types.ModuleType(name))

    class CallbackBase:
        def __init__(self):
            self._display = mock.Mock()

    sys.modules["ansible.plugins.callback"].CallbackBase = CallbackBase
    spec = importlib.util.spec_from_file_location("hermod_summary_under_test", REPO / "ansible/callback_plugins/hermod_summary.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = load()


def cb(mode="drift", nonprod=False, totals=None):
    c = MOD.CallbackModule()
    MOD.atexit.unregister(c._post_once)  # the test drives the hand-off itself
    c.mode, c.nonprod, c.wrapper_name = mode, nonprod, "drift-check.yml"
    for h, s in (totals or {}).items():
        c.totals[h].update(s)
    return c


class Gna(unittest.TestCase):
    def test_a_clean_run_says_nothing(self):
        self.assertIsNone(cb(totals={"frigg": {"ok": 120}})._gna_payload())

    def test_changes_hand_off_template_task_and_per_host_counts_only(self):
        with mock.patch.dict(os.environ, {"SEMAPHORE_TASK_ID": "1918", "SEMAPHORE_FOO": "x"}):
            p = cb(totals={"frigg": {"ok": 121, "changed": 2}, "mimir": {"ok": 50}, "hugin": {"changed": 1}})._gna_payload()
        self.assertEqual(p["changed"], {"frigg": 2, "hugin": 1})
        self.assertEqual((p["source"], p["template"], p["task_id"]), ("semaphore", "asgard-drift-check", 1918))
        self.assertEqual(p["failed"], [])
        self.assertNotIn("x", json.dumps(p).replace("semaphore_env_keys", ""))  # env keys are named, never valued

    def test_a_missing_or_odd_task_id_is_none(self):
        for val in (None, "", "abc", "1; rm"):
            env = {} if val is None else {"SEMAPHORE_TASK_ID": val}
            with mock.patch.dict(os.environ, env, clear=False):
                if val is None:
                    os.environ.pop("SEMAPHORE_TASK_ID", None)
                self.assertIsNone(cb(totals={"frigg": {"changed": 1}})._gna_payload()["task_id"], val)

    def test_not_for_failures_alone_non_prod_or_apply_runs(self):
        self.assertIsNone(cb(totals={"frigg": {"failed": 1}})._gna_payload())
        self.assertIsNone(cb(totals={"frigg": {"unreachable": 1}})._gna_payload())
        self.assertIsNone(cb(nonprod=True, totals={"canary-1": {"changed": 3}})._gna_payload())
        self.assertIsNone(cb(mode="apply", totals={"frigg": {"changed": 3}})._gna_payload())
        self.assertIsNone(cb(mode=None, totals={"frigg": {"changed": 3}})._gna_payload())

    def test_failed_hosts_ride_along_when_there_is_also_drift(self):
        p = cb(totals={"frigg": {"changed": 1}, "bifrost": {"unreachable": 1}})._gna_payload()
        self.assertEqual(p["failed"], ["bifrost"])

    def test_it_posts_only_when_the_url_is_set_and_never_raises(self):
        c = cb(totals={"frigg": {"changed": 2}})
        with mock.patch.dict(os.environ, {"AIOPS_DRIFT_URL": ""}), mock.patch.object(MOD.urllib.request, "urlopen") as op:
            c._post_gna()
            op.assert_not_called()
        with mock.patch.dict(os.environ, {"AIOPS_DRIFT_URL": "http://gna:8081/webhook/aiops/drift"}), \
                mock.patch.object(MOD.urllib.request, "urlopen", side_effect=OSError("down")):
            c._post_gna()  # must not raise
            c._display.warning.assert_called()
        sent = {}

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=0):
            sent["url"], sent["body"] = req.full_url, json.loads(req.data)
            return Resp()

        with mock.patch.dict(os.environ, {"AIOPS_DRIFT_URL": "http://gna:8081/webhook/aiops/drift"}), mock.patch.object(MOD.urllib.request, "urlopen", fake):
            c._post_gna()
        self.assertEqual(sent["url"], "http://gna:8081/webhook/aiops/drift")
        self.assertEqual(sent["body"]["changed"], {"frigg": 2})

    def test_the_hermod_path_is_unchanged_by_the_new_hand_off(self):
        c = cb(totals={"frigg": {"changed": 2}})
        payload = c._build_payload()
        self.assertEqual((payload["tag"], payload["type"]), ("alert", "warning"))
        self.assertTrue(payload["title"].startswith("Drift detected"))


if __name__ == "__main__":
    unittest.main()
