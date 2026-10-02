#!/usr/bin/env python3
"""frigg-ssh-agent-load: load the fleet ansible SSH key from Vault into Frigg's ssh-agent.

Runs as root from frigg-ssh-agent.service (ExecStartPost) because the Vault AppRole
secret-zero (/etc/frigg/vault-approle.env) is root-only. The private key goes
Vault -> this process's memory -> `ssh-add -` over a pipe -> the agent's memory.
It is NEVER written to a file, put on a command line or logged. Only the PUBLIC key is
written (for `-i <file>.pub`, see ansible.cfg: IdentitiesOnly=yes makes ssh use an
agent key only when an identity file names it).

Exit non-zero on any failure: the unit then stops and systemd's Restart= retries, so
the agent never sits there running but empty.
"""
import json
import os
import pwd
import subprocess
import sys
import time
import urllib.request

SOCK = os.environ.get("SSH_AUTH_SOCK", "/run/frigg-ssh-agent/agent.sock")
VAULT_ADDR = os.environ.get("VAULT_ADDR", "https://vault.niflheim.xiiisins.com")
APPROLE_ENV_PATH = os.environ.get("FRIGG_SSH_AGENT_APPROLE_ENV", "/etc/frigg/vault-approle.env")
KEY_PATH = os.environ.get("FRIGG_SSH_AGENT_KEY_PATH", "secret/data/ansible/frigg/ssh-private-key")
KEY_FIELD = os.environ.get("FRIGG_SSH_AGENT_KEY_FIELD", "value")
PUB_PATH = os.environ.get("FRIGG_SSH_AGENT_PUB_PATH", "")
PUB_OWNER = os.environ.get("FRIGG_SSH_AGENT_PUB_OWNER", "")


def log(msg):
    print(f"[frigg-ssh-agent-load] {msg}", flush=True)


def http_json(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed https Vault URL
        return json.load(resp)


def read_approle():
    creds = {}
    with open(APPROLE_ENV_PATH, encoding="utf-8") as fh:
        for line in fh:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                creds[k.strip()] = v.strip()
    return creds["FRIGG_ROLE_ID"], creds["FRIGG_SECRET_ID"]


def wait_for_socket(timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(SOCK):
            return
        time.sleep(0.25)
    sys.exit(f"agent socket {SOCK} did not appear within {timeout}s")


def agent(*args, stdin=None):
    env = dict(os.environ, SSH_AUTH_SOCK=SOCK)
    return subprocess.run(["ssh-add", *args], input=stdin, env=env, capture_output=True, check=False)


def main():
    wait_for_socket()
    role_id, secret_id = read_approle()
    login = http_json("POST", f"{VAULT_ADDR}/v1/auth/approle/login", body={"role_id": role_id, "secret_id": secret_id})
    token = login["auth"]["client_token"]
    secret = http_json("GET", f"{VAULT_ADDR}/v1/{KEY_PATH}", headers={"X-Vault-Token": token})
    key = secret["data"]["data"][KEY_FIELD]
    if not key.endswith("\n"):
        key += "\n"  # ssh-add rejects a key without a trailing newline

    # Start from an empty agent so a restart/reload never stacks identities.
    agent("-D")
    res = agent("-", stdin=key.encode())
    del key, secret
    if res.returncode != 0:
        sys.exit(f"ssh-add failed (exit {res.returncode}): {res.stderr.decode(errors='replace').strip()[:200]}")

    listing = agent("-l")
    fingerprints = listing.stdout.decode().strip()
    if listing.returncode != 0 or "SHA256:" not in fingerprints:
        sys.exit("agent holds no identity after ssh-add")
    log(f"loaded: {fingerprints.splitlines()[0]}")  # fingerprint only; public information

    if PUB_PATH:
        pub = agent("-L").stdout.decode().strip().splitlines()[0] + "\n"
        tmp = PUB_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(pub)
        os.chmod(tmp, 0o644)
        if PUB_OWNER:
            pw = pwd.getpwnam(PUB_OWNER)
            os.chown(tmp, pw.pw_uid, pw.pw_gid)
        os.replace(tmp, PUB_PATH)
        log(f"public key written to {PUB_PATH}")


if __name__ == "__main__":
    main()
