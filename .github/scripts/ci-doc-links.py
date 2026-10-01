#!/usr/bin/env python3
# .github/scripts/ci-doc-links.py — verify relative links in the repo's Markdown.
#
# CLAUDE.md requires docs to cross-reference each other (decisions <-> known-issues
# <-> incidents), so a dead relative link is a real defect. Checks, for every
# tracked *.md:
#   - [text](relative/path.md)           -> the target exists
#   - [text](relative/path.md#anchor)    -> ...and the heading anchor exists
#   - [text](#anchor)                    -> the anchor exists in the same file
# Skips: http(s)/mailto/tel links (no network in CI — external rot is not a gate),
# fenced code blocks, inline code spans, and reference-style definitions.
# Exit 1 on any broken link. Stdlib only.
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
files = subprocess.check_output(["git", "ls-files", "*.md"], text=True, cwd=ROOT).split()

LINK = re.compile(r"(?<!!)\[(?:[^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
INLINE_CODE = re.compile(r"`[^`]*`")
SKIP_SCHEMES = ("http://", "https://", "mailto:", "tel:", "ftp://")


def slug(text: str, seen: dict) -> str:
    """GitHub heading anchor: lowercase, strip markup/punctuation, spaces -> '-', dedupe -N."""
    text = re.sub(r"<[^>]+>", "", text)                 # inline html
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links -> text
    text = text.replace("`", "").replace("*", "").replace("~", "")
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    text = text.replace(" ", "-")
    n = seen.get(text, 0)
    seen[text] = n + 1
    return text if n == 0 else f"{text}-{n}"


_anchor_cache: dict = {}


def anchors(path: Path) -> set:
    if path in _anchor_cache:
        return _anchor_cache[path]
    out, seen, in_fence = set(), {}, False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING.match(line)
        if m:
            out.add(slug(m.group(2), seen))
    _anchor_cache[path] = out
    return out


bad = []
checked = 0
for rel in files:
    path = ROOT / rel
    in_fence = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in LINK.finditer(INLINE_CODE.sub("", line)):
            target = m.group(1).strip("<>")
            if target.startswith(SKIP_SCHEMES):
                continue
            checked += 1
            ref, _, frag = target.partition("#")
            dest = path if not ref else (path.parent / ref).resolve()
            if not ref and not frag:
                continue
            try:
                dest.relative_to(ROOT)
            except ValueError:
                bad.append((rel, lineno, target, "escapes repo root"))
                continue
            if not dest.exists():
                bad.append((rel, lineno, target, "target not found"))
                continue
            if frag and dest.suffix == ".md" and frag.lower() not in anchors(dest):
                bad.append((rel, lineno, target, f"anchor #{frag} not found"))

for rel, lineno, target, why in bad:
    print(f"::error file={rel},line={lineno},title=broken doc link::{target} — {why}")
print(f"checked {checked} relative links in {len(files)} markdown files; {len(bad)} broken")
sys.exit(1 if bad else 0)
