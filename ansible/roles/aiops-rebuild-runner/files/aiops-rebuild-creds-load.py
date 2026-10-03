#!/usr/bin/env python3
"""aiops-rebuild-creds-load: put the rebuild runner's credentials on tmpfs for the service.

Runs as root from aiops-rebuild-creds.service (a dependency of aiops-rebuild-runner.service). It logs in to Vault with the
runner's OWN AppRole (/etc/aiops-rebuild/approle.env, root-only; policy `aiops-rebuild-runner` reads only
secret/ansible/aiops/rebuild/*), reads two documents and writes ONE file, /run/aiops-rebuild-creds/env, root-owned 0400, in
systemd EnvironmentFile format. systemd (root) injects it into the runner; the runner's unix user can never open the
file, and it exists only on tmpfs (gone at reboot, never in a backup). Nothing secret is printed or logged.

  pve-token  {token_id, secret}  -> TF_VAR_proxmox_api_token="USER@REALM!NAME=SECRET"  (the pool-scoped PVE token)
  env        {aws_access_key_id, aws_secret_access_key, ssh_public_key[, aws_default_region]}
             -> AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION (the narrow state identity),
                TF_VAR_ssh_public_key (public: injected into the canary LXCs by the module)

Exit non-zero on any failure (missing field, short value, control characters) so the runner never starts with a partial or
stale credential set.
"""
import json
import os
import sys
import urllib.request


def log(msg):
    print(f"[aiops-rebuild-creds-load] {msg}", flush=True)


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
    return creds["AIOPS_REBUILD_ROLE_ID"], creds["AIOPS_REBUILD_SECRET_ID"]


def field(doc, name, min_len=1):
    value = str(doc[name]).strip()
    if len(value) < min_len or any(ord(c) < 32 or ord(c) == 127 for c in value):
        sys.exit(f"{name} in Vault is missing, too short or contains control characters; refusing to start the runner")
    return value


def env_line(key, value):
    # systemd EnvironmentFile: double-quoted value, backslash escapes for \ and ".
    return f'{key}="' + value.replace("\\", "\\\\").replace('"', '\\"') + '"\n'


def main(env=None):
    env = os.environ if env is None else env
    vault = env.get("VAULT_ADDR", "https://vault.niflheim.xiiisins.com")
    approle = env.get("AIOPS_REBUILD_APPROLE_ENV", "/etc/aiops-rebuild/approle.env")
    pve_path = env["AIOPS_REBUILD_PVE_TOKEN_VAULT_PATH"]
    env_path = env["AIOPS_REBUILD_ENV_VAULT_PATH"]
    out = env["AIOPS_REBUILD_ENV_FILE"]

    role_id, secret_id = read_approle(approle)
    login = http_json("POST", f"{vault}/v1/auth/approle/login", body={"role_id": role_id, "secret_id": secret_id})
    hdr = {"X-Vault-Token": login["auth"]["client_token"]}
    pve = http_json("GET", f"{vault}/v1/{pve_path}", headers=hdr)["data"]["data"]
    doc = http_json("GET", f"{vault}/v1/{env_path}", headers=hdr)["data"]["data"]

    lines = [
        env_line("TF_VAR_proxmox_api_token", field(pve, "token_id", 8) + "=" + field(pve, "secret", 16)),
        env_line("AWS_ACCESS_KEY_ID", field(doc, "aws_access_key_id", 16)),
        env_line("AWS_SECRET_ACCESS_KEY", field(doc, "aws_secret_access_key", 32)),
        env_line("AWS_DEFAULT_REGION", str(doc.get("aws_default_region") or "eu-west-1").strip()),
        env_line("TF_VAR_ssh_public_key", field(doc, "ssh_public_key", 32)),
    ]
    tmp = out + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("".join(lines))
    os.chmod(tmp, 0o400)
    os.replace(tmp, out)
    log(f"credentials written to {out} (root-owned, mode 0400)")  # the path, never a value


if __name__ == "__main__":
    main()
