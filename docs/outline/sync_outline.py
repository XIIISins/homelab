#!/usr/bin/env python3
# docs/outline/sync_outline.py
"""
Reconcile the drafted Outline pages under docs/outline/ into the live Outline
wiki (https://wiki.xiiisins.com) via the Outline REST API.

Reconcile semantics (some pages may already exist):
  - Build a map of existing documents in the target collection.
  - For each local page: UPDATE the matching live doc (by manifest id, else by
    title), or CREATE it if absent. Pages whose title + body already match are
    SKIPPED.
  - Hierarchy mirrors the directory layout: docs/outline/<section>.md is a
    top-level doc; docs/outline/<section>/<sub>.md is its child.
  - A file -> documentId manifest (.outline-manifest.json) is written so future
    runs are deterministic even if a title changes.

DRY-RUN BY DEFAULT. Nothing is written without --apply.

Token (in precedence order):
  1. $OUTLINE_API_TOKEN
  2. Vault: `vault kv get -field=token secret/ansible/outline/api-token`
     (needs VAULT_ADDR + VAULT_TOKEN in env — the vault-backed shim provides them)
Mint the token in Outline UI: Settings -> API & Apps -> New Token. Store it in
Vault at secret/ansible/outline/api-token (field `token`) as primary, and mirror
to 1Password "Asgard - Outline - admin API token".

Pure stdlib — no pip dependencies.

Usage:
  python3 docs/outline/sync_outline.py            # dry-run, show the plan
  python3 docs/outline/sync_outline.py --apply     # write changes
  python3 docs/outline/sync_outline.py --verbose    # show per-file diffs in dry-run

Cross-reference link rewriting (the bold **Name** placeholders -> Outline doc
links) is a deliberate SEPARATE step and is NOT done here — see README.md.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_URL = os.environ.get("OUTLINE_API_URL", "https://wiki.xiiisins.com/api")
COLLECTION_NAME = os.environ.get("OUTLINE_COLLECTION", "Homelab")
OUTLINE_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = OUTLINE_DIR / ".outline-manifest.json"

# Files in docs/outline/ that are NOT wiki pages.
IGNORE_FILES = {"conversion-progress.md", "README.md"}

H1_RE = re.compile(r"^#\s+(.+?)\s*$")
PATH_COMMENT_RE = re.compile(r"^<!--\s*docs/outline/.*-->\s*$")


# --------------------------------------------------------------------------- #
# Token + HTTP
# --------------------------------------------------------------------------- #
def get_token() -> str:
    tok = os.environ.get("OUTLINE_API_TOKEN")
    if tok:
        return tok.strip()
    try:
        out = subprocess.run(
            ["vault", "kv", "get", "-field=token", "secret/ansible/outline/api-token"],
            capture_output=True, text=True, check=True,
        )
        tok = out.stdout.strip()
        if tok:
            return tok
    except FileNotFoundError:
        pass
    except subprocess.CalledProcessError as e:
        sys.exit(f"Vault lookup failed: {e.stderr.strip()}\n"
                 "Set $OUTLINE_API_TOKEN, or store the token at "
                 "secret/ansible/outline/api-token (field `token`).")
    sys.exit("No Outline API token. Set $OUTLINE_API_TOKEN or store it in Vault "
             "at secret/ansible/outline/api-token (field `token`).")


def api(method: str, payload: dict, token: str) -> dict:
    req = urllib.request.Request(
        f"{API_URL}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise SystemExit(f"API {method} -> HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise SystemExit(f"API {method} unreachable: {e.reason}")


def api_paginated(method: str, payload: dict, token: str) -> list:
    items, offset, limit = [], 0, 100
    while True:
        page = api(method, {**payload, "offset": offset, "limit": limit}, token)
        data = page.get("data", [])
        items.extend(data)
        if len(data) < limit:
            return items
        offset += limit


# --------------------------------------------------------------------------- #
# Local pages
# --------------------------------------------------------------------------- #
class Page:
    def __init__(self, path: Path):
        self.path = path
        self.rel = path.relative_to(OUTLINE_DIR).as_posix()
        raw = path.read_text()
        self.title, self.text = self._parse(raw)
        # parent file: docs/outline/<dir>/<x>.md -> docs/outline/<dir>.md
        parent = path.parent
        if parent == OUTLINE_DIR:
            self.parent_rel = None  # top-level
        else:
            self.parent_rel = parent.with_suffix(".md").relative_to(OUTLINE_DIR).as_posix()
        self.depth = 0 if self.parent_rel is None else 1

    @staticmethod
    def _parse(raw: str):
        lines = raw.splitlines()
        title = None
        body_start = 0
        for i, line in enumerate(lines):
            if PATH_COMMENT_RE.match(line):
                continue
            m = H1_RE.match(line)
            if m and title is None:
                title = m.group(1)
                body_start = i + 1
                break
            if line.strip():  # first real content before any H1
                break
        if title is None:
            raise SystemExit(f"No '# Title' H1 found in page")
        body = "\n".join(lines[body_start:]).strip() + "\n"
        return title, body


def discover_pages() -> list:
    pages = []
    for p in sorted(OUTLINE_DIR.rglob("*.md")):
        if p.name in IGNORE_FILES:
            continue
        if any(part.startswith("_") for part in p.relative_to(OUTLINE_DIR).parts):
            continue
        try:
            pages.append(Page(p))
        except SystemExit as e:
            sys.exit(f"{p.relative_to(OUTLINE_DIR)}: {e}")
    # top-level first so parents exist before children are created
    pages.sort(key=lambda pg: (pg.depth, pg.rel))
    return pages


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def normtitle(s: str) -> str:
    """Tolerant title key: case-insensitive, '&'=='and', parentheticals and
    punctuation dropped. Lets the file H1 match a hand-tweaked live title
    (e.g. 'Storage & data' == 'Storage & Data', 'Zabbix (Hugin)' == 'Zabbix')."""
    s = re.sub(r"\(.*?\)", "", s)
    s = s.replace("&", "and")
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def rewrite_links(text: str, urlmap: dict) -> str:
    """Publish-time transform: turn bold canonical-name cross-references into
    Outline doc links, leaving emphasis bold alone. Source files keep the
    readable bold names; only Outline gets the links. A bold span whose text
    doesn't resolve to a page title is ALWAYS left untouched, so this only ever
    links real cross-references. Two contexts:
      - inside a nav section (See also / Where to go deeper / Where to go next),
        every page-name bold is linked (these sections are pure cross-refs;
        non-page bold like a '**I'm on call**' lead-in is left alone);
      - elsewhere, only bold immediately followed by a section qualifier
        ('(Components)', 'section', 'subpage', ...) is linked."""
    qual = (r'(?=\s*\((?:Components|Hardware|Procedures|Troubleshooting|this section)\)'
            r'|\s+section\b|\s+subpage\b)')
    nav = re.compile(r'^#{1,6}\s+(?:See also|Where to go deeper|Where to go next)\b', re.I)

    def link(m):
        key = normtitle(m.group(1))
        return f"[{m.group(1)}]({urlmap[key]})" if key in urlmap else m.group(0)

    out, in_nav = [], False
    for ln in text.split("\n"):
        if re.match(r'^#{1,6}\s', ln):
            in_nav = bool(nav.match(ln))
        pat = r'\*\*([^*]+?)\*\*' + ('' if in_nav else qual)
        out.append(re.sub(pat, link, ln))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Reconcile
# --------------------------------------------------------------------------- #
def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {}


def save_manifest(manifest: dict):
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def ensure_collection(token: str, apply: bool) -> str:
    cols = api_paginated("collections.list", {}, token)
    for c in cols:
        if c["name"] == COLLECTION_NAME:
            return c["id"]
    if not apply:
        print(f"  [dry-run] would CREATE collection '{COLLECTION_NAME}'")
        return "<new-collection>"
    res = api("collections.create", {"name": COLLECTION_NAME,
                                     "description": "Homelab design + operations wiki.",
                                     "permission": "read"}, token)
    print(f"  CREATED collection '{COLLECTION_NAME}' -> {res['data']['id']}")
    return res["data"]["id"]


def main():
    ap = argparse.ArgumentParser(description="Reconcile docs/outline/ into Outline.")
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    ap.add_argument("--verbose", action="store_true", help="show per-file detail")
    args = ap.parse_args()

    token = get_token()
    pages = discover_pages()
    manifest = load_manifest()

    print(f"Target: {API_URL}  collection '{COLLECTION_NAME}'")
    print(f"Local pages: {len(pages)}   mode: {'APPLY' if args.apply else 'DRY-RUN'}\n")

    collection_id = ensure_collection(token, args.apply)

    # Index live docs by id and by title (within the collection).
    live = {}
    if collection_id != "<new-collection>":
        for d in api_paginated("documents.list", {"collectionId": collection_id}, token):
            live[d["id"]] = d
    by_title = {d["title"]: d for d in live.values()}
    by_key = {(d.get("parentDocumentId"), normtitle(d["title"])): d for d in live.values()}
    by_norm = {}
    for d in live.values():
        by_norm.setdefault(normtitle(d["title"]), []).append(d)
    # Relative `/doc/<slug-id>` links — Outline renders these as internal links
    # (verified). Preferred over absolute URLs: portable across a hostname change.
    # (An earlier "links don't render" symptom turned out to be a bulk-apply that
    # dropped links entirely, not the link form.)
    urlmap = {normtitle(d["title"]): (d.get("url") or f"/doc/{d['id']}")
              for d in live.values()}

    plan = {"create": [], "update": [], "skip": []}
    rel_to_id = {}  # resolved this run, for parent lookup

    for pg in pages:
        # parent first — child matching is scoped to the resolved parent
        parent_id = None
        if pg.parent_rel is not None:
            parent_id = rel_to_id.get(pg.parent_rel)
            if parent_id is None and args.apply:
                sys.exit(f"parent {pg.parent_rel} not resolved before child {pg.rel}")

        # resolve an existing doc: manifest id -> exact title -> normalized
        # title within the same parent -> normalized title anywhere.
        existing = None
        prev = manifest.get(pg.rel)
        prev_id = prev.get("id") if isinstance(prev, dict) else prev
        nt = normtitle(pg.title)
        if prev_id and prev_id in live:
            existing = live[prev_id]
        elif pg.title in by_title:
            existing = by_title[pg.title]
        elif (parent_id, nt) in by_key:
            existing = by_key[(parent_id, nt)]
        elif by_norm.get(nt):
            existing = by_norm[nt][0]

        out_text = rewrite_links(pg.text, urlmap)
        sha = text_sha(out_text)
        if existing is None:
            plan["create"].append(pg.rel)
            doc_id = f"<new:{pg.rel}>"
            if args.apply:
                res = api("documents.create", {
                    "title": pg.title, "text": out_text,
                    "collectionId": collection_id,
                    **({"parentDocumentId": parent_id} if parent_id else {}),
                    "publish": True,
                }, token)
                doc_id = res["data"]["id"]
                manifest[pg.rel] = {"id": doc_id, "title": pg.title, "sha": sha}
                print(f"  CREATE  {pg.rel}  -> {doc_id}")
            rel_to_id[pg.rel] = doc_id
        else:
            rel_to_id[pg.rel] = existing["id"]
            # SKIP only if we already pushed this exact source (same id, title,
            # content hash). Outline re-serializes Markdown on store, so its
            # returned text never byte-matches the file — we track what *we*
            # pushed instead. A page edited only in Outline is not reverted
            # until its source file changes.
            same = (isinstance(prev, dict) and prev.get("id") == existing["id"]
                    and prev.get("title") == pg.title and prev.get("sha") == sha)
            if same:
                plan["skip"].append(pg.rel)
            else:
                plan["update"].append(pg.rel)
                if args.apply:
                    api("documents.update", {
                        "id": existing["id"], "title": pg.title,
                        "text": out_text, "publish": True,
                    }, token)
                    manifest[pg.rel] = {"id": existing["id"], "title": pg.title, "sha": sha}
                    print(f"  UPDATE  {pg.rel}  -> {existing['id']}")
                elif args.verbose:
                    print(f"  [dry-run] UPDATE {pg.rel} (id={existing['id']})"
                          f"  title-changed={existing['title'] != pg.title}")

    if not args.apply:
        print("\nPLAN (dry-run):")
        for kind in ("create", "update", "skip"):
            print(f"  {kind.upper():7} {len(plan[kind])}")
            for rel in plan[kind]:
                print(f"      {rel}")
        print("\nRe-run with --apply to write. Manifest NOT updated in dry-run.")
    else:
        save_manifest(manifest)
        print(f"\nDone. Manifest written to {MANIFEST_PATH.name} "
              f"({len(manifest)} entries).")
        print(f"  created={len(plan['create'])} updated={len(plan['update'])} "
              f"skipped={len(plan['skip'])}")


if __name__ == "__main__":
    main()
