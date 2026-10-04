#!/usr/bin/env python3
"""aiops-author-creds-load: put the PR author's credentials on tmpfs for the dispatcher.

Runs as root from aiops-author-creds.service (a dependency of aiops-author.service). It logs in to Vault with the author's OWN
AppRole (/etc/aiops-author/approle.env, root-only; policy `aiops-author` reads exactly four documents) and writes FOUR files
into /run/aiops-author-creds/, each owned by the dispatcher's unix user, mode 0400:

  pat           the GitHub token (field `token`)             -> only ever handed to `git push` and the GitHub API
  anthropic     the model API key (field `key`)               -> copied into a job's env file for the drafting session
  author-token  the Toolbelt AUTHOR bearer (field `value`)    -> claim / report
  tools-token   the Toolbelt AUTHOR-TOOLS bearer (`value`)    -> copied into a job's env file for the drafting session

Nothing secret is printed or logged. Exits non-zero on any failure (missing field, short value, control characters) so the
dispatcher never starts with a partial credential set.
"""
import json
import os
import pwd
import sys
import urllib.request


def log(msg):
    print(f"[aiops-author-creds-load] {msg}", flush=True)


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
    return creds["AIOPS_AUTHOR_ROLE_ID"], creds["AIOPS_AUTHOR_SECRET_ID"]


def field(doc, name, min_len, what):
    value = str(doc[name]).strip()
    if len(value) < min_len or any(ord(c) < 32 or ord(c) == 127 for c in value):
        sys.exit(f"{what} in Vault is missing, too short or contains control characters; refusing to start the author")
    return value


def write_file(path, value, uid, gid):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value)
    os.chown(tmp, uid, gid)
    os.chmod(tmp, 0o400)
    os.replace(tmp, path)


def main(env=None):
    env = os.environ if env is None else env
    vault = env.get("VAULT_ADDR", "https://vault.niflheim.xiiisins.com")
    approle = env.get("AIOPS_AUTHOR_APPROLE_ENV", "/etc/aiops-author/approle.env")
    out = env["AIOPS_AUTHOR_CREDS_DIR"]
    pw = pwd.getpwnam(env["AIOPS_AUTHOR_OWNER"])
    wanted = {
        "pat": (env["AIOPS_AUTHOR_PAT_PATH"], "token", 20),
        "anthropic": (env["AIOPS_AUTHOR_ANTHROPIC_PATH"], "key", 20),
        "author-token": (env["AIOPS_AUTHOR_TOKEN_PATH"], "value", 32),
        "tools-token": (env["AIOPS_AUTHOR_TOOLS_TOKEN_PATH"], "value", 32),
    }
    role_id, secret_id = read_approle(approle)
    login = http_json("POST", f"{vault}/v1/auth/approle/login", body={"role_id": role_id, "secret_id": secret_id})
    hdr = {"X-Vault-Token": login["auth"]["client_token"]}
    values = {}
    for name, (path, fld, min_len) in wanted.items():
        doc = http_json("GET", f"{vault}/v1/{path}", headers=hdr)["data"]["data"]
        values[name] = field(doc, fld, min_len, name)
    for name, value in values.items():
        write_file(os.path.join(out, name), value, pw.pw_uid, pw.pw_gid)
    log(f"credentials written to {out} (owner {pw.pw_name}, mode 0400)")  # the path, never a value


if __name__ == "__main__":
    main()
