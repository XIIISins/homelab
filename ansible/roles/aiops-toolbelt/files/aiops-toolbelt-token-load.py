#!/usr/bin/env python3
"""aiops-toolbelt-token-load: put the Toolbelt API's secrets on tmpfs for the service.

Runs as root from aiops-toolbelt.service (ExecStartPre=+) because the Vault AppRole
secret-zero (/etc/frigg/vault-approle.env, the same one frigg-ssh-agent-load uses) is
root-only. The service itself never sees Vault credentials: it gets one file,
/run/aiops-toolbelt/token, mode 0400, owned by the service user, on tmpfs (gone at
reboot, never in a backup). Nothing secret is printed or logged.

Exit non-zero on any failure so the unit does not start with a missing or stale token.

Read-only backend credentials (the `pve` token, later zabbix/netbox/...) are written the same
way to <creds dir>/<name>.json (0400, the whole KV document). Those are OPTIONAL: a missing
one only logs a warning, so a not-yet-minted credential cannot stop the API from starting;
its tool then answers "credential missing" instead.
"""
import contextlib
import json
import os
import pwd
import sys
import urllib.request


def log(msg):
    print(f"[aiops-toolbelt-token-load] {msg}", flush=True)


def http_json(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed Vault URL
        return json.load(resp)


def read_approle(path):
    creds = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                creds[k.strip()] = v.strip()
    return creds["FRIGG_ROLE_ID"], creds["FRIGG_SECRET_ID"]


def write_secret(path, content, owner):
    """Atomic 0400 write, owned by the service user."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    if owner and os.geteuid() == 0:
        pw = pwd.getpwnam(owner)
        os.chown(tmp, pw.pw_uid, pw.pw_gid)
    os.chmod(tmp, 0o400)
    os.replace(tmp, path)


def main(env=None):
    env = os.environ if env is None else env
    vault = env.get("VAULT_ADDR", "https://vault.niflheim.xiiisins.com")
    approle = env.get("AIOPS_TOOLBELT_APPROLE_ENV", "/etc/frigg/vault-approle.env")
    kv_path = env.get("AIOPS_TOOLBELT_TOKEN_VAULT_PATH", "secret/data/ansible/aiops/toolbelt-token")
    field = env.get("AIOPS_TOOLBELT_TOKEN_FIELD", "value")
    out = env["AIOPS_TOOLBELT_TOKEN_FILE"]
    owner = env.get("AIOPS_TOOLBELT_OWNER", "")

    role_id, secret_id = read_approle(approle)
    login = http_json("POST", f"{vault}/v1/auth/approle/login", body={"role_id": role_id, "secret_id": secret_id})
    secret = http_json("GET", f"{vault}/v1/{kv_path}", headers={"X-Vault-Token": login["auth"]["client_token"]})
    token = secret["data"]["data"][field].strip()
    if len(token) < 32:
        sys.exit("token in Vault is shorter than 32 characters; refusing to start the API with it")

    write_secret(out, token + "\n", owner)
    log(f"token written to {out} (mode 0400)")  # the path, never the value

    extra = json.loads(env.get("AIOPS_TOOLBELT_EXTRA_CREDS", "{}") or "{}")
    creds_dir = env.get("AIOPS_TOOLBELT_CREDS_DIR", "")
    if extra and creds_dir:
        os.makedirs(creds_dir, exist_ok=True)
        if owner and os.geteuid() == 0:
            pw = pwd.getpwnam(owner)
            os.chown(creds_dir, pw.pw_uid, pw.pw_gid)
        os.chmod(creds_dir, 0o750)
        client = login["auth"]["client_token"]
        for name, path in sorted(extra.items()):
            try:
                doc = http_json("GET", f"{vault}/v1/{path}", headers={"X-Vault-Token": client})["data"]["data"]
            except Exception as e:  # not minted yet / no access: warn, keep going
                log(f"WARNING: credential {name!r} not loaded ({type(e).__name__}); its tool will report it missing")
                with contextlib.suppress(FileNotFoundError):
                    os.remove(os.path.join(creds_dir, name + ".json"))
                continue
            write_secret(os.path.join(creds_dir, name + ".json"), json.dumps(doc), owner)
            log(f"credential {name!r} written to {creds_dir}/{name}.json (mode 0400)")


if __name__ == "__main__":
    main()
