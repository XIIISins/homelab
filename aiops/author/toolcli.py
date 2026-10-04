#!/usr/bin/env python3
"""toolcli: the drafting session's only door to live state. `toolcli.py <tool> '<json args>'`.

Calls the Toolbelt's read-only tools (git.log, zabbix.problems, kube.get, logs.query, ...) with the author-tools token from
the environment (AIOPS_TOOLS_URL, AIOPS_TOOLS_TOKEN). That role can reach /tool/* and nothing else; every call is audited
by the Toolbelt like the diagnosis agent's. Output is the JSON answer, secret-scrubbed again here; exit 1 on any error.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

_SECRETISH = re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)\S{6,}")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or not re.fullmatch(r"[a-z]+\.[a-z_]+", argv[1]):
        print("usage: toolcli.py <tool> ['<json args>']   e.g. git.log '{\"path\": \"docs/incidents\"}'", file=sys.stderr)
        return 2
    try:
        args = json.loads(argv[2]) if len(argv) > 2 else {}
        url, token = os.environ["AIOPS_TOOLS_URL"].rstrip("/"), os.environ["AIOPS_TOOLS_TOKEN"]
    except (ValueError, KeyError) as e:
        print(f"toolcli: bad input or environment: {type(e).__name__}", file=sys.stderr)
        return 2
    req = urllib.request.Request(f"{url}/tool/{argv[1]}", method="POST", data=json.dumps({"args": args}).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        print(f"toolcli: HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"toolcli: {type(e).__name__}", file=sys.stderr)
        return 1
    print(_SECRETISH.sub(r"\1\2[redacted]", body)[:60000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
