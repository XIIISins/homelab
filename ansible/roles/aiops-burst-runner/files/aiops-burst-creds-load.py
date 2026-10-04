#!/usr/bin/env python3
"""aiops-burst-creds-load: put the burst runner's credentials on tmpfs for the service.

Runs as root from aiops-burst-creds.service (a dependency of aiops-burst-runner.service). It logs in to Vault with the runner's OWN AppRole
(/etc/aiops-burst/approle.env, root-only; policy `aiops-burst-runner` reads only secret/ansible/aiops/burst/*, the burst tailnet auth key and
the fleet ssh key), reads one document and writes ONE file, /run/aiops-burst-creds/env, root-owned 0400, in systemd EnvironmentFile format.
systemd (root) injects it into the runner; the runner's unix user can never open the file; it exists only on tmpfs. Nothing secret is printed.

  env   {digitalocean_token, aws_access_key_id, aws_secret_access_key[, aws_default_region]}
        -> DIGITALOCEAN_TOKEN (the burst-scoped token), AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION (a state-only identity:
           the burst module's own state key), and the AppRole as ANSIBLE_HASHI_VAULT_* so the burst playbook's one lookup (the tailnet auth key)
           authenticates as THIS role.

Exit non-zero on any failure (missing field, short value, control characters) so the runner never starts with a partial or stale set.
"""
import json
import os
import sys
import urllib.request


def log(msg):
    print(f"[aiops-burst-creds-load] {msg}", flush=True)


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
    return creds["AIOPS_BURST_ROLE_ID"], creds["AIOPS_BURST_SECRET_ID"]


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
    approle = env.get("AIOPS_BURST_APPROLE_ENV", "/etc/aiops-burst/approle.env")
    env_path = env["AIOPS_BURST_ENV_VAULT_PATH"]
    out = env["AIOPS_BURST_ENV_FILE"]
    agent_sock = env["AIOPS_BURST_AGENT_SOCK"]
    pub_path = env["AIOPS_BURST_PUB_PATH"]

    role_id, secret_id = read_approle(approle)
    login = http_json("POST", f"{vault}/v1/auth/approle/login", body={"role_id": role_id, "secret_id": secret_id})
    doc = http_json("GET", f"{vault}/v1/{env_path}", headers={"X-Vault-Token": login["auth"]["client_token"]})["data"]["data"]

    lines = [
        env_line("DIGITALOCEAN_TOKEN", field(doc, "digitalocean_token", 32)),
        env_line("AWS_ACCESS_KEY_ID", field(doc, "aws_access_key_id", 16)),
        env_line("AWS_SECRET_ACCESS_KEY", field(doc, "aws_secret_access_key", 32)),
        env_line("AWS_DEFAULT_REGION", str(doc.get("aws_default_region") or "eu-west-1").strip()),
        # the burst playbook's tailnet-key lookup authenticates as this same role (community.hashi_vault reads these)
        env_line("ANSIBLE_HASHI_VAULT_ADDR", vault),
        env_line("ANSIBLE_HASHI_VAULT_AUTH_METHOD", "approle"),
        env_line("ANSIBLE_HASHI_VAULT_ROLE_ID", role_id),
        env_line("ANSIBLE_HASHI_VAULT_SECRET_ID", secret_id),
        # the private ssh-agent holds the fleet key in memory; ssh/ansible name the PUBLIC half as the identity (IdentitiesOnly=yes)
        env_line("SSH_AUTH_SOCK", agent_sock),
        env_line("ANSIBLE_PRIVATE_KEY_FILE", pub_path),
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
