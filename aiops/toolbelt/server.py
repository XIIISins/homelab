#!/usr/bin/env python3
"""Toolbelt API HTTP shell (Phase 10d2, extended in 10e). Stdlib only; the logic lives in core.py and actions.py.

Two credentials, two roles, two source-IP allow-lists. A caller holds exactly one:

  agent     (Gna / n8n, the LLM host: it reads untrusted text, so it holds no authority)
    POST /ingest/zabbix            native aiops.zabbix-event/v1 -> {action, incident_id, ...}
    GET  /group/<id>               the correlated incident (alerts, state, priority, model hint)
    POST /group/<id>/state         {"state": "running|posted|resolved", "thread_id": "..."}
    POST /diagnosis/<id>           {"diagnosis": {...}, "model": "..."} -> validated + Discord-ready, or 422 + problems;
                                   its proposed_actions become PROPOSALS (never executions)
    POST /tool/<name>              {"args": {...}, "incident_id": N | "conversation_id": N, "turn_id": N}; header
                                   X-AIOPS-Replay: <scenario> replays
    POST /chat/turn                {"thread_id", "author", "content"} -> conversation, history, incident context, limits
    POST /chat/reply               {"conversation_id", "turn_id", "content"} -> scrubbed, Discord-sized answer
    POST /proposals                {"action_id", "params", "reason", "conversation_id"|"incident_id"} -> a PENDING proposal
    GET  /proposals/<id>           one proposal (read-only)
    GET  /replay/<scenario>/latest, GET /watchdog, GET /stats

  approver  (the Discord bot: transport + the only party that can decide)
    GET  /proposals/feed?after=N   state changes to render as Discord cards
    GET  /proposals[?state=a,b]    proposals in those states (default: open ones)
    GET  /proposals/<id>
    POST /proposals/<id>/decision  {"decision": "approve|reject", "by": "<discord user id>", "ref": "...", "params_hash": "..."}
    POST /proposals/<id>/message   {"message_ref": "<discord message id>"}
    GET  /flags, POST /flags/<kill_switch|maintenance>   {"value": bool, "by": "<discord user id>", "reason": "..."}
    GET  /incident/<id>/draft   (approver: the mechanical incident write-up, Phase 10h3)
    GET  /status, GET /stats, GET /report?days=N   (what autonomous healing did and why it did not: the soak's evidence)

  agent + approver (10h2)
    POST /change-requests          {"source", "class", "title", "body", "allowed_paths"?, "source_ref"?, "by"?}: file a request for ONE agent-authored PR (lands pending;
                                   the agent role cannot file as `operator`)
  approver (10h2)
    GET  /change-requests[?state=a,b]   GET /change-requests/feed?after=N   GET /change-requests/<id>
    POST /change-requests/<id>/decision {"decision": "approve|reject|cancel", "by": "<discord user id>"}
    POST /change-requests/<id>/message  {"message_ref": "...", "thread_id"?: "..."}
  author (10h2: the Frigg dispatcher, which holds the GitHub token; the Toolbelt never does)
    POST /change-requests/claim          the oldest approved request, now running (null + why when a cap or flag says no)
    GET  /change-requests?state=pr-open   GET /change-requests/<id>   POST /change-requests/<id>/report {"state": "pr-open|failed|no-change|merged|closed", "pr_url", "branch", "summary", "tests", "error"}
  author-tools (10h2: the drafting session)   POST /tool/<tool>  only: the same read-only tools, nothing else

  GET /healthz                     liveness (no auth, no data)

Every other route needs `Authorization: Bearer <token>` AND a client address inside that role's allow-list: two
independent checks, because the token alone is one leaked file away from being a hole. A role cannot reach the other
role's routes (403, audited): the agent can create a proposal but never decide one. Tokens are read from files (never
an env var or flag, so they are not visible in /proc or `ps`). The server speaks plain HTTP on an internal interface.
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402

MAX_BODY = 64 * 1024
AGENT, APPROVER, AUTHOR, AUTHOR_TOOLS = "agent", "approver", "author", "author-tools"
_ID = r"(\d+)"
_TOOL = re.compile(r"^/tool/([a-z]+\.[a-z_]+)$")
_REPLAY = re.compile(r"^/replay/([a-z0-9-]+)/latest$")
_FLAG = re.compile(r"^/flags/([a-z_]+)$")


class Role:
    def __init__(self, name: str, token: str, allow: list):
        self.name, self.token, self.allow = name, token, allow


def make_handler(tb: core.Toolbelt, token: str, allow: list, approver_token: str | None = None, approver_allow: list | None = None,
                 author_token: str | None = None, author_allow: list | None = None,
                 author_tools_token: str | None = None, author_tools_allow: list | None = None):
    roles = [Role(AGENT, token, allow)]
    if approver_token:
        roles.append(Role(APPROVER, approver_token, approver_allow or []))
    if author_token:
        roles.append(Role(AUTHOR, author_token, author_allow or []))
    if author_tools_token:
        roles.append(Role(AUTHOR_TOOLS, author_tools_token, author_tools_allow or []))

    class Handler(BaseHTTPRequestHandler):
        server_version = "aiops-toolbelt"
        sys_version = ""

        def log_message(self, fmt, *args):  # noqa: D401 - audit goes through tb.audit, not stderr
            return

        def _send(self, status: int, body) -> None:
            raw = json.dumps(body, sort_keys=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _role(self) -> str | None:
            """The caller's role, or None. Both the source address and the token must belong to the same role."""
            try:
                ip = ipaddress.ip_address(self.client_address[0])
            except ValueError:
                return None
            candidates = [r for r in roles if any(ip in net for net in r.allow)]
            if not candidates:
                tb.audit("denied", reason="ip", client=str(ip), path=self.path)
                return None
            got = self.headers.get("Authorization", "")
            for r in candidates:
                if got.startswith("Bearer ") and hmac.compare_digest(got[7:].encode(), r.token.encode()):
                    return r.name
            tb.audit("denied", reason="token", client=str(ip), path=self.path)
            return None

        def _body(self) -> object:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise core.Rejected(413, "body too large")
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw or b"{}")
            except json.JSONDecodeError:
                raise core.Rejected(400, "body is not JSON")

        def _obj(self) -> dict:
            body = self._body()
            if not isinstance(body, dict):
                raise core.Rejected(400, "body must be an object")
            return body

        def _engine(self):
            if tb.engine is None:
                raise core.Rejected(501, "actions are not enabled on this Toolbelt")
            return tb.engine

        # (method, path regex, roles allowed, handler(match, query))
        def _table(self):
            both, only_agent, only_appr = (AGENT, APPROVER), (AGENT,), (APPROVER,)
            return [
                ("POST", re.compile(r"^/ingest/zabbix$"), only_agent,
                 lambda m, q: tb.ingest_zabbix(self._body(), self.headers.get("X-AIOPS-Replay") or None)),
                ("GET", re.compile(rf"^/group/{_ID}$"), only_agent, lambda m, q: tb.group(int(m.group(1)))),
                ("POST", re.compile(rf"^/group/{_ID}/state$"), only_agent, self._h_state),
                ("POST", _TOOL, (AGENT, AUTHOR_TOOLS), self._h_tool),
                ("POST", re.compile(rf"^/diagnosis/{_ID}$"), only_agent, lambda m, q: tb.diagnose(int(m.group(1)), self._body())),
                ("GET", _REPLAY, only_agent, lambda m, q: tb.replay_latest(m.group(1))),
                ("GET", re.compile(r"^/watchdog$"), only_agent, lambda m, q: tb.watchdog()),
                ("GET", re.compile(r"^/stats$"), both, lambda m, q: tb.stats()),
                # chat + proposals: the agent talks and proposes
                ("POST", re.compile(r"^/chat/turn$"), only_agent, self._h_chat_turn),
                ("POST", re.compile(r"^/chat/reply$"), only_agent, self._h_chat_reply),
                ("POST", re.compile(r"^/proposals$"), only_agent, lambda m, q: tb.propose(self._obj())),
                # the approver renders and decides
                ("GET", re.compile(r"^/proposals/feed$"), only_appr,
                 lambda m, q: self._engine().feed(int((q.get("after") or ["0"])[0] or 0), int((q.get("limit") or ["50"])[0] or 50))),
                ("GET", re.compile(r"^/proposals$"), only_appr, self._h_list),
                ("GET", re.compile(rf"^/proposals/{_ID}$"), both, lambda m, q: self._engine().get(int(m.group(1)))),
                ("POST", re.compile(rf"^/proposals/{_ID}/decision$"), only_appr, self._h_decision),
                ("POST", re.compile(rf"^/proposals/{_ID}/message$"), only_appr, self._h_message),
                ("POST", re.compile(r"^/change-requests$"), (AGENT, APPROVER), self._h_cr_create),
                ("GET", re.compile(r"^/change-requests/feed$"), only_appr,
                 lambda m, q: self._cr().feed(int((q.get("after") or ["0"])[0] or 0))),
                ("GET", re.compile(r"^/change-requests$"), (APPROVER, AUTHOR), self._h_cr_list),
                ("GET", re.compile(rf"^/change-requests/{_ID}$"), (APPROVER, AUTHOR), lambda m, q: self._cr().get(int(m.group(1)))),
                ("POST", re.compile(rf"^/change-requests/{_ID}/decision$"), only_appr, self._h_cr_decision),
                ("POST", re.compile(rf"^/change-requests/{_ID}/message$"), only_appr, self._h_cr_message),
                ("POST", re.compile(r"^/change-requests/claim$"), (AUTHOR,), lambda m, q: self._cr().claim()),
                ("POST", re.compile(rf"^/change-requests/{_ID}/report$"), (AUTHOR,), self._h_cr_report),
                ("GET", re.compile(r"^/flags$"), only_appr, lambda m, q: self._engine().flags()),
                ("POST", _FLAG, only_appr, self._h_flag),
                ("GET", re.compile(r"^/status$"), only_appr, lambda m, q: tb.status()),
                ("GET", re.compile(rf"^/incident/{_ID}/draft$"), only_appr, lambda m, q: tb.incident_draft(int(m.group(1)))),
                ("GET", re.compile(r"^/report$"), only_appr, lambda m, q: tb.report(int((q.get("days") or ["14"])[0] or 14))),
            ]

        def _h_state(self, m, q):
            body = self._obj()
            if not isinstance(body.get("state"), str):
                raise core.Rejected(400, "need {\"state\": ...}")
            return tb.set_state(int(m.group(1)), body["state"], body.get("thread_id"))

        def _h_tool(self, m, q):
            body = self._obj()
            if self._role_name == AUTHOR_TOOLS:  # a drafting session: attributed to its change request, never to an incident
                return tb.author_tool(m.group(1), body.get("args", {}), body.get("change_request_id"))
            return tb.call_tool(m.group(1), body.get("args", {}), body.get("incident_id"), self.headers.get("X-AIOPS-Replay"),
                                body.get("conversation_id"), body.get("turn_id"))

        def _h_chat_turn(self, m, q):
            b = self._obj()
            return tb.chat_turn(b.get("thread_id"), b.get("author"), b.get("content"))

        def _h_chat_reply(self, m, q):
            b = self._obj()
            return tb.chat_reply(b.get("conversation_id"), b.get("turn_id"), b.get("content"))

        def _h_list(self, m, q):
            raw = (q.get("state") or [""])[0]
            states = tuple(s for s in raw.split(",") if s) or ("pending", "approved", "running")
            return {"proposals": self._engine().list(states)}

        def _h_decision(self, m, q):
            b = self._obj()
            return self._engine().decide(int(m.group(1)), str(b.get("decision", "")), by=b.get("by"), ref=str(b.get("ref", "")),
                                         params_hash_seen=str(b.get("params_hash", "")))

        def _h_message(self, m, q):
            ref = self._obj().get("message_ref")
            if not isinstance(ref, str) or not ref:
                raise core.Rejected(400, "need message_ref")
            return self._engine().set_message(int(m.group(1)), ref)

        def _cr(self):
            if tb.cr is None:
                raise core.Rejected(501, "change requests are not enabled on this Toolbelt")
            return tb.cr

        def _h_cr_create(self, m, q):
            b = self._obj()
            who = "n8n" if self._role_name == AGENT else str(b.get("by") or "")
            if self._role_name == AGENT and b.get("source") == "operator":
                raise core.Rejected(403, "the agent cannot file a request as the operator")
            return self._cr().create(source=str(b.get("source", "")), class_=str(b.get("class", "")), title=b.get("title"), body=b.get("body"),
                                     allowed_paths=b.get("allowed_paths"), source_ref=str(b.get("source_ref") or ""), created_by=who)

        def _h_cr_list(self, m, q):
            raw = (q.get("state") or [""])[0]
            states = tuple(s for s in raw.split(",") if s) or ("pending", "approved", "running", "pr-open")
            return {"change_requests": self._cr().list(states)}

        def _h_cr_decision(self, m, q):
            b = self._obj()
            return self._cr().decide(int(m.group(1)), str(b.get("decision", "")), by=b.get("by"), ref=str(b.get("ref", "")))

        def _h_cr_message(self, m, q):
            b = self._obj()
            ref = b.get("message_ref")
            if not isinstance(ref, str) or not ref:
                raise core.Rejected(400, "need message_ref")
            return self._cr().set_message(int(m.group(1)), ref, str(b.get("thread_id") or ""))

        def _h_cr_report(self, m, q):
            b = self._obj()
            return self._cr().report(int(m.group(1)), state=str(b.get("state", "")), pr_url=str(b.get("pr_url") or ""), branch=str(b.get("branch") or ""),
                                     summary=str(b.get("summary") or ""), tests=b.get("tests"), error=str(b.get("error") or ""))

        def _h_flag(self, m, q):
            b = self._obj()
            if not isinstance(b.get("value"), bool):
                raise core.Rejected(400, "need {\"value\": true|false, \"by\": ...}")
            return self._engine().set_flag(m.group(1), b["value"], by=b.get("by"), reason=str(b.get("reason", "")))

        def _route(self, method: str) -> None:
            split = urlsplit(self.path)
            path, query = split.path, parse_qs(split.query)
            if method == "GET" and path == "/healthz":
                return self._send(200, {"ok": True})
            role = self._role()
            if role is None:
                return self._send(403, {"error": "forbidden"})
            self._role_name = role
            try:
                matched_other_role = False
                for meth, rx, allowed, fn in self._table():
                    m = rx.match(path)
                    if m is None or meth != method:
                        continue
                    if role not in allowed:
                        matched_other_role = True
                        continue
                    return self._send(200, fn(m, query))
                if matched_other_role:
                    tb.audit("denied", reason="role", role=role, method=method, path=path)
                    return self._send(403, {"error": "forbidden"})
            except (core.Rejected, core.actions.Refused) as e:
                return self._send(e.status, {"error": e.message, **e.detail})
            except Exception as e:  # never leak a traceback to the caller; the audit log has it
                tb.audit("error", path=path, error=type(e).__name__)
                return self._send(500, {"error": "internal"})
            # unknown or write-shaped route: refused here, not by the caller's good behaviour
            tb.audit("denied", reason="unknown-route", method=method, path=path)
            return self._send(404, {"error": "not found"})

        def do_GET(self):  # noqa: N802
            self._route("GET")

        def do_POST(self):  # noqa: N802
            self._route("POST")

        def do_PUT(self):  # noqa: N802
            self._route("PUT")

        def do_DELETE(self):  # noqa: N802
            self._route("DELETE")

        def do_PATCH(self):  # noqa: N802
            self._route("PATCH")

    return Handler


def load_runbooks(root: Path) -> set[str]:
    import yaml

    return {r["id"] for r in yaml.safe_load((root / "aiops" / "runbooks.yml").read_text())["runbooks"]}


def load_action_ids(root: Path) -> set[str]:
    import yaml

    return set(yaml.safe_load((root / "aiops" / "actions.yml").read_text())["actions"])


def read_token(path: str, what: str) -> str:
    t = Path(path).read_text().strip()
    if len(t) < 32:
        raise SystemExit(f"{what} too short (need >= 32 chars)")
    return t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--listen", default="127.0.0.1:8090")
    ap.add_argument("--db", default="/var/lib/aiops-toolbelt/toolbelt.sqlite3")
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--allow", action="append", required=True, help="CIDR the AGENT may call from (repeatable)")
    ap.add_argument("--approver-token-file", help="enables the approver role (the Discord bot)")
    ap.add_argument("--approver-allow", action="append", default=[], help="CIDR the APPROVER may call from (repeatable)")
    ap.add_argument("--operators-file", help="Discord user ids (one per line) allowed to decide proposals and flip the kill switch")
    ap.add_argument("--actions", action="store_true", help="enable proposals, approval and the executor (needs --operators-file)")
    ap.add_argument("--exec-cred", default="semaphore-exec", help="creds-dir file name of the executor's Semaphore token (no file = executor disabled)")
    ap.add_argument("--exec-project", type=int, default=0, help="Semaphore project id of the aiops project (0 = look it up by name `aiops`)")
    ap.add_argument("--rebuild-socket", help="unix socket of the rebuild runner (Phase 10g); no flag = rebuild actions refuse with 501")
    ap.add_argument("--author-token-file", help="enables the author role (the Frigg PR dispatcher: claim + report)")
    ap.add_argument("--author-allow", action="append", default=[], help="CIDR the AUTHOR may call from (repeatable)")
    ap.add_argument("--author-tools-token-file", help="enables the author-tools role (the drafting session: read-only /tool/* only)")
    ap.add_argument("--author-tools-allow", action="append", default=[], help="CIDR the AUTHOR-TOOLS role may call from (repeatable)")
    ap.add_argument("--change-requests", action="store_true", help="enable 10h2 change requests (needs --actions)")
    ap.add_argument("--author-repo", default="XIIISins/homelab", help="owner/name a reported PR URL must belong to")
    ap.add_argument("--author-daily-budget", type=int, default=6, help="drafts the author may start per UTC day")
    ap.add_argument("--placement-file", help="JSON {host: hypervisor-node}, refreshed from NetBox")
    ap.add_argument("--daily-run-cap", type=int, default=40)
    ap.add_argument("--replay-dir", help="aiops/replays: recorded tool responses (X-AIOPS-Replay scenarios)")
    ap.add_argument("--creds-dir", help="directory of <name>.json read-only backend credentials (root loader output)")
    ap.add_argument("--repo-dir", help="read-only clone of the homelab repo for the repo-history tools")
    args = ap.parse_args(argv)

    token = read_token(args.token_file, "agent token")
    approver_token = read_token(args.approver_token_file, "approver token") if args.approver_token_file else None
    if approver_token and approver_token == token:
        print("the approver token must differ from the agent token", file=sys.stderr)
        return 2
    cfg = core.Config(db_path=args.db, daily_run_cap=args.daily_run_cap)
    cfg.live = core.tools.LiveConfig(root=core.REPO, repo_dir=Path(args.repo_dir) if args.repo_dir else None,
                                     creds_dir=Path(args.creds_dir) if args.creds_dir else None)
    if args.replay_dir:
        cfg.replay_dir = Path(args.replay_dir)
    if args.placement_file:
        cfg.placement_file = Path(args.placement_file)  # hot-reloaded; a missing file at start is fine
    registry = None
    if args.actions:
        if not args.operators_file or not approver_token:
            print("--actions needs --operators-file and --approver-token-file", file=sys.stderr)
            return 2
        operators = frozenset(x for x in re.split(r"[,\s]+", Path(args.operators_file).read_text()) if x.isdigit())
        if not operators:
            print("--operators-file holds no Discord user id", file=sys.stderr)
            return 2
        registry = core.actions.Registry.from_file(core.REPO / "aiops" / "actions.yml")
        sem = None
        cred = Path(args.creds_dir or "/nonexistent") / f"{args.exec_cred}.json"
        if cred.is_file():
            c = json.loads(cred.read_text())
            sem = core.actions.SemaphoreAPI(c["url"], c["value"], args.exec_project or None)
        cfg.actions = core.actions.ActionConfig(operators=operators, semaphore=sem, runner_socket=args.rebuild_socket or None)
    author_token = read_token(args.author_token_file, "author token") if args.author_token_file else None
    author_tools_token = read_token(args.author_tools_token_file, "author-tools token") if args.author_tools_token_file else None
    all_tokens = [t for t in (token, approver_token, author_token, author_tools_token) if t]
    if len(set(all_tokens)) != len(all_tokens):
        print("every role needs its own token", file=sys.stderr)
        return 2
    if args.change_requests:
        if registry is None:
            print("--change-requests needs --actions", file=sys.stderr)
            return 2
        sys.path.insert(0, str(core.REPO / "aiops" / "author"))
        import scope  # noqa: E402

        cfg.change_requests = core.change_requests.CRConfig(
            classes=scope.load_classes((core.REPO / "aiops" / "author-classes.yml").read_text()), repo=args.author_repo,
            max_started_per_day=args.author_daily_budget)
    import normalize  # noqa: E402 (path set up by core)

    tb = core.Toolbelt(cfg, normalize.load_routes(), load_runbooks(core.REPO), action_ids=load_action_ids(core.REPO), registry=registry)
    host, _, port = args.listen.rpartition(":")
    handler = make_handler(tb, token, [ipaddress.ip_network(a) for a in args.allow], approver_token,
                           [ipaddress.ip_network(a) for a in args.approver_allow],
                           author_token, [ipaddress.ip_network(a) for a in args.author_allow],
                           author_tools_token, [ipaddress.ip_network(a) for a in args.author_tools_allow])
    srv = ThreadingHTTPServer((host, int(port)), handler)
    tb.audit("start", listen=args.listen, daily_run_cap=cfg.daily_run_cap, actions=tb.engine is not None,
             executor=bool(cfg.actions and cfg.actions.semaphore), approver=bool(approver_token),
             change_requests=tb.cr is not None, author=bool(author_token))
    if tb.engine is not None:
        def sweeper():
            while True:
                time.sleep(30)
                try:
                    tb.engine.sweep()
                    if tb.cr is not None:
                        tb.cr.sweep()
                except Exception as e:  # noqa: BLE001 - a failed sweep must not kill the thread
                    tb.audit("error", where="sweep", error=type(e).__name__)
        threading.Thread(target=sweeper, daemon=True, name="proposal-sweeper").start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
