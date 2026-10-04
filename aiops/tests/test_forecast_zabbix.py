"""Phase 10h1: Zabbix as a forecast source (aiops/toolbelt/forecast_zabbix.py) and the run's per-target stats / current-findings file.
The fake `call` mirrors the real API shapes seen on 2026-10-04 (item keys, trend rows), including a template item with no host."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "aiops" / "toolbelt"))

import forecast as fc  # noqa: E402
import forecast_zabbix as fz  # noqa: E402

DAY = 86400
NOW = 1_791_000_000.0


def hourly(start, end, f):
    t, out = start - start % 3600, []
    while t <= end:
        out.append((t, f((t - start) / DAY)))
        t += 3600
    return out


class FakeZabbix:
    """A small estate: hugin (rising /, flat /data), a template, urd+verd copies of the PVE storage items, memory on hugin."""

    def __init__(self, root_rate=0.01):
        self.calls, self.root_rate = [], root_rate
        start = NOW - 15 * DAY
        self.series = {
            "101": hourly(start, NOW, lambda d: 67.0 + 100 * self.root_rate * d),        # hugin / pused 67% rising root_rate/day (1%/day: 82% now, 90% in ~8 days)
            "102": hourly(start, NOW, lambda d: 30.0),                                   # hugin /data flat
            "201": hourly(start, NOW, lambda d: 130e9 + 1.5e9 * d),                     # urd local-lvm used (bytes), rising
            "202": hourly(start, NOW, lambda d: 945e9),                                  # urd local-lvm total
            "203": hourly(start, NOW, lambda d: 205e9),                                  # pbs-backup used (flat)
            "204": hourly(start, NOW, lambda d: 257.7e9),
            "301": hourly(start, NOW, lambda d: 90.0 - 4.0 * d),                         # hugin memory available % falling 4 pts/day (30% left now: 90% used in ~5 days)
        }

    def __call__(self, method, params):
        self.calls.append(method)
        assert method.endswith(".get"), method
        if method == "host.get":
            return [{"hostid": "10", "host": "hugin"}, {"hostid": "11", "host": "urd"}, {"hostid": "12", "host": "verd"}]
        if method == "item.get":
            key = params["search"]["key_"]
            if key == "vfs.fs.dependent.size[":
                return [{"itemid": "101", "hostid": "10", "key_": "vfs.fs.dependent.size[/,pused]", "value_type": "0"},
                        {"itemid": "102", "hostid": "10", "key_": "vfs.fs.dependent.size[/data,pused]", "value_type": "0"},
                        {"itemid": "103", "hostid": "10", "key_": "vfs.fs.dependent.size[/,total]", "value_type": "3"},
                        {"itemid": "104", "hostid": "999", "key_": "vfs.fs.dependent.size[/,pused]", "value_type": "0"}]  # a template: no such host
            if key == "proxmox.node.disk[":
                return [{"itemid": "201", "hostid": "11", "key_": "proxmox.node.disk[urd,local-lvm]", "value_type": "3"},
                        {"itemid": "201", "hostid": "12", "key_": "proxmox.node.disk[urd,local-lvm]", "value_type": "3"},   # a copy on another PVE host
                        {"itemid": "203", "hostid": "11", "key_": "proxmox.node.disk[urd,pbs-backup]", "value_type": "3"},
                        {"itemid": "299", "hostid": "11", "key_": "proxmox.node.disk[urd,local]", "value_type": "3"}]
            if key == "proxmox.node.maxdisk[":
                return [{"itemid": "202", "hostid": "11", "key_": "proxmox.node.maxdisk[urd,local-lvm]", "value_type": "3"},
                        {"itemid": "204", "hostid": "11", "key_": "proxmox.node.maxdisk[urd,pbs-backup]", "value_type": "3"}]
            if key == "vm.memory.size[pavailable]":
                return [{"itemid": "301", "hostid": "10", "key_": "vm.memory.size[pavailable]", "value_type": "0"},
                        {"itemid": "302", "hostid": "999", "key_": "vm.memory.size[pavailable]", "value_type": "0"}]
            return []
        if method == "trend.get":
            field, rows = params["output"][-1], []
            for i in params["itemids"]:
                for clock, v in self.series.get(i, []):
                    if clock >= params["time_from"]:
                        rows.append({"itemid": i, "clock": str(int(clock)), field: str(v)})
            return rows
        raise AssertionError(method)


def target(name):
    return next(t for t in fc.DEFAULT_TARGETS if t.name == name)


class Source(unittest.TestCase):
    def setUp(self):
        self.z = FakeZabbix()
        self.src = fz.ZabbixSource(self.z)

    def test_filesystems_become_fractions_per_host_and_mount_and_templates_are_skipped(self):
        rows = dict(self.src.fs_used(NOW - 15 * DAY, NOW))
        self.assertEqual(sorted(rows), ["hugin:/", "hugin:/data"])        # the template item and the non-pused item are not series
        self.assertAlmostEqual(rows["hugin:/data"][0][1], 0.30)
        self.assertGreater(rows["hugin:/"][-1][1], rows["hugin:/"][0][1])
        self.assertLessEqual(max(v for _, v in rows["hugin:/"]), 1.0)

    def test_pve_storage_is_used_over_total_deduplicated_and_limited_to_the_named_storages(self):
        rows = dict(self.src.pve_storage(("local-lvm", "pbs-backup"), NOW - 15 * DAY, NOW))
        self.assertEqual(sorted(rows), ["urd/local-lvm", "urd/pbs-backup"])  # `local` has no maxdisk item; the duplicate copy is one series
        self.assertAlmostEqual(rows["urd/pbs-backup"][0][1], 205e9 / 257.7e9, places=4)
        self.assertEqual(dict(self.src.pve_storage(("munin-nfs",), NOW - 15 * DAY, NOW)), {})

    def test_memory_is_the_daily_low_water_mark_as_used_fraction(self):
        rows = dict(self.src.memory_used(NOW - 15 * DAY, NOW))
        self.assertEqual(list(rows), ["hugin"])
        self.assertAlmostEqual(rows["hugin"][0][1], 1 - 0.90, places=2)
        self.assertGreater(rows["hugin"][-1][1], rows["hugin"][0][1])

    def test_only_get_methods_are_ever_called(self):
        for t in ("fleet-fs-used", "pve-storage-used", "memory-used"):
            self.src.query(target(t), NOW - 15 * DAY, NOW)
        self.assertTrue(set(self.z.calls) <= {"host.get", "item.get", "trend.get"}, self.z.calls)

    def test_an_unknown_selector_is_an_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            self.src.query(fc.Target("x", "m", zabbix=("bogus",), capacity=0.9), 0, 1)


class Run(unittest.TestCase):
    def run_pass(self, z, tmp, **kw):
        stats = {}
        found = fc.run_once(lambda *a: [], NOW, Path(tmp) / "log.jsonl", sources={"zabbix": fz.ZabbixSource(z).query}, stats=stats,
                            current_path=Path(tmp) / "current.json", **kw)
        return found, stats

    def test_a_filesystem_filling_on_the_fleet_is_found_with_an_eta_before_the_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            found, stats = self.run_pass(FakeZabbix(root_rate=0.01), tmp)
            slow = [f for f in found if f.kind == "slow-fill" and f.target == "hugin:/"]
            self.assertEqual(len(slow), 1)
            self.assertAlmostEqual(slow[0].days_to_full, (0.90 - (0.67 + 0.15)) / 0.01, delta=1.5)  # 67% + 15 days at 1%/day = 82%, to 90%
            self.assertEqual(stats["fleet-fs-used"]["series"], 2)
            cur = json.loads((Path(tmp) / "current.json").read_text())
            self.assertTrue(any(f["target"] == "hugin:/" for f in cur["findings"]))
            self.assertEqual(cur["stats"]["pve-storage-used"]["series"], 2)

    def test_a_flat_estate_is_silent_and_says_what_it_looked_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            found, stats = self.run_pass(FakeZabbix(root_rate=0.0), tmp)
            self.assertEqual([f for f in found if f.target in ("hugin:/", "hugin:/data", "urd/pbs-backup")], [])
            self.assertTrue(all(s.get("series", 0) >= 1 for n, s in stats.items() if n in ("fleet-fs-used", "pve-storage-used", "memory-used")), stats)
            self.assertEqual(json.loads((Path(tmp) / "current.json").read_text())["ts"], NOW)

    def test_memory_creep_is_a_slow_fill_on_the_daily_high(self):
        with tempfile.TemporaryDirectory() as tmp:
            found, _ = self.run_pass(FakeZabbix(), tmp)
            mem = [f for f in found if f.target == "hugin" and f.kind == "slow-fill"]
            self.assertEqual(len(mem), 1)
            self.assertLess(mem[0].days_to_full, 14)

    def test_a_failing_source_is_reported_not_silent_and_does_not_hide_the_others(self):
        class Down(FakeZabbix):
            def __call__(self, method, params):
                if method == "item.get" and params["search"]["key_"].startswith("proxmox"):
                    raise OSError("connection refused")
                return super().__call__(method, params)

        with tempfile.TemporaryDirectory() as tmp:
            found, stats = self.run_pass(Down(root_rate=0.01), tmp)
            self.assertIn("OSError", stats["pve-storage-used"]["error"])
            self.assertTrue(any(f.target == "hugin:/" for f in found))  # the filesystem target still ran

    def test_without_a_zabbix_source_those_targets_say_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = {}
            fc.run_once(lambda *a: [], NOW, Path(tmp) / "log.jsonl", stats=stats)
            self.assertEqual(stats["fleet-fs-used"], {"error": "no zabbix source configured"})


class Config(unittest.TestCase):
    def test_the_default_targets_validate(self):
        self.assertEqual(fc.validate_targets(fc.DEFAULT_TARGETS), [])
        bad = [fc.Target("a", "m", zabbix=("fs",), promql="q", capacity=0.9), fc.Target("b", "m", zabbix=("pve", ()), capacity=0.9),
               fc.Target("c", "m", zabbix=("nope",), capacity=0.9)]
        errs = "\n".join(fc.validate_targets(bad))
        self.assertIn("a zabbix target has no promql", errs)
        self.assertIn("bad zabbix selector", errs)


if __name__ == "__main__":
    unittest.main()
