"""Phase 10d2: hypervisor-aware grouping - the placement map, its hot reload, and the NetBox sync job."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))
sys.path.insert(0, str(REPO / "aiops" / "tools"))

import core  # noqa: E402
import normalize  # noqa: E402
import placement_sync  # noqa: E402
import test_toolbelt as base  # noqa: E402


class HotReload(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.f = self.tmp / "placement.json"
        self.clock = base.Clock()
        self.tb = core.Toolbelt(core.Config(placement_file=self.f), normalize.load_routes(), base.KNOWN, clock=self.clock, audit=lambda r: None)

    def write(self, data, bump=1.0):
        self.f.write_text(json.dumps(data) if not isinstance(data, str) else data)
        st = self.f.stat()
        os.utime(self.f, (st.st_atime, st.st_mtime + bump))  # force a distinct mtime even on coarse filesystems

    def test_a_missing_file_means_unplaced_and_never_an_error(self):
        r = self.tb.ingest_zabbix(base.ev(host="a1"))
        self.assertEqual(self.tb.group(r["incident_id"])["group_key"], "unplaced")

    def test_a_burst_on_one_node_is_one_incident_and_another_node_is_separate(self):
        self.write({"a1": "skuld", "a2": "skuld", "b1": "urd"})
        a1 = self.tb.ingest_zabbix(base.ev(host="a1"))
        a2 = self.tb.ingest_zabbix(base.ev(host="a2"))
        b1 = self.tb.ingest_zabbix(base.ev(host="b1"))
        self.assertEqual((a2["action"], a2["incident_id"]), ("member", a1["incident_id"]))
        self.assertEqual(b1["action"], "leader")
        self.assertEqual(self.tb.group(a1["incident_id"])["hypervisors"], ["skuld"])

    def test_the_map_is_reloaded_when_the_file_changes(self):
        self.write({"a1": "skuld"})
        self.assertEqual(self.tb.group(self.tb.ingest_zabbix(base.ev(host="a1"))["incident_id"])["group_key"], "node:skuld")
        self.write({"a1": "verd"}, bump=2.0)
        self.clock.t += 200  # a new window
        self.assertEqual(self.tb.group(self.tb.ingest_zabbix(base.ev(host="a1", event_id="9", trigger="Other check"))["incident_id"])["group_key"], "node:verd")

    def test_a_malformed_file_keeps_the_last_good_map(self):
        self.write({"a1": "skuld"})
        self.tb.ingest_zabbix(base.ev(host="a1"))
        for junk in ("{not json", json.dumps(["a"]), json.dumps({"a1": 3})):
            self.write(junk, bump=5.0 + len(junk))
            self.clock.t += 200
            r = self.tb.ingest_zabbix(base.ev(host="a1", event_id="x" + str(len(junk)), trigger=f"Check {len(junk)}"))
            self.assertEqual(self.tb.group(r["incident_id"])["group_key"], "node:skuld", junk)


class FakeNetbox:
    def __init__(self, pages):
        outer = self
        self.calls = 0

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_GET(self):  # noqa: N802
                outer.calls += 1
                if self.headers.get("Authorization") != "Bearer nbt_k.s":
                    self.send_response(403)
                    self.end_headers()
                    return
                idx = int(self.path.split("page=")[1].split("&")[0]) if "page=" in self.path else 0
                results = pages[idx]
                if "brief" in self.path:  # real NetBox: ANY brief value (even 0) means brief mode, which has no `device`
                    results = [{"id": 1, "name": r["name"], "url": "x"} for r in results]
                body = {"results": results, "next": f"{outer.url}/api/virtualization/virtual-machines/?page={idx + 1}" if idx + 1 < len(pages) else None}
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def vm(name, node):
    return {"name": name, "device": {"name": node} if node else None}


class Sync(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.out = self.tmp / "placement.json"
        self.creds = self.tmp / "netbox.json"

    def server(self, pages):
        nb = FakeNetbox(pages)
        self.addCleanup(nb.close)
        self.creds.write_text(json.dumps({"value": "nbt_k.s", "url": nb.url}))
        return nb

    def test_follows_pagination_and_skips_unplaced_guests(self):
        self.server([[vm("a", "urd"), vm("b", "verd")], [vm("c", "skuld"), vm("floating", None)]])
        self.assertEqual(placement_sync.main(["--creds", str(self.creds), "--out", str(self.out), "--min-guests", "3"]), 0)
        self.assertEqual(json.loads(self.out.read_text()), {"a": "urd", "b": "verd", "c": "skuld"})

    def test_an_implausibly_small_answer_keeps_the_previous_map(self):
        self.out.write_text(json.dumps({"keep": "me"}))
        self.server([[vm("a", "urd")]])
        self.assertEqual(placement_sync.main(["--creds", str(self.creds), "--out", str(self.out), "--min-guests", "5"]), 1)
        self.assertEqual(json.loads(self.out.read_text()), {"keep": "me"})

    def test_netbox_down_or_a_refused_token_keeps_the_previous_map(self):
        self.out.write_text(json.dumps({"keep": "me"}))
        self.creds.write_text(json.dumps({"value": "nbt_k.s", "url": "http://127.0.0.1:9"}))  # nothing listens
        self.assertEqual(placement_sync.main(["--creds", str(self.creds), "--out", str(self.out)]), 1)
        nb = self.server([[vm("a", "urd")] * 20])
        self.creds.write_text(json.dumps({"value": "wrong", "url": nb.url}))
        self.assertEqual(placement_sync.main(["--creds", str(self.creds), "--out", str(self.out)]), 1)
        self.assertEqual(json.loads(self.out.read_text()), {"keep": "me"})


if __name__ == "__main__":
    unittest.main()
