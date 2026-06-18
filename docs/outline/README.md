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

## Not done by the sync (follow-ups)

- **Cross-reference link rewriting.** Pages use **bold canonical names**
  (`**Hardware** section`, `**Edge** (this section)`) as placeholders for real
  Outline doc links. Rewriting those to `/doc/<id>` links is a deliberate separate
  step — it needs a curated *canonical-name → doc-id* alias map (the bold names
  aren't always exact titles) and is easy to get wrong, so it is **not** automated
  here. Do it as a reviewed second pass once the manifest is populated.
- **Per-service subpages** for Startpage, MicroBin, and n8n (now live) — the parent
  catalog covers them; dedicated pages are append-only build-out.
