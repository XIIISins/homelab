"""Prove an agent-authored PR on a canary (Phase 10h2): which PRs may be tested, and what the test's summary says.

The test runs the PR branch's own role code on ONE canary through Semaphore (a per-task `git_branch`). That makes the PR's
code, including any guard playbook it ships, untrusted, so nothing here relies on the branch: `inspect_pr` reads the PR from
GitHub (read-only, unauthenticated, the repo is public) and refuses anything but role content for the roles a canary exercises,
with no deletions or renames and no controller-side or network constructs in the added lines. The Toolbelt calls it before it
proposes a test and again before it runs one, and a refused PR simply carries "not tested: <reason>" in its description.

Pure functions over a `fetch(url) -> (status, json)` callable, so tests need no network.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

REPO = "XIIISins/homelab"
BRANCH = re.compile(r"^agent/(?P<cls>[a-z0-9][a-z0-9-]*)/(?P<id>\d+)-[a-z0-9][a-z0-9-]*$")
SHA = re.compile(r"^[0-9a-f]{40}$")
CANARY = re.compile(r"^canary-[0-9]+$")
_ROLE_PATH = re.compile(r"^ansible/roles/(?P<role>[a-z0-9-]+)/(?:tasks|defaults|handlers|templates|files|vars|meta)/[A-Za-z0-9_./-]+$")
# Constructs a role change has no business adding when the point is to run it on a canary: anything that reaches another host,
# the controller's environment or the network from the controller, or pulls code in. Added lines only; the PR can still be reviewed
# and merged by hand, it just does not get the automatic test.
RISKY = re.compile(
    r"delegate_to|local_action|ansible_connection|\bconnection\s*:|add_host|hashi_vault|\blookup\s*\(|\bquery\s*\(|include_vars|"
    r"ansible_user|ansible_ssh|become_user|\bansible\.builtin\.uri\b|\buri\s*:|\bget_url\b|\bansible\.builtin\.git\b|\bgit\s*:|unarchive|"
    r"/dev/tcp|\bcurl\b|\bwget\b|\bnc\b|\bnetcat\b|\bbash\s+-c\b|\bbase64\b|\beval\b|vault_|\bpip\s*:",
    re.I)
_MAX_FILES, _MAX_COMMITS, _MAX_PATCH = 20, 3, 20000


def gh_fetch(url: str, timeout: float = 8.0) -> tuple[int, object]:
    """Unauthenticated GET against api.github.com (60 requests/hour/address is plenty: two calls per check)."""
    if not url.startswith("https://api.github.com/"):
        return 0, {"error": "not the GitHub API"}
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "aiops-toolbelt-pr-test",
                                               "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (OSError, ValueError) as e:
        return 0, {"error": type(e).__name__}


def make_fetch(base: str | None = None):
    """The fetch the Toolbelt uses. The Toolbelt's unit cannot reach the internet, so in production `base` is the loopback GitHub
    read proxy (github_read_proxy.py) which serves exactly the two paths `inspect_pr` needs; without `base` it talks to GitHub."""
    if not base:
        return gh_fetch
    root = base.rstrip("/")

    def fetch(url: str, timeout: float = 8.0) -> tuple[int, object]:
        prefix = "https://api.github.com"
        if not url.startswith(prefix + "/"):
            return 0, {"error": "not the GitHub API"}
        try:
            req = urllib.request.Request(root + url[len(prefix):], headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
        except (OSError, ValueError) as e:
            return 0, {"error": type(e).__name__}
    return fetch


def _transient(status: int) -> bool:
    """A GitHub answer worth asking again later (outage, rate limit, the proxy not up yet), unlike a verdict about the PR."""
    return status == 0 or status == 429 or status >= 500 or status == 403


def added_lines(patch: str) -> list[str]:
    return [ln[1:] for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


def inspect_pr(fetch, branch: str, classes: list[str], roles: list[str], repo: str = REPO) -> dict:
    """What a canary test needs to know about the PR at its current head, or why it is not eligible.
    {ok: True, sha, role_tag, files, additions, deletions} or {ok: False, reason}."""
    m = BRANCH.match(branch or "")
    if not m or m.group("cls") not in classes:
        return {"ok": False, "reason": f"branch is not an agent PR of class {'/'.join(classes)}"}
    st, ref = fetch(f"https://api.github.com/repos/{repo}/git/ref/heads/{branch}")
    sha = (ref or {}).get("object", {}).get("sha") if st == 200 and isinstance(ref, dict) else None
    if not sha or not SHA.match(sha):
        return {"ok": False, "reason": f"could not read the branch head from GitHub (HTTP {st})", "transient": _transient(st)}
    st, cmp = fetch(f"https://api.github.com/repos/{repo}/compare/main...{sha}")
    if st != 200 or not isinstance(cmp, dict):
        return {"ok": False, "reason": f"could not read the PR's changes from GitHub (HTTP {st})", "transient": _transient(st)}
    files = cmp.get("files") or []
    if cmp.get("status") not in ("ahead", "diverged") or not 1 <= int(cmp.get("ahead_by") or 0) <= _MAX_COMMITS:
        return {"ok": False, "reason": f"the branch is not 1-{_MAX_COMMITS} commits ahead of main"}
    if not 1 <= len(files) <= _MAX_FILES or int(cmp.get("total_commits") or 0) > _MAX_COMMITS:
        return {"ok": False, "reason": f"the PR changes {len(files)} files (1-{_MAX_FILES} can be tested)"}
    seen: set[str] = set()
    add = dele = 0
    for f in files:
        name = str(f.get("filename", ""))
        pm = _ROLE_PATH.match(name)
        if f.get("status") not in ("added", "modified") or f.get("previous_filename"):
            return {"ok": False, "reason": f"{name}: only added or modified files are tested (no deletes or renames)"}
        if not pm or ".." in name.split("/"):
            return {"ok": False, "reason": f"{name}: not role content (a canary test covers ansible/roles/<role>/ only)"}
        if pm.group("role") not in roles:
            return {"ok": False, "reason": f"{name}: no canary exercises the {pm.group('role')} role"}
        patch = f.get("patch")
        if not isinstance(patch, str) or len(patch) > _MAX_PATCH:
            return {"ok": False, "reason": f"{name}: the diff is missing or too large to scan"}
        bad = next((ln for ln in added_lines(patch) if RISKY.search(ln) or len(ln) > 400), None)
        if bad is not None:
            return {"ok": False, "reason": f"{name}: an added line has a construct that is not auto-tested ({RISKY.search(bad).group(0).strip()[:30] if RISKY.search(bad) else 'long line'})"}
        seen.add(pm.group("role"))
        add += int(f.get("additions") or 0)
        dele += int(f.get("deletions") or 0)
    if len(seen) != 1:
        return {"ok": False, "reason": f"the PR touches {len(seen)} roles ({', '.join(sorted(seen))}); one role per test"}
    return {"ok": True, "sha": sha, "role_tag": next(iter(seen)), "files": len(files), "additions": add, "deletions": dele}


def check_params(fetch, params: dict, guard: dict, repo: str = REPO) -> list[str]:
    """Problems with a pr-test action's parameters against the PR as it is NOW (empty = fine). Used before a test is proposed
    and again before it runs: the approved sha must still be the branch head and the role must be the one the PR touches."""
    scope = guard.get("pr_scope") or {}
    if not CANARY.match(str(params.get("target_host", ""))):
        return [f"{params.get('target_host')!r} is not a canary"]
    got = inspect_pr(fetch, str(params.get("pr_branch", "")), list(scope.get("classes", [])), list(scope.get("roles", [])), repo)
    if not got["ok"]:
        return [got["reason"]]
    problems = []
    if got["sha"] != params.get("pr_sha"):
        problems.append(f"the PR head moved (approved {str(params.get('pr_sha'))[:8]}, now {got['sha'][:8]}): propose the test again")
    if got["role_tag"] != params.get("role_tag"):
        problems.append(f"the PR touches the {got['role_tag']} role, not {params.get('role_tag')!r}")
    return problems


# ---- 10h: a `k8s/` PR, proven on a burst cluster ---------------------------------------------------------------------------

_APP_PATH = re.compile(r"^k8s/asgard/apps/(?P<app>[a-z0-9][a-z0-9-]*)/[A-Za-z0-9_./-]+\.ya?ml$")
# Things a manifest for an ordinary app has no business adding: they reach the node, not the app. The burst cluster is disposable, but the point
# of the test is the app, and the operator should look at any of these by hand.
K8S_RISKY = re.compile(r"hostPath\b|\bprivileged\s*:\s*true|hostNetwork\s*:\s*true|hostPID\s*:\s*true|hostIPC\s*:\s*true|\ballowPrivilegeEscalation\s*:\s*true|"
                       r"\bkind\s*:\s*(ClusterRole|ClusterRoleBinding|MutatingWebhookConfiguration|ValidatingWebhookConfiguration|CustomResourceDefinition)\b")


def inspect_k8s_pr(fetch, branch: str, classes: list[str], repo: str = REPO) -> dict:
    """What a burst test needs to know about a k8s PR at its current head, or why it is not eligible.
    {ok: True, sha, component, files, additions, deletions} or {ok: False, reason}: yaml files under exactly ONE k8s/asgard/apps/<app>/, added or
    modified only, at most `_MAX_FILES`, 1-3 commits ahead of main, nothing from K8S_RISKY in an added line."""
    m = BRANCH.match(branch or "")
    if not m or m.group("cls") not in classes:
        return {"ok": False, "reason": f"branch is not an agent PR of class {'/'.join(classes)}"}
    st, ref = fetch(f"https://api.github.com/repos/{repo}/git/ref/heads/{branch}")
    sha = (ref or {}).get("object", {}).get("sha") if st == 200 and isinstance(ref, dict) else None
    if not sha or not SHA.match(sha):
        return {"ok": False, "reason": f"could not read the branch head from GitHub (HTTP {st})", "transient": _transient(st)}
    st, cmp = fetch(f"https://api.github.com/repos/{repo}/compare/main...{sha}")
    if st != 200 or not isinstance(cmp, dict):
        return {"ok": False, "reason": f"could not read the PR's changes from GitHub (HTTP {st})", "transient": _transient(st)}
    files = cmp.get("files") or []
    if cmp.get("status") not in ("ahead", "diverged") or not 1 <= int(cmp.get("ahead_by") or 0) <= _MAX_COMMITS:
        return {"ok": False, "reason": f"the branch is not 1-{_MAX_COMMITS} commits ahead of main"}
    if not 1 <= len(files) <= _MAX_FILES or int(cmp.get("total_commits") or 0) > _MAX_COMMITS:
        return {"ok": False, "reason": f"the PR changes {len(files)} files (1-{_MAX_FILES} can be tested)"}
    apps: set[str] = set()
    add = dele = 0
    for f in files:
        name = str(f.get("filename", ""))
        pm = _APP_PATH.match(name)
        if f.get("status") not in ("added", "modified") or f.get("previous_filename"):
            return {"ok": False, "reason": f"{name}: only added or modified files are tested (no deletes or renames)"}
        if not pm or ".." in name.split("/"):
            return {"ok": False, "reason": f"{name}: not an app manifest (a burst test covers k8s/asgard/apps/<app>/*.yaml only)"}
        patch = f.get("patch")
        if not isinstance(patch, str) or len(patch) > _MAX_PATCH:
            return {"ok": False, "reason": f"{name}: the diff is missing or too large to scan"}
        bad = next((ln for ln in added_lines(patch) if K8S_RISKY.search(ln) or len(ln) > 600), None)
        if bad is not None:
            hit = K8S_RISKY.search(bad)
            return {"ok": False, "reason": f"{name}: an added line has a construct that is not auto-tested ({hit.group(0).strip()[:30] if hit else 'long line'})"}
        apps.add(pm.group("app"))
        add += int(f.get("additions") or 0)
        dele += int(f.get("deletions") or 0)
    if len(apps) != 1:
        return {"ok": False, "reason": f"the PR touches {len(apps)} apps ({', '.join(sorted(apps))}); one app per test"}
    return {"ok": True, "sha": sha, "component": next(iter(apps)), "files": len(files), "additions": add, "deletions": dele}


def check_k8s_params(fetch, params: dict, guard: dict, repo: str = REPO) -> list[str]:
    """Problems with a pr-burst-test action's parameters against the PR as it is NOW (empty = fine): the approved sha must still be the head and the
    app must be the one the PR touches."""
    scope = guard.get("pr_scope") or {}
    got = inspect_k8s_pr(fetch, str(params.get("pr_branch", "")), list(scope.get("classes", [])), repo)
    if not got["ok"]:
        return [got["reason"]]
    problems = []
    if got["sha"] != params.get("pr_sha"):
        problems.append(f"the PR head moved (approved {str(params.get('pr_sha'))[:8]}, now {got['sha'][:8]}): propose the test again")
    if got["component"] != params.get("component"):
        problems.append(f"the PR touches the {got['component']} app, not {params.get('component')!r}")
    return problems


# ---- what the PR description says -------------------------------------------------------------------------------------

def _changed(step: dict | None):
    res = (step or {}).get("result") or {}
    return res.get("changed")


def summarize(proposal: dict | None, ineligible: str | None = None, transient: bool = False) -> dict:
    """The canary-test status of one PR, from its (optional) proposal view: {status, ...}. Statuses: not-tested (with a reason),
    proposed (waiting for the operator), running, passed, failed, expired, rejected, cancelled."""
    if proposal is None:
        return {"status": "not-tested", "reason": ineligible or "no test was proposed", "retry": bool(transient)}
    st = proposal["state"]
    p = proposal.get("params") or {}
    if proposal.get("action_id") == "pr-burst-test":   # 10h: a k8s PR proven on a burst cluster; the runner's own markdown is the evidence
        res = next((s.get("result") for s in ((proposal.get("result") or {}).get("steps") or []) if isinstance(s, dict) and s.get("step") == "test"), None) or {}
        status = {"pending": "proposed", "approved": "running", "running": "running", "succeeded": "passed"}.get(
            st, st if st in ("expired", "rejected", "cancelled") else "failed")
        burst = {"kind": "burst", "status": status, "proposal": proposal["id"], "component": p.get("component"), "sha": str(p.get("pr_sha", ""))[:8]}
        if res.get("summary_md"):
            burst["markdown"] = str(res["summary_md"])[:2800]
        if res.get("seconds"):
            burst["seconds"] = int(res["seconds"])
        if status == "failed" and not res.get("summary_md"):
            burst["reason"] = str((proposal.get("result") or {}).get("why", st))[:200]
        return burst
    out = {"status": {"pending": "proposed", "approved": "running", "running": "running", "succeeded": "passed"}.get(
        st, st if st in ("expired", "rejected", "cancelled") else "failed"),
        "proposal": proposal["id"], "canary": p.get("target_host"), "role": p.get("role_tag"), "sha": str(p.get("pr_sha", ""))[:8]}
    steps = (proposal.get("result") or {}).get("steps") or []
    by = {s.get("step"): s for s in steps if isinstance(s, dict)}
    before, apply_, after = by.get("prior:pr-test-check"), by.get("action"), by.get("verify:pr-test-check")
    if before:
        out["dry_run_changes"] = _changed(before)
    if apply_:
        out["apply"] = "ok" if (apply_.get("result") or {}).get("ok") else "failed"
    if after:
        out["second_run_changed"] = _changed(after)
    if out["status"] == "failed":
        out["reason"] = str((proposal.get("result") or {}).get("why", st))[:200]
    return out


def render_burst(s: dict) -> str:
    st = s["status"]
    head = {"passed": "**Passed** on a burst cluster", "failed": "**Failed** on a burst cluster", "running": "Running on a burst cluster",
            "proposed": "Proposed, **waiting for the operator's approval**", "expired": "Not run: the approval window expired",
            "rejected": "Not run: the operator rejected the test", "cancelled": "Not run: cancelled", "not-tested": "**Not tested**"}.get(st, st)
    if st == "failed" and "no cluster was built" in (s.get("markdown") or ""):   # the offline gate failed first: say so in the headline too
        head = "**Failed** at the offline gate, before a cluster was built"
    lines = [head + (f": {s['reason']}" if s.get("reason") and st in ("not-tested", "failed") else "")]
    if s.get("component"):
        lines.append(f"- App `{s['component']}`, commit `{s.get('sha')}`" + (f", {s['seconds']} s" if s.get("seconds") else ""))
    if s.get("markdown"):
        lines += ["", s["markdown"].rstrip()]
    if s.get("proposal"):
        lines += ["", f"- Evidence: approval proposal #{s['proposal']}; the full run output is in the private Discord thread, not here"]
    return "\n".join(lines)


def title_of(summary: dict | None) -> str:
    """The PR description section heading for this summary."""
    return "Burst-cluster test" if (summary or {}).get("kind") == "burst" else "Canary test"


def render(summary: dict | None) -> str:
    """Markdown for the PR description's test section (public repo: counts and ids only, never run output)."""
    s = summary or {"status": "not-tested", "reason": "not evaluated yet"}
    if s.get("kind") == "burst":
        return render_burst(s)
    st = s["status"]
    head = {"passed": "**Passed** on a canary", "failed": "**Failed** on a canary", "running": "Running on a canary",
            "proposed": "Proposed, **waiting for the operator's approval**", "expired": "Not run: the approval window expired",
            "rejected": "Not run: the operator rejected the test", "cancelled": "Not run: cancelled",
            "not-tested": "**Not tested**"}.get(st, st)
    lines = [head + (f": {s['reason']}" if s.get("reason") and st in ("not-tested", "failed") else "")]
    if s.get("retry") and st == "not-tested":
        lines.append("- This looks temporary; the author will ask again shortly.")
    if s.get("canary"):
        lines.append(f"- Where: `{s['canary']}` (a disposable canary, alerts capped at the info tier), role `{s.get('role')}`, commit `{s.get('sha')}`")
    if "dry_run_changes" in s:
        lines.append(f"- Dry run of the PR's code: {s['dry_run_changes']} task(s) would change")
    if "apply" in s:
        lines.append(f"- Real run: {s['apply']}")
    if "second_run_changed" in s:
        lines.append(f"- Second dry run: {s['second_run_changed']} task(s) would change" + (" (idempotent)" if s["second_run_changed"] == 0 else " (NOT idempotent)"))
    if s.get("proposal"):
        lines.append(f"- Evidence: approval proposal #{s['proposal']}; the full run output is in the private Discord thread, not here")
    return "\n".join(lines)
