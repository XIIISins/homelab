"""Phase 10h1: the shadow forecasting runner (aiops/toolbelt/forecast_run.py) against a fake VictoriaMetrics."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))

import forecast  # noqa: E402
import forecast_run  # noqa: E402

DAY = 86400
NOW = 1_790_000_000.0


def filling_matrix(now=NOW, days=14, start=0.40, per_day=0.02):
    """An hourly PVC used-ratio series rising linearly: crosses 0.85 in about (0.85 - now_level) / per_day days."""
    vals = [[now - days * DAY + h * 3600, str(start + per_day * (h / 24))] for h in range(days * 24 + 1)]
    return {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"namespace": "outline", "persistentvolumeclaim": "data-outline-0"}, "values": vals}]}}


class Parse(unittest.TestCase):
    def test_labels_are_stable_and_readable(self):
        self.assertEqual(forecast_run.label_of({"namespace": "outline", "persistentvolumeclaim": "data-0"}), "outline/data-0")
        self.assertEqual(forecast_run.label_of({"instance": "frigg", "job": "node"}), "frigg/node")
        self.assertEqual(forecast_run.label_of({"__name__": "vl_data_size_bytes"}), "vl_data_size_bytes")
        self.assertEqual(forecast_run.label_of({}), "series")

    def test_matrix_parsing_drops_garbage_samples_and_refuses_a_failed_query(self):
        rows = forecast_run.parse_matrix({"status": "success", "data": {"result": [
            {"metric": {"namespace": "a", "persistentvolumeclaim": "b"}, "values": [[1, "0.5"], [2, "NaN-ish"], [3, None], [4, "0.7"]]}]}})
        self.assertEqual(rows, [("a/b", [(1.0, 0.5), (4.0, 0.7)])])
        with self.assertRaises(ValueError):
            forecast_run.parse_matrix({"status": "error", "error": "boom"})
        self.assertEqual(forecast_run.parse_matrix({"status": "success", "data": {}}), [])


class FakeVM(BaseHTTPRequestHandler):
    payload: dict = {}
    seen: list = []

    def log_message(self, *a):
        return

    def do_GET(self):  # noqa: N802
        FakeVM.seen.append(self.path)
        body = json.dumps(FakeVM.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Runner(unittest.TestCase):
    def setUp(self):
        FakeVM.payload, FakeVM.seen = filling_matrix(), []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeVM)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def test_the_query_function_sends_a_range_query_and_returns_series(self):
        rows = forecast_run.make_query(self.url)("up", NOW - DAY, NOW, "1h")
        self.assertEqual(rows[0][0], "outline/data-outline-0")
        self.assertGreater(len(rows[0][1]), 300)  # full resolution, not the thinned series the agent tool returns
        self.assertIn("/api/v1/query_range?", FakeVM.seen[0])
        self.assertIn("step=1h", FakeVM.seen[0])

    def test_a_filling_pvc_becomes_one_finding_in_the_shadow_log_and_dedup_holds_across_runs(self):
        log, state = self.tmp / "f.jsonl", self.tmp / "state.json"
        argv = ["--log", str(log), "--url", self.url, "--state", str(state)]
        self.assertEqual(forecast_run.main(argv, now=NOW), 0)
        lines = [json.loads(x) for x in log.read_text().splitlines()]
        slow = [x for x in lines if x["kind"] == "slow-fill"]
        self.assertEqual(len(slow), 1)
        self.assertEqual(slow[0]["target"], "outline/data-outline-0")
        self.assertLess(slow[0]["days_to_full"], 14)
        n = len(lines)
        self.assertEqual(forecast_run.main(argv, now=NOW + 3600), 0)  # the next timer run an hour later: same finding, no repeat
        self.assertEqual(len(log.read_text().splitlines()), n)
        self.assertTrue(state.exists())

    def test_an_unreachable_endpoint_skips_without_crashing_or_writing(self):
        log = self.tmp / "none.jsonl"
        self.srv.shutdown()
        self.srv.server_close()
        self.assertEqual(forecast_run.main(["--log", str(log), "--url", self.url], now=NOW), 0)
        self.assertFalse(log.exists())

    def test_a_corrupt_state_file_only_means_a_repeat_report(self):
        log, state = self.tmp / "f.jsonl", self.tmp / "state.json"
        state.write_text("{not json")
        self.assertEqual(forecast_run.main(["--log", str(log), "--url", self.url, "--state", str(state)], now=NOW), 0)
        self.assertTrue(log.exists())


if __name__ == "__main__":
    unittest.main()
