#!/usr/bin/env python3
"""A loopback-only, read-only window onto two GitHub API paths, for the Toolbelt (Phase 10h2 PR canary tests).

The Toolbelt's unit has `IPAddressDeny=any` on purpose (it holds read tokens for most of the homelab, so it must not be able to
talk to the internet). To check an agent PR before a canary test it needs exactly two public facts from GitHub: the head sha of an
agent branch and the compare of that sha against main. This process, in its own unit that may reach the internet and nothing
private, serves ONLY those two GET paths for ONE repository on 127.0.0.1 and refuses everything else, so the Toolbelt keeps its
sandbox and the proxy can answer nothing it was not built to answer. It forwards unauthenticated (the repo is public), caps the
answer size, and relays GitHub's status code, so a rate limit or an outage is visible to the caller as such.

    github_read_proxy.py --listen 127.0.0.1:8092 --repo XIIISins/homelab
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://api.github.com"
MAX_BYTES = 4 * 1024 * 1024


def allowed(repo: str):
    r = re.escape(repo)
    return [re.compile(rf"^/repos/{r}/git/ref/heads/agent/(?:drift|capacity|k8s|rightsizing)/[0-9]+-[a-z0-9][a-z0-9-]*$"),
            re.compile(rf"^/repos/{r}/compare/main\.\.\.[0-9a-f]{{40}}$")]


def upstream_get(path: str, timeout: float = 8.0) -> tuple[int, bytes]:
    req = urllib.request.Request(UPSTREAM + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "aiops-github-read-proxy",
                                                           "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        return e.code, e.read(MAX_BYTES + 1)
    except (OSError, ValueError):
        return 0, b""


def make_handler(repo: str, get=upstream_get):
    rules = allowed(repo)

    class H(BaseHTTPRequestHandler):
        server_version = "aiops-github-read"
        sys_version = ""

        def log_message(self, fmt, *args):  # noqa: D401 - no access log: the Toolbelt audits what it asks for
            return

        def _send(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if "?" in self.path or not any(rx.match(self.path) for rx in rules):
                return self._send(404, b'{"error":"not a path this proxy serves"}')
            status, body = get(self.path)
            if status == 0:
                return self._send(502, b'{"error":"upstream unreachable"}')
            if len(body) > MAX_BYTES:
                return self._send(502, b'{"error":"upstream answer too large"}')
            self._send(status, body)

        def _refuse(self):
            self._send(405, b'{"error":"read-only"}')

        do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _refuse  # noqa: N815

    return H


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1:8092")
    ap.add_argument("--repo", default="XIIISins/homelab")
    a = ap.parse_args(argv)
    host, _, port = a.listen.rpartition(":")
    if host not in ("127.0.0.1", "localhost"):
        print("github_read_proxy: refusing to listen anywhere but loopback", file=sys.stderr)
        return 2
    ThreadingHTTPServer((host, int(port)), make_handler(a.repo)).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
