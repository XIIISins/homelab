#!/usr/bin/env python3
"""Prove the Toolbelt's read-only credentials are read-only (Phase 10d2). Re-run after ANY credential or role change.

    (homelab env with Vault access)  python3 aiops/tools/verify_readonly.py [zabbix] [pve] [all]

Reads each token from Vault inside this process (never printed, never on a command line) and attempts the writes it must
not be able to do. Only outcomes are printed. Exit status 1 if any check is wrong, so it can gate a change.

  zabbix  host.get / problem.get / trigger.get succeed; host.update, host.delete, event.acknowledge, user.create,
          script.execute, token.generate, configuration.import are all `No permissions to call`.
  pve     reads succeed; stop / start / delete / snapshot / config against a NON-EXISTENT vmid are 403. PVE checks permission
          before existence, so a write attempt cannot touch a real guest even if the token were over-privileged. A node-level
          call such as POST /nodes/<n>/status (shutdown) must never be used as a probe.

The other three identities carry their own proof, run by re-running their idempotent minting script (it only reconciles
and verifies once the Vault secret exists): aiops/tools/mint_netbox_ro.py, mint_semaphore_ro.py, mint_kube_ro.py.
"""
from __future__ import annotations

import json
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

ZABBIX_URL = "http://10.0.11.21/api_jsonrpc.php"
PVE_URL = "https://10.0.254.11:8006/api2/json"


def vault(path: str, field: str | None = None):
    args = ["vault", "kv", "get"] + ([f"-field={field}"] if field else ["-format=json"]) + [path]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"cannot read {path} from Vault (is the homelab env loaded?)")
    return r.stdout.strip() if field else json.loads(r.stdout)["data"]["data"]


def zabbix() -> bool:
    tok = vault("secret/ansible/aiops/zabbix-token", "value")

    def call(method, params):
        req = urllib.request.Request(ZABBIX_URL, json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode(),
                                     {"Content-Type": "application/json-rpc", "Authorization": "Bearer " + tok})
        d = json.load(urllib.request.urlopen(req, timeout=15))
        return ("error", d["error"].get("data", "")) if "error" in d else ("ok", d["result"])

    ok = True
    for m, p in (("host.get", {"output": ["host"], "limit": 1}), ("problem.get", {"output": ["name"], "limit": 1}), ("trigger.get", {"output": ["description"], "limit": 1})):
        status, _ = call(m, p)
        good = status == "ok"
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} zabbix {m} allowed" if good else f"  BAD zabbix {m} should be allowed")
    for m, p in (("host.update", {"hostid": "1", "status": 1}), ("host.delete", ["1"]), ("event.acknowledge", {"eventids": "1", "action": 1}),
                 ("user.create", {"username": "x", "usrgrps": [{"usrgrpid": "7"}], "passwd": "x" * 20}), ("script.execute", {"scriptid": "1", "hostid": "1"}),
                 ("token.generate", ["1"]), ("configuration.import", {"format": "json", "source": "{}", "rules": {}})):
        status, detail = call(m, p)
        good = status == "error" and "No permissions" in str(detail)
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} zabbix {m} refused" + ("" if good else f" (got {status}: {str(detail)[:80]})"))
    return ok


def pve() -> bool:
    d = vault("secret/ansible/aiops/pve-token")
    hdr = {"Authorization": f"PVEAPIToken={d['token_id']}={d['secret']}"}
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE

    def call(method, path):
        req = urllib.request.Request(PVE_URL + path, method=method, headers=hdr, data=(b"" if method in ("POST", "PUT") else None))
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    ok = True
    for path in ("/nodes", "/cluster/resources?type=vm"):
        good = call("GET", path) == 200
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} pve GET {path} allowed")
    for method, path in (("POST", "/nodes/urd/qemu/999999/status/stop"), ("POST", "/nodes/urd/lxc/999999/status/stop"),
                         ("POST", "/nodes/urd/qemu/999999/status/start"), ("DELETE", "/nodes/urd/lxc/999999"),
                         ("POST", "/nodes/urd/qemu/999999/snapshot"), ("PUT", "/nodes/urd/qemu/999999/config")):
        code = call(method, path)
        good = code == 403
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} pve {method} {path} refused (403)" + ("" if good else f" (got {code})"))
    return ok


def main() -> int:
    want = set(sys.argv[1:]) or {"all"}
    results = {}
    for name, fn in (("zabbix", zabbix), ("pve", pve)):
        if "all" in want or name in want:
            print(f"== {name}")
            results[name] = fn()
    bad = [n for n, good in results.items() if not good]
    print("read-only proof:", "PASSED" if not bad else f"FAILED ({', '.join(bad)})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
