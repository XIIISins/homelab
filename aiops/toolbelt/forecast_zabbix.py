"""Zabbix as a forecast source (Phase 10h1): hourly trends for filesystem fill, Proxmox storage fill and memory headroom.

The fleet is watched by Zabbix, which keeps 31 days of raw history and 365 days of hourly trends (verified 2026-10-04), so a 14-day
robust fit has plenty of data. This adapter turns five families of items into the same `[(label, [(ts, value), ...])]` shape the
VictoriaMetrics source returns, scaled to a 0..1 fill ratio so one detector and one threshold convention serve every signal:

  fs      `vfs.fs.dependent.size[<mount>,pused]` per monitored host           -> pused / 100, daily maximum
  pve     `proxmox.node.disk[<node>,<storage>]` / `proxmox.node.maxdisk[...]` -> used / total (thin pools, the PBS datastore as PVE sees it)
  memory  `vm.memory.size[pavailable]` per monitored host                     -> 1 - pavailable / 100 (the daily low-water mark of available memory)
  await   `vfs.dev.read.await[<dev>]` / `vfs.dev.write.await[<dev>]`         -> milliseconds, hourly average (label host:dev:read|write)
  smart   `smart.disk.<metric>[<disk>]` (SMART by Zabbix agent 2)             -> the raw counter, hourly maximum (label host:disk)

Read-only: only `*.get` methods are ever called. `call(method, params)` is injected (production: tools._zbx with the Toolbelt's
read-only Zabbix credential; tests: a fake), so nothing here touches the network on its own.
"""
from __future__ import annotations

import re

HOUR = 3600.0
_BATCH = 20  # items per trend.get call


class ZabbixSource:
    def __init__(self, call):
        self.call = call

    # -- plumbing -----------------------------------------------------------------------------------------------------
    def monitored_hosts(self) -> dict:
        """{hostid: host name} for hosts only (a template's items have no history, and trend.get on them returns nothing)."""
        rows = self.call("host.get", {"output": ["hostid", "host"], "filter": {"status": 0}, "limit": 1000})
        return {r["hostid"]: r["host"] for r in rows}

    def _items(self, key_prefix: str, suffix: str | None = None) -> list[dict]:
        rows = self.call("item.get", {"output": ["itemid", "hostid", "key_", "value_type"], "search": {"key_": key_prefix}, "limit": 2000})
        return [r for r in rows if suffix is None or r["key_"].endswith(suffix)]

    def _trends(self, itemids: list[str], start: float, field: str) -> dict[str, list]:
        """{itemid: [(clock, float(field)), ...]} from hourly trends, ascending."""
        out: dict[str, list] = {i: [] for i in itemids}
        for n in range(0, len(itemids), _BATCH):
            chunk = itemids[n:n + _BATCH]
            rows = self.call("trend.get", {"output": ["itemid", "clock", field], "itemids": chunk, "time_from": int(start), "limit": len(chunk) * 800})
            for r in rows:
                try:
                    out[r["itemid"]].append((float(r["clock"]), float(r[field])))
                except (KeyError, TypeError, ValueError):
                    continue
        for pts in out.values():
            pts.sort()
        return out

    # -- the three families -------------------------------------------------------------------------------------------
    def fs_used(self, start: float, end: float) -> list:
        hosts = self.monitored_hosts()
        items = [i for i in self._items("vfs.fs.dependent.size[", ",pused]") if i["hostid"] in hosts]
        tr = self._trends([i["itemid"] for i in items], start, "value_max")
        out = []
        for i in items:
            mount = re.match(r"vfs\.fs\.dependent\.size\[(.*),pused\]$", i["key_"])
            pts = [(t, v / 100.0) for t, v in tr[i["itemid"]] if t <= end]
            if pts:
                out.append((f"{hosts[i['hostid']]}:{mount.group(1) if mount else i['key_']}", pts))
        return out

    def memory_used(self, start: float, end: float) -> list:
        hosts = self.monitored_hosts()
        items = [i for i in self._items("vm.memory.size[pavailable]") if i["hostid"] in hosts and i["key_"] == "vm.memory.size[pavailable]"]
        tr = self._trends([i["itemid"] for i in items], start, "value_min")
        out = []
        for i in items:
            pts = [(t, 1.0 - v / 100.0) for t, v in tr[i["itemid"]] if t <= end]
            if pts:
                out.append((hosts[i["hostid"]], pts))
        return out

    def pve_storage(self, storages: tuple, start: float, end: float) -> list:
        """One series per `<node>/<storage>`. The Proxmox template puts a copy of each node's items on every PVE host, so items are
        de-duplicated by key; the ratio is used / total at the same hour."""
        used, total = {}, {}
        for i in self._items("proxmox.node.disk["):
            used.setdefault(i["key_"], i)
        for i in self._items("proxmox.node.maxdisk["):
            total.setdefault(i["key_"], i)
        out = []
        for key, ui in sorted(used.items()):
            m = re.match(r"proxmox\.node\.disk\[([^,\]]+),([^\]]+)\]$", key)
            if not m or m.group(2) not in storages:
                continue
            ti = total.get(f"proxmox.node.maxdisk[{m.group(1)},{m.group(2)}]")
            if ti is None:
                continue
            tr = self._trends([ui["itemid"], ti["itemid"]], start, "value_avg")
            cap = dict(tr[ti["itemid"]])
            pts = [(t, v / cap[t]) for t, v in tr[ui["itemid"]] if t <= end and cap.get(t)]
            if pts:
                out.append((f"{m.group(1)}/{m.group(2)}", pts))
        return out

    def await_ms(self, start: float, end: float, dev_prefix: str = "") -> list:
        """Per host, device and direction; `dev_prefix` keeps only devices whose name starts with it ("nvme": the hypervisors' drives, not
        every guest's virtual disk, whose week-to-week swings are noise)."""
        hosts = self.monitored_hosts()
        pat = re.compile(r"vfs\.dev\.(read|write)\.await\[([^\],]+)\]$")
        items = [i for i in self._items("vfs.dev.") if i["hostid"] in hosts and pat.match(i["key_"]) and pat.match(i["key_"]).group(2).startswith(dev_prefix)]
        tr = self._trends([i["itemid"] for i in items], start, "value_avg")
        out = []
        for i in items:
            m = pat.match(i["key_"])
            pts = [(t, v) for t, v in tr[i["itemid"]] if t <= end]
            if pts:
                out.append((f"{hosts[i['hostid']]}:{m.group(2)}:{m.group(1)}", pts))
        return out

    def smart(self, metric: str, start: float, end: float) -> list:
        """One series per (host, disk) for `smart.disk.<metric>[<disk>]`, hourly maximum. Empty until the SMART template's discovery has run."""
        hosts = self.monitored_hosts()
        pat = re.compile(r"smart\.disk\." + re.escape(metric) + r"\[([^\]]+)\]$")
        items = [i for i in self._items(f"smart.disk.{metric}[") if i["hostid"] in hosts and pat.match(i["key_"])]
        tr = self._trends([i["itemid"] for i in items], start, "value_max")
        out = []
        for i in items:
            pts = [(t, v) for t, v in tr[i["itemid"]] if t <= end]
            if pts:
                out.append((f"{hosts[i['hostid']]}:{pat.match(i['key_']).group(1)}", pts))
        return out

    def query(self, target, start: float, end: float) -> list:
        """The series for one forecast Target (target.zabbix = ("fs",) | ("memory",) | ("pve", (storage, ...)) | ("await",) | ("smart", metric))."""
        kind = target.zabbix[0]
        if kind == "fs":
            return self.fs_used(start, end)
        if kind == "memory":
            return self.memory_used(start, end)
        if kind == "pve":
            return self.pve_storage(tuple(target.zabbix[1]), start, end)
        if kind == "await":
            return self.await_ms(start, end, str(target.zabbix[1]) if len(target.zabbix) > 1 else "")
        if kind == "smart":
            return self.smart(str(target.zabbix[1]), start, end)
        raise ValueError(f"unknown zabbix source kind {kind!r}")


def make_call(creds_dir: str, root: str):
    """The production `call`: the Toolbelt's own read-only Zabbix helper (`*.get` only, bearer token read from the credential file)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(root) / "aiops" / "toolbelt"))
    import tools
    cfg = tools.LiveConfig(root=Path(root), creds_dir=Path(creds_dir))

    def call(method: str, params: dict):
        return tools._zbx(cfg, method, params)
    return call
