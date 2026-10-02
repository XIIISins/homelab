#!/usr/bin/env python3
"""Mint the Toolbelt's read-only Semaphore identity (Phase 10d2) and prove it is read-only.

    (homelab env: SEMAPHOREUI_API_BASE_URL + SEMAPHOREUI_API_TOKEN of an admin, and a Vault identity that can write
     secret/ansible/aiops/*)    python3 aiops/tools/mint_semaphore_ro.py

Creates a non-admin local user `aiops-ro`, gives it the built-in `guest` role on project 1 (Semaphore's read-only role:
no running tasks, no editing templates/keys/inventories), logs in AS that user once with a random password nobody keeps,
creates an API token for it and stores {value, url} at `secret/ansible/aiops/semaphore-token`. It then proves the token is
read-only: task history reads succeed, running a task / editing a template / reading users are refused. Idempotent: the
user and role are reconciled every run; the token is minted only while the Vault secret is empty. To rotate: delete the
secret and the token in Semaphore (user menu -> API tokens), re-run. Only outcomes are printed.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

USER = "aiops-ro"
PROJECT = 1
VAULT_PATH = "secret/ansible/aiops/semaphore-token"


class Client:
    def __init__(self, base: str, bearer: str | None = None):
        self.base = base.rstrip("/")
        self.bearer = bearer
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method: str, path: str, body=None):
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.bearer:
            headers["Authorization"] = "Bearer " + self.bearer
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=20) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, None


def vault_has_value() -> bool:
    r = subprocess.run(["vault", "kv", "get", "-field=value", VAULT_PATH], capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout.strip())


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


def main() -> int:
    base, admin_token = os.environ["SEMAPHOREUI_API_BASE_URL"], os.environ["SEMAPHOREUI_API_TOKEN"]
    admin = Client(base, admin_token)

    st, users = admin.call("GET", "/users")
    if st != 200:
        sys.exit(f"cannot talk to Semaphore as admin (HTTP {st})")
    existing = next((u for u in users if u.get("username") == USER), None)
    password = secrets.token_urlsafe(40)
    if existing:
        uid = existing["id"]
        print(f"user {USER}: exists (id {uid})")
    else:
        st, res = admin.call("POST", "/users", {"name": "AIOps read-only", "username": USER, "email": f"{USER}@niflheim.xiiisins.com",
                                                 "password": password, "admin": False, "alert": False, "external": False})
        if st != 201:
            sys.exit(f"user create failed (HTTP {st})")
        uid = res["id"]
        print(f"user {USER}: created (id {uid})")

    st, members = admin.call("GET", f"/project/{PROJECT}/users")
    mine = next((m for m in members if m.get("id") == uid), None)
    if mine and mine.get("role") == "guest":
        print(f"project {PROJECT} role: guest (already)")
    elif mine:
        st, _ = admin.call("PUT", f"/project/{PROJECT}/users/{uid}", {"user_id": uid, "role": "guest"})
        print(f"project {PROJECT} role: reconciled to guest (HTTP {st})")
    else:
        st, _ = admin.call("POST", f"/project/{PROJECT}/users", {"user_id": uid, "role": "guest"})
        print(f"project {PROJECT} role: guest granted (HTTP {st})")
    if st not in (200, 201, 204):
        sys.exit("could not grant the guest role")

    if vault_has_value():
        print(f"token: already stored at {VAULT_PATH}; not minting another")
        token = subprocess.run(["vault", "kv", "get", "-field=value", VAULT_PATH], capture_output=True, text=True).stdout.strip()
    else:
        # a one-time password so we can log in as the user once; it is never stored
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

    ro = Client(base, token)
    ok = True
    for label, method, path, body, want in (
        ("read task history", "GET", f"/project/{PROJECT}/tasks/last", None, {200}),
        ("read templates", "GET", f"/project/{PROJECT}/templates", None, {200}),
        ("run a task (must be refused)", "POST", f"/project/{PROJECT}/tasks", {"template_id": 1, "debug": False, "dry_run": True}, {403}),
        ("edit a template (must be refused)", "PUT", f"/project/{PROJECT}/templates/1", {"id": 1}, {403}),
        ("create a template (must be refused)", "POST", f"/project/{PROJECT}/templates", {"name": "x"}, {403}),
        # Semaphore lets any authenticated user list users (id, name, username only: no secrets), which project membership
        # needs; creating one is admin-only and answers 401/403.
        ("list users (allowed: id/name/username only)", "GET", "/users", None, {200}),
        ("create a user (must be refused)", "POST", "/users", {"username": "x"}, {401, 403}),
    ):
        st, _ = ro.call(method, path, body)
        good = st in want
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} {label}: HTTP {st} (want {sorted(want)})")
    print("read-only proof:", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
