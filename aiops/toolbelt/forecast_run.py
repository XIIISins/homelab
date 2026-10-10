"""One shadow forecasting pass against VictoriaMetrics (Phase 10h1): `python3 forecast_run.py --log PATH`.

Run by a systemd timer on Frigg (roles/aiops-toolbelt, `aiops-toolbelt-forecast`), in its own unit like the placement
sync: the Toolbelt API unit has no route to the metrics endpoint by design. It reads only (a GET to the same
`metrics-read` route the agent's read tool uses, at full resolution rather than the thinned series the model sees) and
appends findings to a local JSONL file. There is no Discord or ticket output: this is the 14-day shadow period of
docs/plans/active/10h-predictive-change.md. Detectors and targets live in forecast.py.

Phase 10i2: with `--rightsizing CONFIG` the same pass also runs the pod-rightsizing findings (rightsizing.py) at most once per
`--rightsizing-every` seconds (default a day). They ride in the same current-findings file as `kind = "rightsizing"` rows, which the
forecast store keeps QUIET (no cards, no budget): the periodic digest (10i3) is their only reader. Between daily passes the previous
findings are carried over, and a pass with query errors never replaces them, so a metrics outage cannot "resolve" a finding.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forecast  # noqa: E402
import rightsizing  # noqa: E402

DEFAULT_URL = "https://metrics-read.niflheim.xiiisins.com"
LABEL_KEYS = ("persistentvolumeclaim", "namespace", "instance", "job")


def label_of(metric: dict) -> str:
    """A stable, readable target label for one series: the identifying labels in a fixed order (namespace/pvc first)."""
    ns, pvc = metric.get("namespace"), metric.get("persistentvolumeclaim")
    if ns and pvc:
        return f"{ns}/{pvc}"
    parts = [str(metric[k]) for k in LABEL_KEYS if metric.get(k)]
    return "/".join(parts) or str(metric.get("__name__", "series"))


def parse_matrix(payload: dict) -> list:
    """VictoriaMetrics query_range JSON -> [(label, [(ts, float), ...]), ...]. Non-numeric samples are dropped."""
    if payload.get("status") != "success":
        raise ValueError("query did not succeed")
    out = []
    for s in payload.get("data", {}).get("result", []) or []:
        pts = []
        for ts, v in s.get("values", []) or []:
            try:
                pts.append((float(ts), float(v)))
            except (TypeError, ValueError):
                continue
        out.append((label_of(s.get("metric", {})), pts))
    return out


def make_query(base_url: str = DEFAULT_URL, timeout: float = 30.0):
    """The `query(promql, start, end, step)` function run_once() wants."""
    def query(promql: str, start: float, end: float, step: str) -> list:
        q = urllib.parse.urlencode({"query": promql, "start": int(start), "end": int(end), "step": step})
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/v1/query_range?{q}", headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return parse_matrix(json.loads(r.read().decode()))
    return query


def fetch_text(timeout: float = 60.0):
    """`fetch(url) -> text` for rightsizing.VM (a GET to the metrics-read route; 30-day subqueries take a few seconds)."""
    def fetch(url: str) -> str:
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode()
    return fetch


def rightsizing_pass(config: str, url: str, now: float, every: float, prev: dict | None, fetch=None) -> dict:
    """The (at most daily) rightsizing pass: returns {"ts", "findings", "stats"} to store under `rightsizing` in the current file.
    Not due -> the previous block as it was. Failed or partial (query errors) -> the previous findings, the new stats and the OLD ts, so the
    next hourly run retries; a first pass that fails stores no findings at all."""
    prev = prev or {}
    if prev.get("ts") and prev.get("snapshot") and now - float(prev["ts"]) < every:   # a block from before the snapshot existed (10i2) is always due
        return prev
    try:
        cfg = rightsizing.load_config(config)
        found, stats, snap = rightsizing.findings(rightsizing.VM(url, fetch or fetch_text()), cfg, now, with_snapshot=True)
    except Exception as e:  # noqa: BLE001 - the forecasts must still be written
        return {**prev, "stats": {"error": f"{type(e).__name__}: {str(e)[:120]}"}}
    if stats.get("errors"):
        return {**prev, "stats": stats}
    return {"ts": now, "findings": found, "stats": stats, "snapshot": snap}


def main(argv: list | None = None, query=None, now: float | None = None, fetch=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", required=True, help="JSONL file the findings are appended to (shadow mode: no other output)")
    ap.add_argument("--url", default=DEFAULT_URL, help="VictoriaMetrics read endpoint")
    ap.add_argument("--state", help="JSON file remembering when each finding was last reported (dedup across timer runs)")
    ap.add_argument("--current", help="JSON file replaced each pass with every finding that holds now (the Toolbelt reads it)")
    ap.add_argument("--rightsizing", help="aiops/rightsizing.yml: also run the daily pod-rightsizing findings (Phase 10i2; needs --current)")
    ap.add_argument("--rightsizing-every", type=float, default=86400.0, help="seconds between rightsizing passes")
    ap.add_argument("--zabbix-creds", help="directory holding the read-only zabbix.json credential (enables the Zabbix targets)")
    ap.add_argument("--root", default="/opt/aiops-toolbelt", help="where aiops/toolbelt lives (for the Zabbix helper)")
    a = ap.parse_args(argv)
    dedup = forecast.Dedup()
    state = Path(a.state) if a.state else None
    t = time.time() if now is None else now
    if state and state.exists():
        try:
            dedup._seen = {k: (float(v[0]), v[1]) for k, v in json.loads(state.read_text()).items()}
        except (OSError, ValueError, TypeError, IndexError):
            pass  # a bad state file only means a repeat report
    sources = {}
    if a.zabbix_creds:
        import forecast_zabbix
        sources["zabbix"] = forecast_zabbix.ZabbixSource(forecast_zabbix.make_call(a.zabbix_creds, a.root)).query
    stats: dict = {}
    prev_rs = None
    if a.rightsizing and a.current and Path(a.current).exists():
        try:
            prev_rs = json.loads(Path(a.current).read_text()).get("rightsizing")
        except (OSError, ValueError, AttributeError):
            prev_rs = None
    found = forecast.run_once(query or make_query(a.url), t, a.log, dedup=dedup, sources=sources, stats=stats, current_path=a.current)
    for name, s in sorted(stats.items()):
        print(f"forecast: {name}: " + ", ".join(f"{k}={v}" for k, v in s.items()))
    if a.rightsizing and a.current:
        rs = rightsizing_pass(a.rightsizing, a.url, t, a.rightsizing_every, prev_rs, fetch)
        cur = Path(a.current)
        doc = json.loads(cur.read_text())
        doc["rightsizing"] = rs
        doc["findings"] = doc.get("findings", []) + rs.get("findings", [])
        tmp = cur.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, sort_keys=True))
        tmp.replace(cur)
        print("rightsizing: " + ", ".join(f"{k}={v}" for k, v in sorted((rs.get("stats") or {}).items())))
    if state:
        state.parent.mkdir(parents=True, exist_ok=True)
        keep = {k: v for k, v in dedup._seen.items() if t - v[0] < 7 * 86400}
        tmp = state.with_suffix(".tmp")
        tmp.write_text(json.dumps(keep))
        tmp.replace(state)
    print(f"forecast: {len(found)} new finding(s) appended to {a.log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
