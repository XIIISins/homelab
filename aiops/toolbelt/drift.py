"""Drift hand-off (Phase 10h2): a finished Semaphore drift-check that would change something, as one aiops.alert/v1.

The Ansible callback (ansible/callback_plugins/hermod_summary.py) tells Gná, over a webhook with no secret, that a drift-check
run would change hosts. Nothing in that message is believed: `verify` re-reads the run from Semaphore with the read-only token,
waits for it to finish and takes the per-host `changed=` counts from its own recap. `build_alert` then shapes the finding like
every other source (same routing table row, same fingerprint function), so dedupe, the diagnosis agent and the approval flow
treat it like any alert.

Pure functions over small callables, so tests need no Semaphore.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

for _p in (Path(__file__).resolve().parent, Path(__file__).resolve().parents[1] / "tools"):
    sys.path.insert(0, str(_p))
import normalize  # noqa: E402

TEMPLATES = ("asgard-drift-check",)  # prod only; the non-prod drift-check is expected to drift
_HOST = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
_RECAP = re.compile(r"^\s*(?P<host>[A-Za-z0-9][A-Za-z0-9._-]*)\s*:\s*ok=\d+\s+changed=(?P<changed>\d+)\s+unreachable=(?P<unreachable>\d+)\s+failed=(?P<failed>\d+)")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_DONE = ("success", "error", "stopped")


class DriftError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def validate(body: object) -> dict:
    """The callback's hand-off, shape-checked (it is a hint, not evidence)."""
    if not isinstance(body, dict):
        raise DriftError(400, "body must be an object")
    template = body.get("template", TEMPLATES[0])
    if template not in TEMPLATES:
        raise DriftError(400, f"template must be one of {list(TEMPLATES)}")
    task_id = body.get("task_id")
    if task_id is not None and (isinstance(task_id, bool) or not isinstance(task_id, int) or not 0 < task_id < 10**9):
        raise DriftError(400, "task_id must be a positive integer or null")
    changed = body.get("changed", {})
    if not isinstance(changed, dict) or len(changed) > 200 or not all(
            isinstance(h, str) and _HOST.match(h) and isinstance(n, int) and not isinstance(n, bool) and n >= 0 for h, n in changed.items()):
        raise DriftError(400, "changed must map host names to counts")
    return {"template": template, "task_id": task_id, "claimed": dict(changed)}


def recap(lines: list[str]) -> dict:
    """{host: {changed, failed, unreachable}} from the run's PLAY RECAP lines (summed if a host appears more than once)."""
    out: dict = {}
    for raw in lines:
        m = _RECAP.match(_ANSI.sub("", raw))
        if not m:
            continue
        h = out.setdefault(m.group("host"), {"changed": 0, "failed": 0, "unreachable": 0})
        for k in h:
            h[k] += int(m.group(k))
    return out


def verify(get, project: int, hint: dict, wait_seconds: int = 180, sleep=time.sleep, now=time.monotonic) -> dict:
    """Re-read the run. `get(path)` is the Semaphore GET helper. Returns {task_id, status, hosts: {...}, changed: {host: n}}."""
    base = f"/project/{project}"
    task_id = hint["task_id"]
    deadline = now() + wait_seconds
    while True:
        if task_id is None:  # the newest run of this template (the callback fires just before the run ends)
            runs = [t for t in get(f"{base}/tasks/last") if t.get("tpl_alias") == hint["template"]]
            if runs:
                task_id = runs[0]["id"]
        if task_id is not None:
            task = get(f"{base}/tasks/{task_id}")
            if task.get("tpl_alias") not in (None, hint["template"]):
                raise DriftError(400, "that task is not a run of the named template")
            if task.get("status") in _DONE:
                break
        if now() >= deadline:
            raise DriftError(504, "the run did not finish inside the wait")
        sleep(5)
    lines = [o.get("output", "") for o in get(f"{base}/tasks/{task_id}/output")]
    hosts = recap(lines)
    return {"task_id": task_id, "status": task.get("status"), "hosts": hosts,
            "changed": {h: v["changed"] for h, v in sorted(hosts.items()) if v["changed"] > 0}, "commit": (task.get("commit_hash") or "")[:8]}


def build_alert(routes: list[dict], found: dict, received_at: str, template: str) -> dict | None:
    """One aiops.alert/v1 for a verified run with drift, or None. Same routing row as the Hermod text path
    ("Drift detected: ...", semaphore-drift-detected), so the runbook and severity come from the one table. The host is the
    single drifted host, else `fleet`, so frigg's drift and hugin's drift are different problems with different fingerprints."""
    changed = found["changed"]
    if not changed:
        return None
    total = sum(changed.values())
    title = f"Drift detected: {total} task(s) on {len(changed)} host(s)"
    body = "\n".join([f"Run: {template} task {found['task_id']}", *(f"- {h}: changed={n}" for h, n in changed.items())])
    alerts = normalize.normalize({"title": title, "body": body, "type": "warning", "tag": "alert"}, received_at, routes)
    if not alerts:
        return None
    a = alerts[0]
    if len(changed) == 1:
        a["host"] = next(iter(changed))
        a["fingerprint"] = normalize.fingerprint(a["source"], a["host"], a["service"], a["check"])
    a["labels"].update({"template": template, "semaphore_task": str(found["task_id"]), "commit": found.get("commit", ""),
                        "changed_hosts": ",".join(f"{h}={n}" for h, n in changed.items())[:400]})
    return a
