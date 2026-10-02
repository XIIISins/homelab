"""Phase 10d3: the acceptance harness judges runs correctly - positive case plus negative controls."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "tools"))

import replay_run  # noqa: E402

SC = replay_run.load_scenario("canary-agent-down")


def good_run():
    return {
        "incident_id": 7, "state": "posted", "model": "claude-sonnet-5-5", "no_recording": 0,
        "calls": [], "diagnosis": {
            "layer": "workload", "confidence": "high", "needs_human": False, "summary": "the agent service on canary-1 is down",
            "evidence": [{"tool": "reach.tcp", "args": {"host": "canary-1", "port": 10050}, "finding": "closed"},
                         {"tool": "pve.guests", "args": {}, "finding": "running"}]}}


class Evaluate(unittest.TestCase):
    def test_a_correct_run_passes(self):
        self.assertEqual(replay_run.evaluate(SC, good_run()), [])

    def test_negative_controls_each_failure_mode_is_caught(self):
        cases = {
            "forbidden layer": lambda r: r["diagnosis"].update(layer="hypervisor"),
            "layer outside the allowed set": lambda r: r["diagnosis"].update(layer="drift"),
            "too little evidence": lambda r: r["diagnosis"].update(evidence=r["diagnosis"]["evidence"][:1]),
            "cites none of the required tools": lambda r: r["diagnosis"].update(
                evidence=[{"tool": "registry.runbooks", "args": {}, "finding": "x"}] * 2),
            "never posted": lambda r: r.update(state="running"),
            "no diagnosis accepted": lambda r: r.update(diagnosis=None),
            "thrashing on unrecorded calls": lambda r: r.update(no_recording=replay_run.MAX_NO_RECORDING + 1),
            "high confidence in unknown": lambda r: r["diagnosis"].update(layer="unknown", confidence="high"),
        }
        for name, mutate in cases.items():
            run = copy.deepcopy(good_run())
            mutate(run)
            self.assertTrue(replay_run.evaluate(SC, run), f"{name} should FAIL the scenario")

    def test_unknown_with_low_confidence_is_not_a_pass_unless_the_scenario_allows_it(self):
        run = good_run()
        run["diagnosis"].update(layer="unknown", confidence="low", evidence=[])
        fails = replay_run.evaluate(SC, run)
        self.assertTrue(any("not one of" in f for f in fails))  # a calibrated "I don't know" is not the right answer here


class Scenarios(unittest.TestCase):
    def test_every_committed_scenario_loads_and_its_expectations_are_satisfiable_by_its_own_recordings(self):
        for f in sorted((REPO / "aiops" / "replays").glob("*/scenario.json")):
            sc = json.loads(f.read_text())
            tools_recorded = {c["tool"] for c in sc["calls"]}
            need = sc["expect"].get("must_cite_any_of", [])
            self.assertTrue(not need or tools_recorded & set(need), f"{f.parent.name}: nothing recorded for any required tool")
            self.assertGreaterEqual(len(tools_recorded), sc["expect"].get("min_evidence", 1), f.parent.name)


class ScenarioLint(unittest.TestCase):
    """A malformed recording must fail CI, not surface as a baffling NO_RECORDING in the middle of an acceptance run."""

    def setUp(self):
        import shutil
        import tempfile

        import lint

        self.lint = lint
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copytree(REPO / "aiops" / "toolbelt", self.tmp / "aiops" / "toolbelt")
        self.sc = copy.deepcopy(SC)

    def errors(self, sc):
        d = self.tmp / "aiops" / "replays" / "t"
        d.mkdir(parents=True, exist_ok=True)
        (d / "scenario.json").write_text(json.dumps(sc))
        return self.lint.check_replays(self.tmp)

    def test_the_committed_scenario_is_clean(self):
        self.assertEqual(self.errors(self.sc), [])

    def test_each_malformation_is_reported(self):
        def bad_tool(sc): sc["calls"][0]["tool"] = "kube.delete"
        def bad_args(sc): sc["calls"][0]["args"] = {"nope": 1}
        def dup(sc): sc["calls"].append(copy.deepcopy(sc["calls"][0]))
        def no_response(sc): del sc["calls"][0]["response"]
        def overlap(sc): sc["expect"]["layers_forbidden"] = ["workload"]
        def bad_event(sc): sc["event"]["status"] = "WAT"
        def unknown_layer(sc): sc["expect"]["layers_allowed"] = ["cloud"]
        def empty(sc): sc["calls"] = []
        def bad_cite(sc): sc["expect"]["must_cite_any_of"] = ["shell.bash"]
        for fn in (bad_tool, bad_args, dup, no_response, overlap, bad_event, unknown_layer, empty, bad_cite):
            sc = copy.deepcopy(SC)
            fn(sc)
            self.assertTrue(self.errors(sc), fn.__name__)


if __name__ == "__main__":
    unittest.main()
