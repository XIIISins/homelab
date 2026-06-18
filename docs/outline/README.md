<!-- docs/outline/README.md -->

# Outline sync tooling

The pages under `docs/outline/` are the **source of truth** for the live Outline
wiki (the `Homelab` collection at <https://wiki.xiiisins.com>). `sync_outline.py`
reconciles them into Outline via the REST API: it updates the pages that already
exist and creates any that are missing. Edit the Markdown here, run the sync, and
the live wiki converges.

> Not wiki pages, so the sync skips them: `README.md`, `conversion-progress.md`,
> `sync_outline.py`, `.outline-manifest.json`.

## How it maps

- **Title** = the page's first `# H1`. The leading `<!-- path -->` comment and the
  H1 are stripped from the body Outline stores (Outline keeps the title separately).
- **Hierarchy** = the directory layout. `docs/outline/<section>.md` is a top-level
  doc; `docs/outline/<section>/<sub>.md` is its child.
- **Matching** (reconcile) is tolerant: exact title → normalized title within the
  same parent → normalized title anywhere. Normalization is case-insensitive,
  treats `&`==`and`, and drops parentheticals — so the file H1 `Storage & data`
  matches a hand-titled live `Storage & Data`, and `Zabbix (Hugin)` matches `Zabbix`.
- After an `--apply`, `.outline-manifest.json` records `{file → {id, title, sha}}`.
  Re-runs **SKIP** any page whose source is unchanged since the last push (content
  hash match). Outline re-serializes Markdown on store, so its returned text never
  byte-matches the file — the sync tracks what *it* pushed rather than diffing
  Outline's reformatted text. Consequence: a page edited only in Outline is **not**
  reverted until its source file changes. On `--apply` the **file H1 wins** — a
  hand-tweaked live title gets re-cased to match the file. Commit the manifest; it
  is the deterministic-sync state (doc IDs + hashes, no secrets).

## The API token

Mint a personal token in Outline: **Settings → API & Apps → New Token** (log in via
Authentik OIDC first). Store it in **both** stores, per the homelab secrets rule:

- **Vault (primary):** `secret/ansible/outline/api-token`, field `token`.
- **1Password (offline mirror):** item `[Asgard] - Mirror - Outline - admin API token`
  (UUID `ulzotac7ns77ia6ql73bsr2tsy`), field `credential`. Machine-accessed by UUID,
  so the title can change without breaking anything.

The script resolves the token in this order: `$OUTLINE_API_TOKEN` → `vault kv get
-field=token secret/ansible/outline/api-token` (needs `VAULT_ADDR`/`VAULT_TOKEN`
in env — the vault-backed shim provides them).

## Usage

```bash
# dry-run (default) — prints the CREATE/UPDATE/SKIP plan, writes nothing
python3 docs/outline/sync_outline.py
python3 docs/outline/sync_outline.py --verbose   # + per-file detail (title changes)

# apply — writes to the live wiki and updates the manifest
python3 docs/outline/sync_outline.py --apply
```

Token from Vault (preferred) or, for a one-off where the shim isn't wired:

```bash
export OUTLINE_API_TOKEN="$(op read 'op://Homelab 2.0/ulzotac7ns77ia6ql73bsr2tsy/credential')"
python3 docs/outline/sync_outline.py
```

Pure stdlib — no `pip install`.

## Gotchas

- **Links don't render even though the stored markdown has them.** Seen on pages
  that were originally hand-imported into Outline and then updated via the API many
  times: Outline keeps the `[text](url)` in the document's markdown but never
  materializes it as a link mark, so it renders as plain text (in *read* mode — edit
  mode shows everything as a link, which masks the problem). `documents.update` does
  not repair it. **Fix: recreate the doc fresh** — `documents.create` always renders
  links. The whole-collection recreate that fixed this (2026-06-18): soft-delete the
  managed docs (IDs from the manifest), `rm .outline-manifest.json`, then run
  `--apply` **twice** (pass 1 creates all; pass 2 wires cross-links to the new IDs,
  since URLs aren't known until the docs exist). Recreating changes every doc's
  ID/URL. Deleted docs sit in Trash (recoverable); the unmanaged "Storage redesign"
  page has to be restored from Trash by hand (no source file).
- **Outline rate-limits writes (HTTP 429).** A bulk recreate (~80 write calls) trips
  it. The sync throttles writes to 1.2s apart and retries 429 with backoff
  (`api()` + `WRITE_OPS`), so a full recreate takes a few minutes — let it run.

## Cross-reference links

The sync rewrites the **bold canonical-name** cross-references into Outline doc
links at publish time (`rewrite_links`). Source files keep the readable bold names;
only Outline gets the links. Links are **relative** (`/doc/<slug-id>`) — Outline
renders these as internal links and they're portable across a hostname change.
(Absolute `https://<wiki>/doc/...` also works if you ever prefer it; switch the
`urlmap` construction.) The transform is high-precision — a bold
span is linked **only** if its text resolves (normalized) to a real page title, so
emphasis bold (`**The rule:**`, `**tiered by access pattern**`) is never touched.
Two contexts are linked:

- inside a nav section (**See also** / **Where to go deeper** / **Where to go
  next**), every page-name bold;
- elsewhere, bold immediately followed by a section qualifier (`(Components)`,
  `section`, `subpage`, …).

The link map is built from the live collection each run, so a page must already
exist for others to link to it: a brand-new page's **inbound** links land on the
*next* sync (its own outbound links work on the first). Bold names inside tables
(the parent catalogs' "Deep page" column) are intentionally left as-is.
