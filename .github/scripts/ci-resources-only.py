#!/usr/bin/env python3
"""A rightsizing PR may change container resources and nothing else (Phase 10i4).

    ci-resources-only.py [--base REV] [--head REV|INDEX] [--root DIR]

Compares every changed file between BASE and HEAD as YAML and fails unless each difference is a container `resources` value:
`requests`/`limits` x `cpu`/`memory` (a whole `resources`, `requests` or `limits` block may be added: a preset replaced by an explicit
block), or a `resourcesPreset` that becomes "none" while an explicit `resources:` block sits beside it. It also fails on:

  * any changed file that is not YAML, a new or a deleted file, a changed document count, a changed list length;
  * a removed key (a rightsizing PR never deletes anything), an added or changed CPU LIMIT (throttling on 2-vCPU workers hurts more than it protects);
  * a memory limit below its request, a CPU request below 10m or a memory request or limit below 32 MiB (the floors in aiops/rightsizing.yml, read from BASE);
  * a quantity that is not a plain Kubernetes quantity.

Two callers, one script: the PR author's dispatcher on Frigg runs it in its patched clone before it pushes (no arguments: BASE = HEAD, HEAD = the
staged index), and CI's `agent-scope` job runs it from the PR's BASE commit against the PR head (`--base SHA --head SHA`), so a PR cannot loosen it.
Standard library plus PyYAML. Exit 0 = fine, 1 = violations (printed), 2 = bad input.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

SUFFIX = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5, "Ei": 1024**6,
          "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18, "m": 1e-3, "": 1}
QUANTITY = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E|m)?$")
DEFAULT_FLOORS = {"cpu_millicores": 10, "memory_mib": 32}


def quantity(v) -> float | None:
    """A Kubernetes quantity as a float (bytes, or cores), None when it is not a plain one."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v >= 0 else None
    m = QUANTITY.match(str(v).strip())
    return float(m.group(1)) * SUFFIX[m.group(2) or ""] if m else None


def git(root: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:200] or "git failed")
    return p.stdout


def show(root: Path, rev: str, path: str) -> str | None:
    """The file at REV (`INDEX` = staged), None when it does not exist there."""
    spec = f":{path}" if rev == "INDEX" else f"{rev}:{path}"
    p = subprocess.run(["git", "-C", str(root), "show", spec], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def is_resources_leaf(path: tuple) -> bool:
    """`...resources.requests|limits.cpu|memory`: the only scalar a rightsizing PR may change."""
    return len(path) >= 3 and path[-3] == "resources" and path[-2] in ("requests", "limits") and path[-1] in ("cpu", "memory")


def is_resources_block(path: tuple, value) -> bool:
    """A whole `resources`, `requests` or `limits` mapping that holds only cpu/memory quantities (no other key, no nested surprise)."""
    if not isinstance(value, dict) or not path:
        return False
    if path[-1] == "resources":
        return set(value) <= {"requests", "limits"} and all(is_resources_block(path + (k,), v) for k, v in value.items())
    if path[-1] in ("requests", "limits") and len(path) >= 2 and path[-2] == "resources":
        return set(value) <= {"cpu", "memory"} and all(quantity(v) is not None for v in value.values())
    return False


def diff(base, head, path: tuple = ()) -> list[tuple]:
    """[(kind, path, old, new)] with kind in changed | added | removed | shape."""
    out: list[tuple] = []
    if isinstance(base, dict) and isinstance(head, dict):
        for k in sorted(set(base) | set(head), key=str):
            if k not in head:
                out.append(("removed", path + (k,), base[k], None))
            elif k not in base:
                out.append(("added", path + (k,), None, head[k]))
            else:
                out += diff(base[k], head[k], path + (k,))
    elif isinstance(base, list) and isinstance(head, list):
        if len(base) != len(head):
            out.append(("shape", path, f"list of {len(base)}", f"list of {len(head)}"))
        else:
            for i, (a, b) in enumerate(zip(base, head)):
                out += diff(a, b, path + (i,))
    elif type(base) is not type(head) and not (isinstance(base, (int, float)) and isinstance(head, (int, float)) and not isinstance(base, bool) and not isinstance(head, bool)):
        out.append(("shape", path, type(base).__name__, type(head).__name__))
    elif base != head:
        out.append(("changed", path, base, head))
    return out


def node_at(doc, path: tuple):
    for k in path:
        try:
            doc = doc[k]
        except (KeyError, IndexError, TypeError):
            return None
    return doc


def check_docs(base_docs: list, head_docs: list, name: str, floors: dict) -> list[str]:
    bad: list[str] = []
    if len(base_docs) != len(head_docs):
        return [f"{name}: the number of YAML documents changed"]
    for di, (b, h) in enumerate(zip(base_docs, head_docs)):
        label = name + (f" (document {di + 1})" if len(base_docs) > 1 else "")
        for kind, path, old, new in diff(b, h):
            where = ".".join(str(p) for p in path) or "<root>"
            if kind == "removed":
                bad.append(f"{label}: `{where}` was removed (a rightsizing PR deletes nothing)")
            elif kind == "shape" and not is_resources_leaf(path):
                bad.append(f"{label}: `{where}` changed shape ({old} -> {new})")
            elif kind == "added":
                if not is_resources_block(path, new) and not (is_resources_leaf(path)):
                    bad.append(f"{label}: `{where}` was added (only container resources may be)")
            elif is_resources_leaf(path):
                pass   # a value change (or a type change such as 1 -> "500m") under resources: judged below with the limits and floors
            elif path and path[-1] == "resourcesPreset":
                parent = node_at(h, path[:-1])
                if new != "none" or not isinstance(parent, dict) or not parent.get("resources"):
                    bad.append(f"{label}: `{where}` may only become \"none\" next to an explicit `resources:` block")
            else:
                bad.append(f"{label}: `{where}` changed ({str(old)[:40]!r} -> {str(new)[:40]!r}); only container resources may")
        bad += check_values(h, b, label, floors)
    return bad


def walk_resources(doc, path: tuple = ()):
    """Every `resources` mapping in the document with its path."""
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k == "resources" and isinstance(v, dict):
                yield path + (k,), v
            yield from walk_resources(v, path + (k,))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            yield from walk_resources(v, path + (i,))


def check_values(head, base, label: str, floors: dict) -> list[str]:
    bad = []
    base_res = {p: r for p, r in walk_resources(base)}
    for path, res in walk_resources(head):
        if base_res.get(path) == res:
            continue   # untouched by this change: its floors are not this PR's business
        where = ".".join(str(p) for p in path)
        req, lim = res.get("requests") or {}, res.get("limits") or {}
        for section, vals in (("requests", req), ("limits", lim)):
            for res_name, v in vals.items():
                q = quantity(v)
                if q is None:
                    bad.append(f"{label}: `{where}.{section}.{res_name}` is not a plain Kubernetes quantity ({str(v)[:20]!r})")
                    continue
                if res_name == "memory" and q < floors["memory_mib"] * 1024**2:
                    bad.append(f"{label}: `{where}.{section}.memory` is below the {floors['memory_mib']} MiB floor")
                if res_name == "cpu" and section == "requests" and q < floors["cpu_millicores"] / 1000:
                    bad.append(f"{label}: `{where}.requests.cpu` is below the {floors['cpu_millicores']}m floor")
        if "cpu" in lim and (base_res.get(path) or {}).get("limits", {}).get("cpu") != lim["cpu"]:
            bad.append(f"{label}: `{where}.limits.cpu` was added or changed (a rightsizing PR never sets a CPU limit)")
        for res_name in ("memory", "cpu"):
            rq, lq = quantity(req.get(res_name)), quantity(lim.get(res_name))
            if rq is not None and lq is not None and lq < rq:
                bad.append(f"{label}: `{where}` {res_name} limit is below its request")
    return bad


def load_floors(root: Path, base: str) -> dict:
    text = show(root, base if base != "INDEX" else "HEAD", "aiops/rightsizing.yml")
    try:
        f = (yaml.safe_load(text) or {}).get("floors") if text else None
        return {**DEFAULT_FLOORS, **f} if isinstance(f, dict) else dict(DEFAULT_FLOORS)
    except yaml.YAMLError:
        return dict(DEFAULT_FLOORS)


def changed_files(root: Path, base: str, head: str) -> list[tuple[str, str]]:
    """[(status, path)] between BASE and HEAD (`INDEX` = the staged changes)."""
    out = git(root, "diff", "--cached", "--name-status", "--no-renames", base) if head == "INDEX" else git(root, "diff", "--name-status", "--no-renames", base, head)
    return [tuple(ln.split("\t", 1)) for ln in out.splitlines() if "\t" in ln]


def check(root: Path, base: str, head: str) -> list[str]:
    bad: list[str] = []
    floors = load_floors(root, base)
    files = changed_files(root, base, head)
    if not files:
        return ["no changed files: there is nothing for a rightsizing PR to say"]
    for status, path in files:
        if status != "M":
            bad.append(f"{path}: a rightsizing PR only modifies existing files (status {status})")
            continue
        if not path.endswith((".yaml", ".yml")):
            bad.append(f"{path}: not a YAML file")
            continue
        old, new = show(root, base, path), show(root, head, path)
        if old is None or new is None:
            bad.append(f"{path}: could not read both versions")
            continue
        try:
            bad += check_docs(list(yaml.safe_load_all(old)), list(yaml.safe_load_all(new)), path, floors)
        except yaml.YAMLError as e:
            bad.append(f"{path}: does not parse as YAML ({str(e)[:80]})")
    return sorted(set(bad))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--head", default="INDEX")
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    try:
        bad = check(Path(a.root), a.base, a.head)
    except (RuntimeError, OSError) as e:
        print(f"resources-only: bad input: {e}")
        return 2
    if bad:
        print("resources-only: this change is more than container resources:")
        for b in bad:
            print(f"  - {b}")
        return 1
    print("resources-only: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
