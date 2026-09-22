#!/usr/bin/env python3
# ansible/roles/control-node/files/frigg-reauth-listener.py
#
# Frigg re-auth watchtower: auto-recovers claude-remote-control from the RC
# login-invalidation crash-loop (docs/known-issues/frigg-control-node.md item
# 18) instead of just alerting on it. Self-checks the unit's failed state,
# drives `claude auth login` under a pty, posts the resulting auth URL to
# Hermod/Discord, and serves a one-page paste-back form for the code.
# Reachable only via the internal-only frigg-auth.niflheim.xiiisins.com AGH
# rewrite - no UCG port-forward, no external exposure.
#
# Config is entirely via environment variables (set by the systemd unit) so
# this file stays a plain static script, not a Jinja template.

import json
import os
import pty
import re
import select
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE_NAME = os.environ.get("REAUTH_SERVICE_NAME", "claude-remote-control")
PORT = int(os.environ.get("REAUTH_PORT", "8686"))
CHECK_INTERVAL = int(os.environ.get("REAUTH_CHECK_INTERVAL", "30"))
LOGIN_TIMEOUT = int(os.environ.get("REAUTH_LOGIN_TIMEOUT", "600"))
APPROLE_ENV_PATH = os.environ.get("REAUTH_APPROLE_ENV_PATH", "/etc/frigg/vault-approle.env")
HERMOD_CONFIG_KEY_VAULT_PATH = os.environ.get(
    "REAUTH_HERMOD_CONFIG_KEY_VAULT_PATH", "secret/ansible/hermod/config-key"
)
HERMOD_BASE_URL = os.environ.get("REAUTH_HERMOD_BASE_URL", "http://hermod.niflheim.xiiisins.com")
PASTE_BACK_URL = os.environ.get("REAUTH_PASTE_BACK_URL", f"http://frigg-auth.niflheim.xiiisins.com:{PORT}/")
CREDENTIALS_PATH = os.path.expanduser(
    os.environ.get("REAUTH_CREDENTIALS_PATH", "~/.claude/.credentials.json")
)
VAULT_ADDR = os.environ.get("VAULT_ADDR", "https://vault.niflheim.xiiisins.com")

URL_MARKER = "https://claude.com/cai/oauth/authorize?"

state_lock = threading.Lock()
state = {
    "phase": "idle",  # idle | starting | waiting_for_code | processing
    "url": None,
    "master_fd": None,
    "proc": None,
    "creds_mtime_before": None,
}


def log(msg):
    print(f"[frigg-reauth] {msg}", flush=True)


def read_approle_field(name):
    # Root-only file (0600); ghost reads it via NOPASSWD sudo, same pattern
    # as homelab-env.sh's __frigg_read_approle helper.
    try:
        r = subprocess.run(
            ["sudo", "-n", "grep", "-E", f"^{name}=", APPROLE_ENV_PATH],
            capture_output=True, text=True, timeout=10,
        )
        line = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
        return line.split("=", 1)[1] if "=" in line else ""
    except Exception as e:
        log(f"read_approle_field({name}) failed: {e}")
        return ""


def vault_login():
    role_id = read_approle_field("FRIGG_ROLE_ID")
    secret_id = read_approle_field("FRIGG_SECRET_ID")
    if not role_id or not secret_id:
        log("vault_login: missing RoleID/SecretID from approle env file")
        return None
    try:
        r = subprocess.run(
            ["vault", "write", "-field=token", "auth/approle/login",
             f"role_id={role_id}", f"secret_id={secret_id}"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "VAULT_ADDR": VAULT_ADDR},
        )
        return r.stdout.strip() or None
    except Exception as e:
        log(f"vault_login failed: {e}")
        return None


def hermod_notify(title, body, tag="critical"):
    token = vault_login()
    if not token:
        log("hermod_notify: no vault token, skipping")
        return
    try:
        r = subprocess.run(
            ["vault", "kv", "get", "-field=value", HERMOD_CONFIG_KEY_VAULT_PATH],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "VAULT_ADDR": VAULT_ADDR, "VAULT_TOKEN": token},
        )
        config_key = r.stdout.strip()
        if not config_key:
            log("hermod_notify: empty config-key from vault, skipping")
            return
        url = f"{HERMOD_BASE_URL}/notify/{config_key}"
        payload = json.dumps({"title": title, "body": body, "tag": tag, "format": "markdown"}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            log(f"hermod_notify: posted (status={resp.status})")
    except Exception as e:
        log(f"hermod_notify failed: {e}")


def is_failed():
    r = subprocess.run(["systemctl", "is-failed", f"{SERVICE_NAME}.service"],
                        capture_output=True, text=True)
    return r.returncode == 0


def extract_url(buf):
    # claude auth login prints the URL twice back-to-back (an OSC-8 hyperlink
    # whose visible label is the URL itself) - take everything up to the
    # second occurrence, or to the next whitespace if there's only one.
    i = buf.find(URL_MARKER)
    if i == -1:
        return None
    j = buf.find(URL_MARKER, i + 1)
    if j != -1:
        raw = buf[i:j]
    else:
        m = re.match(r"\S+", buf[i:])
        raw = m.group(0) if m else buf[i:]
    return re.sub(r"[^\x21-\x7e]", "", raw)


def creds_mtime():
    try:
        return os.path.getmtime(CREDENTIALS_PATH)
    except OSError:
        return None


def creds_looks_settled(before):
    now = creds_mtime()
    if now is None:
        return False
    if before is not None and now <= before:
        return False
    # Debounce: make sure the file isn't mid-write.
    time.sleep(1)
    return creds_mtime() == now


def start_attempt():
    with state_lock:
        if state["phase"] != "idle":
            return
        state["phase"] = "starting"

    log("claude-remote-control is failed - starting re-auth attempt")
    subprocess.run(["sudo", "-n", "systemctl", "stop", f"{SERVICE_NAME}.service"], timeout=30)

    before = creds_mtime()
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        ["claude", "auth", "login"],
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        close_fds=True, start_new_session=True,
        cwd=os.path.expanduser("~"),
    )
    os.close(slave_fd)

    with state_lock:
        state.update(phase="waiting_for_code", url=None, master_fd=master_fd,
                     proc=proc, creds_mtime_before=before)

    threading.Thread(target=watch_attempt, daemon=True).start()


def watch_attempt():
    buf = ""
    url_sent = False
    deadline = time.time() + LOGIN_TIMEOUT
    while time.time() < deadline:
        with state_lock:
            master_fd = state["master_fd"]
            proc = state["proc"]
            before = state["creds_mtime_before"]
        if master_fd is None:
            return

        try:
            r, _, _ = select.select([master_fd], [], [], 2.0)
            if master_fd in r:
                buf += os.read(master_fd, 8192).decode(errors="replace")
        except OSError:
            pass

        if not url_sent:
            url = extract_url(buf)
            if url:
                url_sent = True
                with state_lock:
                    state["url"] = url
                hermod_notify(
                    "Frigg: claude-remote-control needs re-auth",
                    "The RC login session was invalidated and auto-recovery "
                    "has kicked in. Visit the URL below, sign in, then paste "
                    f"the resulting code back at {PASTE_BACK_URL} "
                    "(VPN/LAN only):\n\n" + url,
                    tag="critical",
                )
                log(f"posted auth URL to Hermod: {url}")

        if creds_looks_settled(before):
            finish_attempt(success=True)
            return

        if proc.poll() is not None:
            finish_attempt(success=False)
            return

    finish_attempt(success=False, timed_out=True)


def finish_attempt(success, timed_out=False):
    with state_lock:
        proc = state["proc"]
        master_fd = state["master_fd"]
        state.update(phase="idle", url=None, master_fd=None, proc=None, creds_mtime_before=None)
    try:
        if proc and proc.poll() is None:
            proc.terminate()
    except Exception:
        pass
    try:
        if master_fd is not None:
            os.close(master_fd)
    except OSError:
        pass

    if success:
        log("re-auth succeeded - restarting service")
        # reset-failed is required, not cosmetic: a crash-loop that tripped
        # StartLimitBurst leaves the unit refusing new `start` attempts
        # (silently, no journal entry) until StartLimitIntervalSec (300s)
        # elapses on its own. Since we're recovering well inside that
        # window, clear the failure counter first or the restart below is a
        # silent no-op and the unit stays down. Confirmed live 2026-09-22.
        subprocess.run(["sudo", "-n", "systemctl", "reset-failed", f"{SERVICE_NAME}.service"], timeout=30)
        subprocess.run(["sudo", "-n", "systemctl", "start", f"{SERVICE_NAME}.service"], timeout=30)
        hermod_notify(
            "Frigg: claude-remote-control recovered",
            "Re-auth completed and the service was restarted.",
            tag="alert",
        )
    else:
        reason = "timed out waiting for the code" if timed_out else "the login process exited without completing"
        log(f"re-auth attempt failed: {reason}")
        hermod_notify(
            "Frigg: claude-remote-control re-auth failed",
            f"Re-auth attempt failed ({reason}). Will retry on the next check cycle.",
            tag="critical",
        )


def monitor_loop():
    while True:
        with state_lock:
            idle = state["phase"] == "idle"
        if idle and is_failed():
            start_attempt()
        time.sleep(CHECK_INTERVAL)


PAGE_IDLE = """<!doctype html><html><head><title>Frigg re-auth</title></head>
<body style="font-family:sans-serif;max-width:40em;margin:3em auto">
<h1>Frigg re-auth</h1>
<p>No re-auth in progress. claude-remote-control is healthy, or a failure
hasn't been detected yet.</p>
</body></html>"""

PAGE_WAITING = """<!doctype html><html><head><title>Frigg re-auth</title></head>
<body style="font-family:sans-serif;max-width:40em;margin:3em auto">
<h1>Frigg re-auth</h1>
<p>A re-auth is in progress. Visit the URL sent to Discord, sign in, then
paste the resulting code below.</p>
<form method="POST">
<input name="code" style="width:70%;padding:0.5em" autofocus autocomplete="off">
<button type="submit" style="padding:0.5em 1em">Submit</button>
</form>
</body></html>"""

PAGE_SUBMITTED = """<!doctype html><html><head><title>Frigg re-auth</title></head>
<body style="font-family:sans-serif;max-width:40em;margin:3em auto">
<h1>Frigg re-auth</h1><p>Code submitted. Check Discord for confirmation.</p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log("http: " + (fmt % args))

    def _send(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        with state_lock:
            phase = state["phase"]
        self._send((PAGE_WAITING if phase == "waiting_for_code" else PAGE_IDLE).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode(errors="replace")
        params = urllib.parse.parse_qs(raw)
        code = (params.get("code", [""])[0]).strip()

        with state_lock:
            phase = state["phase"]
            master_fd = state["master_fd"]

        if phase == "waiting_for_code" and master_fd is not None and re.fullmatch(r"[A-Za-z0-9._#-]{4,200}", code or ""):
            try:
                os.write(master_fd, (code + "\n").encode())
                log("code submitted via web form")
            except OSError as e:
                log(f"failed writing code to pty: {e}")

        self._send(PAGE_SUBMITTED.encode())


def main():
    threading.Thread(target=monitor_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"listening on :{PORT}, checking {SERVICE_NAME}.service every {CHECK_INTERVAL}s")
    server.serve_forever()


if __name__ == "__main__":
    main()
