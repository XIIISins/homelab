#!/usr/bin/env python3
"""Phase 10h2 scope check: may this agent-authored change set be pushed / merged?

One implementation, two callers: the dispatcher on Frigg (refuses to push a branch that fails) and the CI job
`agent-scope` (runs this file from the PR's BASE commit against the PR's file list, so a PR cannot weaken it).
Pure standard library plus PyYAML. Exit 0 = ok or not an agent PR, 1 = violations (printed), 2 = bad input.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import yaml

_BRANCH = re.compile(r"^agent/([a-z0-9][a-z0-9-]*)/(\d+)-[a-z0-9][a-z0-9-]*$")


def _glob_re(pattern: str) -> re.Pattern:
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(path: str, patterns: list[str]) -> bool:
    return any(_glob_re(p).match(path) for p in patterns)


def load_classes(text: str) -> dict:
    data = yaml.safe_load(text) or {}
    if data.get("version") != 1 or not isinstance(data.get("classes"), dict):
        raise ValueError("author-classes.yml: version 1 with a `classes` map is required")
    data.setdefault("deny", [])
    data.setdefault("limits", {})
    data.setdefault("branch_prefix", "agent/")
    data.setdefault("agent_logins", [])
    return data


def is_agent_pr(cfg: dict, branch: str, author: str) -> bool:
    return branch.startswith(cfg["branch_prefix"]) or (bool(author) and author in cfg["agent_logins"])


def branch_class(branch: str) -> tuple[str, int] | None:
    m = _BRANCH.match(branch)
    return (m.group(1), int(m.group(2))) if m else None


def check(cfg: dict, branch: str, files: list[dict], declared_allow: list[str] | None = None) -> list[str]:
    """files: GitHub `pulls/N/files` shape ({filename, status, previous_filename?, additions, deletions}).
    declared_allow: an optional narrower per-request path list (the dispatcher passes the change request's)."""
    bad: list[str] = []
    parsed = branch_class(branch)
    if not parsed:
        return [f"branch `{branch}` is not agent/<class>/<change-request-id>-<slug>"]
    cls_name, _cr = parsed
    cls = cfg["classes"].get(cls_name)
    if not cls:
        return [f"unknown class `{cls_name}`"]
    if not cls.get("enabled"):
        return [f"class `{cls_name}` is not enabled"]
    lim = cfg["limits"]
    if len(files) > int(lim.get("max_files", 12)):
        bad.append(f"{len(files)} files changed, limit {lim.get('max_files', 12)}")
    lines = sum(int(f.get("additions", 0)) + int(f.get("deletions", 0)) for f in files)
    if lines > int(lim.get("max_changed_lines", 600)):
        bad.append(f"{lines} changed lines, limit {lim.get('max_changed_lines', 600)}")
    for f in files:
        names = [f["filename"]] + ([f["previous_filename"]] if f.get("previous_filename") else [])
        if f.get("status") == "removed" and not lim.get("allow_deletes", False):
            bad.append(f"{f['filename']}: deleting files is not allowed")
        for n in names:
            segs = n.split("/")
            if n.startswith("/") or "\\" in n or ".." in segs or "." in segs or ".git" in segs or "" in segs:
                bad.append(f"{n}: not a plain repo-relative path")
            elif matches(n, cfg["deny"]):
                bad.append(f"{n}: forbidden for agents")
            elif not matches(n, cls["allow"]):
                bad.append(f"{n}: outside class `{cls_name}`")
            elif declared_allow is not None and not matches(n, declared_allow):
                bad.append(f"{n}: not in the change request's allowed paths")
    return sorted(set(bad))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--classes", required=True)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--author", default="")
    ap.add_argument("--files-json", required=True, help="JSON list in the GitHub pulls/N/files shape")
    a = ap.parse_args(argv)
    try:
        cfg = load_classes(open(a.classes).read())
        files = json.load(open(a.files_json))
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f"agent-scope: bad input: {e}")
        return 2
    if not is_agent_pr(cfg, a.branch, a.author):
        print("agent-scope: not an agent-authored PR, nothing to check")
        return 0
    bad = check(cfg, a.branch, files)
    if bad:
        print("agent-scope: this agent-authored PR breaks the scope rules:")
        for b in bad:
            print(f"  - {b}")
        return 1
    print(f"agent-scope: ok ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
