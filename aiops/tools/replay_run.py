#!/usr/bin/env python3
"""Acceptance harness for the diagnosis agent (Phase 10d3): run a recorded scenario through the REAL stack and judge it.

    python3 aiops/tools/replay_run.py canary-agent-down            # needs the homelab Vault env + ssh to Gna

What it does: posts the scenario's event to the agent's ingest webhook on Gna with `X-AIOPS-Replay: <scenario>`,
which makes the Toolbelt answer every tool call from the scenario's recordings (no live infrastructure is touched
and nothing can be broken), waits for the incident to be posted, reads the run back from the Toolbelt
(`GET /replay/<scenario>/latest`) and checks it against the scenario's `expect`.

`evaluate()` is pure and unit-tested with negative controls (a run that blames a forbidden layer, cites nothing,
was never posted, or leaned on unrecorded calls must FAIL); the transport at the bottom is the thin live part.
Secrets (the ingest and Toolbelt tokens) are read from Vault inside this process and handed to ssh on stdin, never
placed on a command line or printed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPLAYS = REPO / "aiops" / "replays"
MAX_NO_RECORDING = 3  # an agent that thrashes against unrecorded calls is not behaving, even if it lands


def evaluate(scenario: dict, run: dict) -> list[str]:
    """Return the reasons a run FAILS its scenario (empty list = pass)."""
    exp = scenario.get("expect", {})
    fails: list[str] = []
    if run.get("state") not in ("posted", "resolved"):
        fails.append(f"the incident never reached posted (state {run.get('state')!r})")
    d = run.get("diagnosis")
    if not d:
        fails.append("no diagnosis was accepted for the incident (the agent failed or its answer did not validate)")
        return fails  # nothing further can be judged
    if d["layer"] not in exp.get("layers_allowed", []):
        fails.append(f"layer {d['layer']!r} is not one of {exp.get('layers_allowed')}")
    if d["layer"] in exp.get("layers_forbidden", []):
        fails.append(f"layer {d['layer']!r} is explicitly forbidden for this scenario")
    if len(d["evidence"]) < exp.get("min_evidence", 1):
        fails.append(f"only {len(d['evidence'])} piece(s) of evidence, need {exp.get('min_evidence', 1)}")
    need = exp.get("must_cite_any_of", [])
    if need and not {e["tool"] for e in d["evidence"]} & set(need):
        fails.append(f"evidence cites none of {need}")
    if run.get("no_recording", 0) > MAX_NO_RECORDING:
        fails.append(f"{run['no_recording']} unrecorded tool calls (limit {MAX_NO_RECORDING}): the agent is guessing at checks")
    if d["confidence"] == "high" and d["layer"] == "unknown":
        fails.append("confidence high with layer unknown is incoherent")
    return fails


def load_scenario(name: str) -> dict:
    return json.loads((REPLAYS / name / "scenario.json").read_text())


# ---- live transport (ssh to Gna; the tokens ride stdin) -----------------------------------------------
def _vault(path: str) -> str:
    r = subprocess.run(["vault", "kv", "get", "-field=value", path], capture_output=True, text=True, check=False)
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit(f"cannot read {path} from Vault (is the homelab env loaded?)")
    return r.stdout.strip()


def _ssh(cmd: str, stdin: str) -> str:
    key = os.environ.get("ANSIBLE_PRIVATE_KEY_FILE", "")
    base = ["ssh", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=accept-new"] + (["-i", key] if key else [])
    r = subprocess.run(base + ["ansible@10.0.11.221", cmd], input=stdin, capture_output=True, text=True, timeout=90, check=False)
    return r.stdout


def post_event(scenario: str, event: dict) -> str:
    tok = _vault("secret/ansible/aiops/n8n-ingest-token/zabbix")
    cmd = (f"read -r t; curl -sS -m 15 -o /dev/null -w '%{{http_code}}' -H \"X-AIOPS-Token: $t\" -H 'Content-Type: application/json' "
           f"-H 'X-AIOPS-Replay: {scenario}' --data-binary @- http://127.0.0.1:5678/webhook/aiops/zabbix")
    return _ssh(cmd, tok + "\n" + json.dumps(event)).strip()


def get_latest(scenario: str) -> dict | None:
    tok = _vault("secret/ansible/aiops/toolbelt-token")
    cmd = ("read -r t; curl -sS -m 15 -w '\\n%{http_code}' -H \"Authorization: Bearer $t\" "
           f"http://10.0.11.30:8090/replay/{scenario}/latest")
    out = _ssh(cmd, tok + "\n")
    body, _, code = out.rpartition("\n")
    return json.loads(body) if code.strip() == "200" else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenario")
    ap.add_argument("--timeout", type=int, default=600, help="seconds to wait for the run to finish")
    args = ap.parse_args(argv)
    sc = load_scenario(args.scenario)
    ev = dict(sc["event"], fired_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              sent_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    before = (get_latest(args.scenario) or {}).get("incident_id")
    print(f"POST event -> {post_event(args.scenario, ev)}", flush=True)
    deadline = time.time() + args.timeout
    run = None
    while time.time() < deadline:
        time.sleep(15)
        run = get_latest(args.scenario)
        if run and run["incident_id"] != before and (run["state"] in ("posted", "resolved") or run["diagnosis"]):
            time.sleep(10)  # let "mark posted" land
            run = get_latest(args.scenario)
            break
    if not run or run["incident_id"] == before:
        print("FAIL: no new incident appeared for the scenario within the timeout")
        return 1
    print(f"incident #{run['incident_id']} state={run['state']} model={run['model']} calls={len(run['calls'])} no_recording={run['no_recording']}")
    for c in run["calls"]:
        print(f"  {c['outcome']:12} {c['tool']} {json.dumps(c['args'], sort_keys=True)}")
    d = run["diagnosis"]
    if d:
        print(f"diagnosis: layer={d['layer']} confidence={d['confidence']} needs_human={d['needs_human']}\n  {d['summary']}")
    fails = evaluate(sc, run)
    for f in fails:
        print("FAIL:", f)
    print("PASS" if not fails else "FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
