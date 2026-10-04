#!/usr/bin/env python3
"""dispatcher: turns an operator-approved change request into a pull request. Runs on Frigg as `aiops-author`.

The only process that holds the GitHub token, and it never runs an LLM. Loop:
  1. claim the next approved change request from the Toolbelt (which enforces the kill switch, maintenance, concurrency,
     the open-PR limit and the daily budget);
  2. start `aiops-draft@<id>.service` (an unprivileged user with an Anthropic key and read-only tools, no GitHub token);
  3. read back ONLY a patch file: parse it strictly, run the scope rules (aiops/author/scope.py, the same ones CI runs),
     scan the added lines for secrets, apply it to this process's OWN clean clone (never git inside the session's tree),
     commit as the bot identity, push `agent/<class>/<id>-<slug>`, open the PR, report to the Toolbelt;
  4. every few minutes, ask GitHub whether the open PRs were merged or closed and report that.
It refuses to run with a token whose account has admin/maintain on the repo: the `main` ruleset lets admins bypass it, so such
a token could push to main (= deploy). Override only on purpose (--allow-admin-token). Stdout is the audit log (JSON lines).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "toolbelt"))
import scope  # noqa: E402
import tools  # noqa: E402  (redact)

MAX_PATCH = 512 * 1024
_TOKENISH = re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\bAKIA[0-9A-Z]{16}\b|\bxox[bpas]-[A-Za-z0-9-]{10,}")


def audit(event: str, **kw) -> None:
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "component": "aiops-author", "event": event, **kw},
                     sort_keys=True), flush=True)


# ---- the patch: parsed strictly, never trusted --------------------------------------------------------------
class PatchError(Exception):
    pass


def parse_patch(text: str) -> list[dict]:
    """GitHub `pulls/N/files`-shaped dicts from a `git diff --binary --full-index` text. Refuses anything but plain text files
    (no binary, no symlink, no mode change, no submodule)."""
    if len(text.encode()) > MAX_PATCH:
        raise PatchError("the patch is larger than 512 KiB")
    files: list[dict] = []
    cur: dict | None = None
    in_hunk = False
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            m = re.match(r"^diff --git a/(.+) b/(.+)$", line)
            if not m:
                raise PatchError("unparseable diff header")
            cur = {"filename": m.group(2), "status": "modified", "additions": 0, "deletions": 0}
            if m.group(1) != m.group(2):
                cur["previous_filename"] = m.group(1)
                cur["status"] = "renamed"
            files.append(cur)
            in_hunk = False
        elif cur is None:
            continue
        elif not in_hunk and line.startswith("new file mode"):
            if not line.endswith("100644"):
                raise PatchError(f"{cur['filename']}: only regular non-executable files may be added")
            cur["status"] = "added"
        elif not in_hunk and line.startswith("deleted file mode"):
            cur["status"] = "removed"
        elif not in_hunk and (line.startswith("old mode") or line.startswith("new mode")):
            raise PatchError(f"{cur['filename']}: mode changes are not allowed")
        elif not in_hunk and (line.startswith("GIT binary patch") or line.startswith("Binary files")):
            raise PatchError(f"{cur['filename']}: binary changes are not allowed")
        elif not in_hunk and re.match(r"^(new|old) file mode 120000|^index .* 120000$", line):
            raise PatchError(f"{cur['filename']}: symlinks are not allowed")
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            cur["additions"] += 1
        elif in_hunk and line.startswith("-"):
            cur["deletions"] += 1
    if not files:
        raise PatchError("the patch changes nothing")
    return files


def added_lines(text: str) -> list[str]:
    """Every line the patch adds. `+++ b/file` is a header only BEFORE a file's first hunk; inside a hunk a line starting `+++`
    is added content (e.g. `++ password=...`) and must be scanned like any other."""
    out, in_hunk = [], False
    for ln in text.split("\n"):
        if ln.startswith("diff --git "):
            in_hunk = False
        elif ln.startswith("@@"):
            in_hunk = True
        elif in_hunk and ln.startswith("+"):
            out.append(ln[1:])
    return out


def secret_findings(text: str) -> list[str]:
    """Secret-shaped strings among the ADDED lines. The repo is public: a hit blocks the push, the operator is told which line."""
    bad = []
    for n, ln in enumerate(added_lines(text), 1):
        if _TOKENISH.search(ln) or tools.redact(ln) != ln:
            bad.append(f"added line {n} looks like a secret")
    return bad


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return s or "change"


def branch_for(cr: dict) -> str:
    return f"agent/{cr['class']}/{cr['id']}-{slugify(cr['title'])}"


def pr_body(cr: dict, summary: str, tests: dict | None, files: list[dict]) -> str:
    rows = "\n".join(f"- `{f['filename']}` (+{f['additions']} -{f['deletions']})" for f in files)
    t = "\n".join(f"- {k}: {v}" for k, v in (tests or {}).items()) or "- repo CI (links, yamllint, gitleaks) runs on this PR"
    body = (f"Agent-authored draft for change request **#{cr['id']}** (class `{cr['class']}`, source `{cr['source']}`"
            f"{', ref ' + cr['source_ref'] if cr.get('source_ref') else ''}), approved by Discord user `{cr.get('decided_by')}`.\n\n"
            f"## Summary\n{summary or '(the session wrote no summary)'}\n\n## Files\n{rows}\n\n## Checks\n{t}\n\n"
            f"## Rollback\nRevert this PR; it only touches the files above.\n\n"
            f"---\n*Written by an AI session with read-only access to live state; the operator reviews and merges. "
            f"The branch `{branch_for(cr)}` was created by the dispatcher, which applied the session's patch after the scope rules and a secret scan.*")
    return tools.redact(body)[:60000]


# ---- the world, behind small clients so tests can fake them -----------------------------------------------------
def http(method: str, url: str, headers: dict, body=None, timeout: float = 30.0) -> tuple[int, object]:
    req = urllib.request.Request(url, method=method, headers={"Accept": "application/json", **headers},
                                 data=None if body is None else json.dumps(body).encode())
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except OSError as e:
        return 0, {"error": type(e).__name__}


class ToolbeltClient:
    def __init__(self, base: str, token: str):
        self.base, self.h = base.rstrip("/"), {"Authorization": "Bearer " + token}

    def claim(self) -> dict:
        st, b = http("POST", self.base + "/change-requests/claim", self.h, {})
        return b if st == 200 else {"change_request": None, "why": f"http-{st}"}

    def report(self, cid: int, **fields) -> tuple[int, object]:
        return http("POST", f"{self.base}/change-requests/{cid}/report", self.h, fields)

    def open_prs(self) -> list[dict]:
        st, b = http("GET", self.base + "/change-requests?state=pr-open", self.h)
        return b.get("change_requests", []) if st == 200 and isinstance(b, dict) else []


class GitHubClient:
    def __init__(self, repo: str, token: str):
        self.repo, self.h = repo, {"Authorization": "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28"}

    def identity(self) -> dict:
        st, u = http("GET", "https://api.github.com/user", self.h)
        st2, r = http("GET", f"https://api.github.com/repos/{self.repo}", self.h)
        perms = (r or {}).get("permissions") or {}
        return {"login": (u or {}).get("login"), "ok": st == 200 and st2 == 200, "admin": bool(perms.get("admin")),
                "maintain": bool(perms.get("maintain")), "push": bool(perms.get("push"))}

    def create_pr(self, head: str, base: str, title: str, body: str) -> tuple[int, dict]:
        return http("POST", f"https://api.github.com/repos/{self.repo}/pulls", self.h,
                    {"title": title, "head": head, "base": base, "body": body, "maintainer_can_modify": False})

    def label(self, number: int, label: str) -> None:
        http("POST", f"https://api.github.com/repos/{self.repo}/issues/{number}/labels", self.h, {"labels": [label]})

    def pr_state(self, number: int) -> str:
        st, p = http("GET", f"https://api.github.com/repos/{self.repo}/pulls/{number}", self.h)
        if st != 200 or not isinstance(p, dict):
            return "unknown"
        return "merged" if p.get("merged") else ("open" if p.get("state") == "open" else "closed")


class Git:
    """git as a subprocess in a directory THIS process created. The token reaches git only through GIT_ASKPASS (an env var of
    that one child), never in an argv, a URL or a file."""

    def __init__(self, token: str = "", askpass: str = ""):
        self.token, self.askpass = token, askpass

    def run(self, args: list[str], cwd: str | None = None, push: bool = False, timeout: int = 180) -> str:
        env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_AUTHOR_NAME": "aiops-author", "GIT_AUTHOR_EMAIL": "aiops-author@users.noreply.github.com",
               "GIT_COMMITTER_NAME": "aiops-author", "GIT_COMMITTER_EMAIL": "aiops-author@users.noreply.github.com"}
        if push:
            env.update(GIT_ASKPASS=self.askpass, AIOPS_AUTHOR_PAT=self.token)
        p = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args], cwd=cwd, env=env,
                           capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {tools.redact(p.stderr)[-300:]}")
        return p.stdout


class Config:
    def __init__(self, **kw):
        self.repo = kw.get("repo", "XIIISins/homelab")
        self.repo_url = kw.get("repo_url", f"https://github.com/{self.repo}.git")
        self.work = Path(kw.get("work", "/var/lib/aiops-author"))
        self.classes = kw["classes"]
        self.max_parallel = int(kw.get("max_parallel", 2))
        self.session_timeout = int(kw.get("session_timeout", 1800))
        self.allow_admin_token = bool(kw.get("allow_admin_token", False))
        self.dry_run = bool(kw.get("dry_run", False))
        self.tools_url = kw.get("tools_url", "")
        self.tools_cli = kw.get("tools_cli", "")
        self.agent_file = kw.get("agent_file", "")
        self.model = kw.get("model", "")
        self.max_budget_usd = kw.get("max_budget_usd", 5)
        self.draft_unit = kw.get("draft_unit", "aiops-draft")


class Dispatcher:
    def __init__(self, cfg: Config, tb: ToolbeltClient, gh: GitHubClient, git: Git, secrets: dict, start_session=None):
        self.cfg, self.tb, self.gh, self.git, self.secrets = cfg, tb, gh, git, secrets
        self.start_session = start_session or self._systemd_session
        self._ident: tuple[float, dict] | None = None

    # -- the safety guard ----------------------------------------------------------------------------------------
    def identity_ok(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if self._ident is None or now - self._ident[0] > 600:
            self._ident = (now, self.gh.identity())
            audit("identity", **{k: v for k, v in self._ident[1].items()})
        i = self._ident[1]
        if not i["ok"]:
            audit("identity_refused", why="cannot read the token's account or the repo")
            return False
        if not i["push"]:
            audit("identity_refused", why="the token cannot push to the repo")
            return False
        if (i["admin"] or i["maintain"]) and not self.cfg.allow_admin_token:
            audit("identity_refused", why="the token's account has admin/maintain on the repo, and the main ruleset lets admins bypass it; "
                  "use a write-only machine user (or --allow-admin-token on purpose)", login=i["login"])
            return False
        return True

    # -- one change request --------------------------------------------------------------------------------------
    def _systemd_session(self, cid: int, poll: float = 3.0) -> int:
        """Ask the root launcher (aiops-draft-launch, triggered by a systemd path unit on these markers) to start
        aiops-draft@<id>.service, then wait for the session's result file. No sudo, no setuid: this process stays fully
        sandboxed and the launcher only ever acts on a numeric directory name it validated itself."""
        job = self.cfg.work / "jobs" / str(cid)
        result = job / "out" / "result.json"
        (job / "ready").write_text("")
        deadline = time.time() + self.cfg.session_timeout + 120
        while time.time() < deadline:
            if result.exists():
                return 0
            time.sleep(poll)
        (job / "stop").write_text("")  # the launcher stops the unit
        time.sleep(poll * 2)
        return 124

    def prepare(self, cr: dict) -> Path:
        job = self.cfg.work / "jobs" / str(cr["id"])
        shutil.rmtree(job, ignore_errors=True)
        (job / "out").mkdir(parents=True)
        for d in (job, job / "out"):
            os.chmod(d, 0o2770)
        spec = {"id": cr["id"], "class": cr["class"], "title": cr["title"], "body": cr["body"], "allowed_paths": cr["allowed_paths"],
                "limits": self.cfg.classes["limits"], "repo_url": self.cfg.repo_url, "base": "main", "tools_url": self.cfg.tools_url,
                "tools_cli": self.cfg.tools_cli, "agent_file": self.cfg.agent_file, "model": self.cfg.model,
                "max_budget_usd": self.cfg.max_budget_usd, "timeout": self.cfg.session_timeout - 120}
        (job / "spec.json").write_text(json.dumps(spec))
        env = job / "env"
        fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
        with os.fdopen(fd, "w") as fh:
            fh.write(f'ANTHROPIC_API_KEY="{self.secrets["anthropic"]}"\nAIOPS_TOOLS_TOKEN="{self.secrets["tools"]}"\n')
        os.chmod(env, 0o640)
        return job

    def process(self, cr: dict) -> dict:
        """Run one claimed request to a reported outcome. Never raises: whatever happens is reported to the Toolbelt."""
        cid = cr["id"]
        job = None
        try:
            job = self.prepare(cr)
            rc = self.start_session(cid)
            env = job / "env"
            env.unlink(missing_ok=True)  # the key is only needed while the session runs
            res = json.loads((job / "out" / "result.json").read_text()) if (job / "out" / "result.json").exists() else {}
            if not res.get("ok"):
                return self._fail(cid, res.get("error") or f"the session did not finish cleanly (exit {rc})", res)
            patch = (job / "out" / "change.patch").read_text()
            summary = tools.redact((job / "out" / "summary.md").read_text()[:2000]) if (job / "out" / "summary.md").exists() else ""
            files = parse_patch(patch)
            branch = branch_for(cr)
            bad = scope.check(self.cfg.classes, branch, files, declared_allow=cr["allowed_paths"]) + secret_findings(patch)
            if bad:
                audit("patch_refused", cr=cid, problems=bad[:10])
                return self._fail(cid, "refused before pushing: " + "; ".join(bad[:6]), res)
            if self.cfg.dry_run:
                audit("dry_run", cr=cid, files=[f["filename"] for f in files])
                return self._fail(cid, "dry run: the patch passed every check and was not pushed", res)
            if not self.identity_ok():
                return self._fail(cid, "the GitHub identity check refuses pushing (see the dispatcher audit log)", res)
            return self._publish(cr, branch, patch, files, summary, res)
        except PatchError as e:
            return self._fail(cid, f"the patch is not acceptable: {e}", {})
        except Exception as e:  # noqa: BLE001 - always report; the audit log has the type
            audit("error", cr=cid, error=type(e).__name__, detail=tools.redact(str(e))[:200])
            return self._fail(cid, f"dispatcher error: {type(e).__name__}", {})
        finally:
            if job is not None:
                (job / "env").unlink(missing_ok=True)
                shutil.rmtree(job / "repo", ignore_errors=True)
                shutil.rmtree(job / "home", ignore_errors=True)

    def _fail(self, cid: int, error: str, res: dict) -> dict:
        self.tb.report(cid, state="failed", error=error, summary=f"turns={res.get('turns')} cost_usd={res.get('cost_usd')}")
        audit("failed", cr=cid, error=error[:200])
        return {"state": "failed", "error": error}

    def _publish(self, cr: dict, branch: str, patch: str, files: list[dict], summary: str, res: dict) -> dict:
        cid = cr["id"]
        with tempfile.TemporaryDirectory(prefix="push-", dir=str(self.cfg.work)) as d:
            repo = str(Path(d) / "repo")
            self.git.run(["clone", "--quiet", "--depth", "1", "--branch", "main", self.cfg.repo_url, repo])
            self.git.run(["checkout", "-q", "-b", branch], cwd=repo)
            pf = Path(d) / "change.patch"
            pf.write_text(patch)
            self.git.run(["apply", "--index", "--whitespace=nowarn", str(pf)], cwd=repo)
            staged = [ln.split("\t")[-1] for ln in self.git.run(["diff", "--cached", "--numstat"], cwd=repo).splitlines()]
            if sorted(staged) != sorted(f["filename"] for f in files):
                return self._fail(cid, "the applied change does not match the parsed patch", res)
            kind = "docs" if cr["class"] == "docs" else cr["class"]
            title = f"{kind}: {cr['title']}"[:100]
            self.git.run(["commit", "-q", "-m", f"{title}\n\nChange request #{cid} ({cr['class']}); agent-authored, operator-reviewed."], cwd=repo)
            self.git.run(["push", "origin", f"HEAD:refs/heads/{branch}"], cwd=repo, push=True)
        st, pr = self.gh.create_pr(branch, "main", title, pr_body(cr, summary, {"scope rules": "pass", "secret scan": "pass"}, files))
        if st != 201 or not isinstance(pr, dict) or not pr.get("html_url"):
            return self._fail(cid, f"the branch was pushed but GitHub refused the pull request (HTTP {st})", res)
        self.gh.label(pr["number"], "agent-authored")
        self.tb.report(cid, state="pr-open", pr_url=pr["html_url"], branch=branch, summary=summary,
                       tests={"scope rules": "pass", "secret scan": "pass", "turns": res.get("turns"), "cost_usd": res.get("cost_usd")})
        audit("pr_opened", cr=cid, pr=pr["number"], branch=branch)
        return {"state": "pr-open", "pr": pr["html_url"]}

    # -- reconciling and the loop --------------------------------------------------------------------------------
    def reconcile(self) -> None:
        for cr in self.tb.open_prs():
            m = re.search(r"/pull/(\d+)$", cr.get("pr_url") or "")
            if not m:
                continue
            state = self.gh.pr_state(int(m.group(1)))
            if state in ("merged", "closed"):
                self.tb.report(cr["id"], state=state)
                audit("pr_" + state, cr=cr["id"])

    def tick(self, running: set) -> None:
        if not self.identity_ok():
            return
        while len(running) < self.cfg.max_parallel:
            got = self.tb.claim()
            cr = got.get("change_request")
            if not cr:
                return
            running.add(cr["id"])

            def work(c=cr):
                try:
                    self.process(c)
                finally:
                    running.discard(c["id"])
            threading.Thread(target=work, daemon=True, name=f"draft-{cr['id']}").start()

    def run_forever(self, poll: float = 30.0) -> None:
        running: set = set()
        last_reconcile = 0.0
        while True:
            try:
                if time.time() - last_reconcile > 300:
                    self.reconcile()
                    last_reconcile = time.time()
                self.tick(running)
            except Exception as e:  # noqa: BLE001
                audit("error", where="loop", error=type(e).__name__)
            time.sleep(poll)


def read_secret(path: str) -> str:
    return Path(path).read_text().strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--toolbelt", required=True)
    ap.add_argument("--author-token-file", required=True)
    ap.add_argument("--tools-token-file", required=True)
    ap.add_argument("--pat-file", required=True)
    ap.add_argument("--anthropic-file", required=True)
    ap.add_argument("--askpass", required=True)
    ap.add_argument("--classes", required=True)
    ap.add_argument("--work", default="/var/lib/aiops-author")
    ap.add_argument("--repo", default="XIIISins/homelab")
    ap.add_argument("--tools-cli", required=True)
    ap.add_argument("--agent-file", required=True)
    ap.add_argument("--model", default="")
    ap.add_argument("--max-budget-usd", type=float, default=5)
    ap.add_argument("--max-parallel", type=int, default=2)
    ap.add_argument("--allow-admin-token", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cfg = Config(repo=a.repo, work=a.work, classes=scope.load_classes(Path(a.classes).read_text()), tools_url=a.toolbelt, tools_cli=a.tools_cli,
                 agent_file=a.agent_file, model=a.model, max_budget_usd=a.max_budget_usd, max_parallel=a.max_parallel,
                 allow_admin_token=a.allow_admin_token, dry_run=a.dry_run)
    pat = read_secret(a.pat_file)
    d = Dispatcher(cfg, ToolbeltClient(a.toolbelt, read_secret(a.author_token_file)), GitHubClient(a.repo, pat), Git(pat, a.askpass),
                   {"anthropic": read_secret(a.anthropic_file), "tools": read_secret(a.tools_token_file)})
    audit("start", repo=a.repo, dry_run=a.dry_run, allow_admin_token=a.allow_admin_token, max_parallel=a.max_parallel)
    d.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
