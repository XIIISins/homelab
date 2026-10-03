"""One shadow forecasting pass against VictoriaMetrics (Phase 10h1): `python3 forecast_run.py --log PATH`.

Run by a systemd timer on Frigg (roles/aiops-toolbelt, `aiops-toolbelt-forecast`), in its own unit like the placement
sync: the Toolbelt API unit has no route to the metrics endpoint by design. It reads only (a GET to the same
`metrics-read` route the agent's read tool uses, at full resolution rather than the thinned series the model sees) and
appends findings to a local JSONL file. There is no Discord or ticket output: this is the 14-day shadow period of
docs/operations/10h-predictive-change.md. Detectors and targets live in forecast.py.
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


def main(argv: list | None = None, query=None, now: float | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", required=True, help="JSONL file the findings are appended to (shadow mode: no other output)")
    ap.add_argument("--url", default=DEFAULT_URL, help="VictoriaMetrics read endpoint")
    ap.add_argument("--state", help="JSON file remembering when each finding was last reported (dedup across timer runs)")
    a = ap.parse_args(argv)
    dedup = forecast.Dedup()
    state = Path(a.state) if a.state else None
    t = time.time() if now is None else now
    if state and state.exists():
        try:
            dedup._seen = {k: (float(v[0]), v[1]) for k, v in json.loads(state.read_text()).items()}
        except (OSError, ValueError, TypeError, IndexError):
            pass  # a bad state file only means a repeat report
    found = forecast.run_once(query or make_query(a.url), t, a.log, dedup=dedup)
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
