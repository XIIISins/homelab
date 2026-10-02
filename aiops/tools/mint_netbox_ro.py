#!/usr/bin/env python3
"""Mint the Toolbelt's read-only NetBox identity (Phase 10d2) and prove it is read-only.

    NETBOX_SERVER_URL=... NETBOX_API_TOKEN=... VAULT_ADDR=... VAULT_TOKEN=... python3 aiops/tools/mint_netbox_ro.py

Why a script and not Terraform: the e-breuninger/netbox provider cannot create NetBox 4.4+ tokens or grant permissions
(docs/known-issues/netbox.md), and the token's secret is shown once, at creation, so it has to be written to Vault by the
run that creates it. Idempotent: the user and the permission are reconciled every run; the token is minted only while
`secret/ansible/aiops/netbox-token` is empty. To rotate: delete that secret and the token `aiops-toolbelt` in NetBox, re-run.

What it creates: user `aiops-ro` (random password nobody learns, no UI use), an object permission `aiops-readonly` with
ONLY the `view` action on the object types the diagnosis tools read, and a token with write_enabled=false. It then calls
the API with the NEW token: reads must succeed, and a create must be refused. Only outcomes are printed, never secrets.
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

USER = "aiops-ro"
PERMISSION = "aiops-readonly"
VAULT_PATH = "secret/ansible/aiops/netbox-token"
VIEW_TYPES = ["dcim.device", "dcim.site", "dcim.interface", "dcim.devicerole", "virtualization.virtualmachine",
              "virtualization.vminterface", "virtualization.cluster", "ipam.ipaddress", "ipam.prefix", "ipam.vlan", "extras.tag"]


def bearer_header(token: str) -> str:
    return ("Bearer " if token.startswith("nbt_") else "Token ") + token


class Api:
    def __init__(self, base: str, token: str):
        self.base, self.token = base.rstrip("/"), token

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}/api{path}", data=data, method=method,
                                     headers={"Authorization": bearer_header(self.token), "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
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


def vault_put(bearer: str, url: str) -> None:
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"value": bearer, "url": url}, fh)
        r = subprocess.run(["vault", "kv", "put", VAULT_PATH, f"@{path}"], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("vault write failed (is this Vault identity allowed to write secret/ansible/aiops/*?)")
    finally:
        os.remove(path)


def main() -> int:
    base, admin = os.environ["NETBOX_SERVER_URL"], os.environ["NETBOX_API_TOKEN"]
    api = Api(base, admin)

    st, res = api.call("GET", f"/users/users/?username={USER}")
    if st != 200:
        sys.exit(f"cannot talk to NetBox as admin (HTTP {st})")
    if res["results"]:
        uid = res["results"][0]["id"]
        print(f"user {USER}: exists (id {uid})")
    else:
        st, res = api.call("POST", "/users/users/", {"username": USER, "password": secrets.token_urlsafe(48), "is_active": True})
        if st != 201:
            sys.exit(f"user create failed (HTTP {st}): {json.dumps(res)[:200]}")
        uid = res["id"]
        print(f"user {USER}: created (id {uid})")

    perm = {"name": PERMISSION, "description": "AIOps Toolbelt: read-only. Managed by aiops/tools/mint_netbox_ro.py.",
            "enabled": True, "object_types": VIEW_TYPES, "actions": ["view"], "users": [uid]}
    st, res = api.call("GET", f"/users/permissions/?name={PERMISSION}")
    if res and res["results"]:
        st, res = api.call("PATCH", f"/users/permissions/{res['results'][0]['id']}/", perm)
        print(f"permission {PERMISSION}: reconciled (HTTP {st})")
    else:
        st, res = api.call("POST", "/users/permissions/", perm)
        print(f"permission {PERMISSION}: created (HTTP {st})")
    if st not in (200, 201):
        sys.exit(f"permission write failed: {json.dumps(res)[:300]}")

    if vault_has_value():
        print(f"token: already stored at {VAULT_PATH}; not minting another")
        bearer = subprocess.run(["vault", "kv", "get", "-field=value", VAULT_PATH], capture_output=True, text=True).stdout.strip()
    else:
        st, res = api.call("POST", "/users/tokens/", {"user": uid, "write_enabled": False, "description": "aiops-toolbelt (read-only)"})
        if st != 201 or not res or "token" not in res:
            sys.exit(f"token create failed (HTTP {st}): {json.dumps({k: v for k, v in (res or {}).items() if k != 'token'})[:200]}")
        bearer = f"nbt_{res['key']}.{res['token']}" if res.get("key") else res["token"]
        vault_put(bearer, base)
        print(f"token: minted (write_enabled={res.get('write_enabled')}) and stored at {VAULT_PATH}")

    ro = Api(base, bearer)
    ok = True
    for label, method, path, body, want in (
        ("read VMs", "GET", "/virtualization/virtual-machines/?limit=1", None, 200),
        ("read devices", "GET", "/dcim/devices/?limit=1", None, 200),
        ("read IPs", "GET", "/ipam/ip-addresses/?limit=1", None, 200),
        ("create a VM (must be refused)", "POST", "/virtualization/virtual-machines/", {}, 403),
        ("create a tag (must be refused)", "POST", "/extras/tags/", {}, 403),
        ("read users (must be refused)", "GET", "/users/users/?limit=1", None, 403),
    ):
        st, _ = ro.call(method, path, body)
        good = st == want
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} {label}: HTTP {st} (want {want})")
    # NetBox lets every user list THEIR OWN tokens (built in, not a permission we can remove). Prove it sees only its own.
    st, res = ro.call("GET", "/users/tokens/")
    own = st == 200 and res and res.get("count") == 1 and res["results"][0]["user"]["username"] == USER
    ok &= bool(own)
    print(f"  {'OK ' if own else 'BAD'} tokens visible: only its own (HTTP {st}, count {res.get('count') if res else None})")
    print("read-only proof:", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
