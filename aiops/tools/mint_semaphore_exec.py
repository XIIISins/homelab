#!/usr/bin/env python3
"""Mint the Toolbelt executor's Semaphore identity (Phase 10e1) and prove how far its token reaches.

    (homelab env: SEMAPHOREUI_API_BASE_URL + SEMAPHOREUI_API_TOKEN of an admin, and a Vault identity that can write
     secret/ansible/aiops/*; run AFTER `terraform apply` in terraform/semaphore created the `aiops` project)
    python3 aiops/tools/mint_semaphore_exec.py [--prove-run]

Creates a non-admin local user `aiops-exec`, makes it **Task Runner on the `aiops` project only** (and removes it from
any other project), logs in as it once with a random password nobody keeps, creates an API token and stores
{value, url} at `secret/ansible/aiops/semaphore-exec-token`. Why a dedicated project: Semaphore roles are per project, so
a Task Runner could run EVERY template of its project, including asgard-apply; the aiops-* templates therefore live in their
own project (terraform/semaphore/main.tf) and this token cannot see the rest.

Then it proves the reach: it CAN list the aiops templates and the tasks of that project; it CANNOT read or run anything in
another project, edit or create a template, create a user, or give itself a role elsewhere. `--prove-run` additionally
runs the read-only `aiops-service-status` template once (canary-1, vlagent.service) and checks the AIOPS_RESULT line.
Idempotent: the user and role are reconciled every run; the token is minted only while the Vault secret is empty (to
rotate: delete the secret and the token in Semaphore, re-run). Only outcomes are printed.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mint_semaphore_ro import Client  # noqa: E402  (shared HTTP client)

USER = "aiops-exec"
PROJECT_NAME = "aiops"
VAULT_PATH = "secret/ansible/aiops/semaphore-exec-token"
ROLE = "task_runner"
REGISTRY = Path(__file__).resolve().parents[1] / "actions.yml"


def registry_templates() -> list[str]:
    import yaml

    acts = yaml.safe_load(REGISTRY.read_text())["actions"]
    return sorted({a["semaphore"]["template"] for a in acts.values() if a["semaphore"].get("applied") and not a["semaphore"].get("runner_only")})


def prove(call, aiops_id: int, other_ids: list[int], uid: int, expected_templates: list[str]) -> list[tuple[str, bool, str]]:
    """`call(method, path, body)` -> (http status, parsed body) as the EXECUTOR. Returns (label, ok, detail) rows."""
    rows: list[tuple[str, bool, str]] = []

    def check(label, status, want, detail=""):
        rows.append((label, status in want, f"HTTP {status} (want {sorted(want)}){detail}"))

    st, projects = call("GET", "/projects", None)
    names = sorted(p.get("name") for p in (projects or [])) if st == 200 and isinstance(projects, list) else None
    rows.append(("sees exactly one project, `aiops`", names == [PROJECT_NAME], f"HTTP {st}, projects {names}"))
    st, tpls = call("GET", f"/project/{aiops_id}/templates", None)
    have = sorted(t.get("name") for t in (tpls or [])) if st == 200 and isinstance(tpls, list) else []
    missing = [n for n in expected_templates if n not in have]
    rows.append(("lists the aiops templates, all registry templates present", st == 200 and not missing, f"HTTP {st}, missing {missing}"))
    st, _ = call("GET", f"/project/{aiops_id}/tasks/last", None)
    check("reads the aiops task history", st, {200})
    first = next((t["id"] for t in (tpls or []) if isinstance(t, dict) and "id" in t), 1)
    st, _ = call("PUT", f"/project/{aiops_id}/templates/{first}", {"id": first})
    check("edits a template (must be refused)", st, {403})
    st, _ = call("POST", f"/project/{aiops_id}/templates", {"name": "x"})
    check("creates a template (must be refused)", st, {403})
    st, _ = call("POST", "/users", {"username": "x"})
    check("creates a user (must be refused)", st, {401, 403})
    st, _ = call("POST", f"/project/{aiops_id}/users", {"user_id": uid, "role": "owner"})
    check("raises its own role in aiops (must be refused)", st, {403})
    for pid in other_ids:
        st, _ = call("GET", f"/project/{pid}/templates", None)
        check(f"reads project {pid} templates (must be refused)", st, {403, 404})
        st, _ = call("POST", f"/project/{pid}/tasks", {"template_id": 1, "debug": False, "dry_run": True})
        check(f"runs a task in project {pid} (must be refused)", st, {403, 404})
        st, _ = call("POST", f"/project/{pid}/users", {"user_id": uid, "role": "owner"})
        check(f"gives itself a role in project {pid} (must be refused)", st, {403, 404})
    return rows


def vault_get() -> str:
    r = subprocess.run(["vault", "kv", "get", "-field=value", VAULT_PATH], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def vault_put(token: str, url: str) -> None:
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"value": token, "url": url}, fh)
        if subprocess.run(["vault", "kv", "put", VAULT_PATH, f"@{path}"], capture_output=True, text=True).returncode != 0:
            sys.exit("vault write failed (can this Vault identity write secret/ansible/aiops/*?)")
    finally:
        os.remove(path)


def prove_run(call, aiops_id: int) -> tuple[bool, str]:
    """Run the read-only aiops-service-status template once through the executor's own token."""
    st, tpls = call("GET", f"/project/{aiops_id}/templates", None)
    tid = next((t["id"] for t in (tpls or []) if t.get("name") == "aiops-service-status"), None)
    if tid is None:
        return False, "template aiops-service-status not found"
    st, res = call("POST", f"/project/{aiops_id}/tasks", {"template_id": tid, "debug": False, "dry_run": False,
                                                         "environment": json.dumps({"target_host": "canary-1", "unit": "vlagent.service"})})
    if st not in (200, 201) or not res:
        return False, f"could not start the task (HTTP {st})"
    task = res["id"]
    for _ in range(90):
        st, t = call("GET", f"/project/{aiops_id}/tasks/{task}", None)
        if t and t.get("status") in ("success", "error", "stopped"):
            break
        time.sleep(2)
    else:
        return False, f"task {task} did not finish in 3 minutes"
    st, out = call("GET", f"/project/{aiops_id}/tasks/{task}/output", None)
    text = "\n".join(o.get("output", "") for o in (out or []))
    ok = t.get("status") == "success" and "AIOPS_RESULT" in text
    return ok, f"task {task} ended {t.get('status')}; AIOPS_RESULT line {'present' if 'AIOPS_RESULT' in text else 'ABSENT'}"


def main() -> int:
    base, admin_token = os.environ["SEMAPHOREUI_API_BASE_URL"], os.environ["SEMAPHOREUI_API_TOKEN"]
    admin = Client(base, admin_token)
    st, projects = admin.call("GET", "/projects")
    if st != 200:
        sys.exit(f"cannot talk to Semaphore as admin (HTTP {st})")
    aiops = next((p for p in projects if p.get("name") == PROJECT_NAME), None)
    if aiops is None:
        sys.exit(f"no Semaphore project named {PROJECT_NAME!r}: run `terraform apply` in terraform/semaphore first (from the main checkout)")
    pid = aiops["id"]
    others = [p["id"] for p in projects if p["id"] != pid]
    print(f"project {PROJECT_NAME}: id {pid}")

    st, users = admin.call("GET", "/users")
    existing = next((u for u in users if u.get("username") == USER), None)
    password = secrets.token_urlsafe(40)
    if existing:
        uid = existing["id"]
        print(f"user {USER}: exists (id {uid})")
    else:
        st, res = admin.call("POST", "/users", {"name": "AIOps executor", "username": USER, "email": f"{USER}@niflheim.xiiisins.com",
                                                 "password": password, "admin": False, "alert": False, "external": False})
        if st != 201:
            sys.exit(f"user create failed (HTTP {st})")
        uid = res["id"]
        print(f"user {USER}: created (id {uid})")
    if existing and existing.get("admin"):
        sys.exit(f"user {USER} is a Semaphore ADMIN: refusing to continue (it must be a plain user)")

    st, members = admin.call("GET", f"/project/{pid}/users")
    mine = next((m for m in members if m.get("id") == uid), None)
    if mine and mine.get("role") == ROLE:
        print(f"project {PROJECT_NAME} role: {ROLE} (already)")
    elif mine:
        st, _ = admin.call("PUT", f"/project/{pid}/users/{uid}", {"user_id": uid, "role": ROLE})
        print(f"project {PROJECT_NAME} role: reconciled to {ROLE} (HTTP {st})")
    else:
        st, _ = admin.call("POST", f"/project/{pid}/users", {"user_id": uid, "role": ROLE})
        print(f"project {PROJECT_NAME} role: {ROLE} granted (HTTP {st})")
    if st not in (200, 201, 204):
        sys.exit(f"could not grant the {ROLE} role")
    for other in others:  # the whole point: nothing outside the aiops project
        st, mem = admin.call("GET", f"/project/{other}/users")
        if any(m.get("id") == uid for m in (mem or [])):
            st, _ = admin.call("DELETE", f"/project/{other}/users/{uid}")
            print(f"removed {USER} from project {other} (HTTP {st})")

    token = vault_get()
    if token:
        print(f"token: already stored at {VAULT_PATH}; not minting another")
    else:
        st, _ = admin.call("POST", f"/users/{uid}/password", {"password": password})
        if st not in (200, 204):
            sys.exit(f"could not set the one-time password (HTTP {st})")
        user = Client(base)
        st, _ = user.call("POST", "/auth/login", {"auth": USER, "password": password})
        if st not in (200, 204):
            sys.exit(f"login as {USER} failed (HTTP {st})")
        st, res = user.call("POST", "/user/tokens")
        if st != 201 or not res or "id" not in res:
            sys.exit(f"token create failed (HTTP {st})")
        token = res["id"]
        vault_put(token, base)
        print(f"token: minted and stored at {VAULT_PATH}")

    ex = Client(base, token)
    rows = prove(ex.call, pid, others, uid, registry_templates())
    for label, ok, detail in rows:
        print(f"  {'OK ' if ok else 'BAD'} {label}: {detail}")
    good = all(ok for _, ok, _ in rows)
    if "--prove-run" in sys.argv:
        ok, detail = prove_run(ex.call, pid)
        print(f"  {'OK ' if ok else 'BAD'} runs aiops-service-status end to end: {detail}")
        good &= ok
    print("executor reach proof:", "PASSED" if good else "FAILED")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
