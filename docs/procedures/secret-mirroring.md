<!-- docs/procedures/secret-mirroring.md -->

# Procedure — mirroring secrets between Vault and 1Password

*Tool: [`scripts/secrets/vault-1p-mirror`](../../scripts/secrets/vault-1p-mirror), map: [`scripts/secrets/mirror-map.toml`](../../scripts/secrets/mirror-map.toml). Rule it serves: every Vault-held homelab secret has an offline 1Password mirror ([`identity-secrets.md`](../architecture/identity-secrets.md), "three stores, one rule"). Why a script: hand-run `vault kv patch` has silently stored wrong values (see [`vault.md`](../known-issues/vault.md)).*

## Prerequisites

- **Operator-only tool.** It uses whatever Vault token is in your environment, so run it with your own admin/root login (the warm `homelab-env` cache, or `vault login`). The shim's AppRole token cannot write secrets and is not meant to run this.
- `op` signed in (`op vault list` works).
- Python 3.11+ (stdlib only).

## Everyday use

```bash
scripts/secrets/vault-1p-mirror status                    # every mapped secret: SYNC / 1P-MISSING / VAULT-MISSING / DIFFERS (length + hash only)
scripts/secrets/vault-1p-mirror to-1p --all               # dry run: what would be created in 1Password
scripts/secrets/vault-1p-mirror to-1p --all --apply       # do it (creates missing "[Asgard] - Mirror - ..." items)
scripts/secrets/vault-1p-mirror to-vault NAME --apply     # restore one secret from 1Password into Vault

# one field, no map entry needed (src, then dst; dst optional when the pair is in the map)
scripts/secrets/vault-1p-mirror mirror-to vault <1p-uuid>/<field> <vault-path>/<field> --apply
scripts/secrets/vault-1p-mirror mirror-to 1p <vault-path>/<field> <1p-uuid>/<field> --apply
scripts/secrets/vault-1p-mirror mirror-to 1p <vault-path>/<field> <new-uuid-or-name>/credential --title '[Asgard] - Mirror - X - Y' --apply
```

## Keeping the map current

The map is a hand-edited file; **adding an item to 1Password or a secret to Vault does not update it by itself.** Two commands keep it honest:

```bash
scripts/secrets/vault-1p-mirror unmapped   # Vault paths in neither [[mirror]] nor [[ignore]], and 1P "Mirror" items no entry points at (exit 2 if any)
scripts/secrets/vault-1p-mirror pin        # write op_id into the map for entries whose 1P item now exists
```

- **New Vault secret:** run `unmapped`, then add a `[[mirror]]` block (names only: `vault_path`, `op_title`, `fields = { vault_field = "1P label" }`) or an `[[ignore]]` block with a reason. `status`, then `to-1p --apply`.
- **`to-1p --apply` that creates an item pins it:** the new item's UUID is written into the map as `op_id` automatically, so a later title rename in 1P is harmless.
- **An item you add to 1Password by hand:** point a `[[mirror]]` entry at it (`op_title`, then run `pin`), or use `mirror-to` for a one-off. Until then `unmapped` lists it if its title contains "Mirror".
- A pair marked `GUESSED` in a `# NOTE:` comment was matched by title because the values differed. Read `status` before ever using `--overwrite` on it.

## Direction of the rule

Everything in Vault is mirrored in 1Password; **not every 1Password item has to exist in Vault** (web logins, bootstrap material, Terraform-only tokens). `unmapped` and `status` therefore only look for Vault secrets missing from 1Password, never the reverse.

## Initial state of the map (2026-10-03)

88 entries: 33 point at items that already existed in 1Password (matched by comparing value hashes, so nothing is duplicated), 55 are new `[Asgard] - Mirror - ...` / `[DO - Offsite] - Mirror - ...` items that `to-1p --apply` creates. Seven Vault paths are `[[ignore]]`d with a reason (Terraform-minted Tailscale auth keys, the `iac-env` aggregate, a derived hash, a Document-type 1P item, an unaddressable field name, the removed n8n). Items of type SSH Key cannot be edited by the `op` CLI, so for those only `to-vault` and `status` work.

## What it guarantees

- **Dry run unless `--apply`.** `--apply` needs explicit names or `--all`.
- **No silent overwrite.** A destination that already exists and differs is skipped and reported; `--overwrite` replaces it.
- **Verified writes.** After each write the value is read back and compared by SHA-256; a mismatch exits non-zero. A passing run means the stored bytes equal the source bytes.
- **No secret on screen.** Output carries `len=` and an 8-hex hash prefix only; error text from `vault`/`op` is scrubbed of every value handled in the run. Two runs are in sync exactly when the hashes match.
- **Trailing newlines are handled.** 1Password trims the final newline of multi-line values, Vault often keeps it (PEM / OpenSSH keys, a password file). A pair that differs only by that newline is reported `SYNC~` and left alone, and a PEM written into Vault always gets its final newline back (an OpenSSH key without it fails to load); one written into 1Password is trimmed.
- **Refuses values that get mangled:** a value starting with `@` or equal to `-` (the vault CLI reads a file/stdin instead of storing it), leading/trailing spaces, a CR, or a one-line value with a trailing newline. `--allow-odd` overrides the whitespace checks only; the `@`/`-` refusal is absolute for Vault writes.

## 1Password conventions it follows

Created items are `API Credential` named `[Asgard] - Mirror - <Service> - <Detail>` in `Homelab 2.0`, the secret in the built-in `credential` field; ids/urls/server lists are plain-text fields (`label:text` in the map). Machine-read items still go by UUID ([`identity-secrets.md`](../architecture/identity-secrets.md), "1Password vault organisation").

### Audit of 2026-10-03 (every 1Password item in `Homelab 2.0` against every Vault field)

Exact value matches all sat in the item and field the map names (nothing was filed under a different field). Three pairs differed only by a trailing newline (the Ansible Vault password on Frigg, the Semaphore GitHub deploy key's private and public halves). Two title-based guesses were wrong and became new items: the 1P note `Mirror - Cloudflare - Tunnel credentials` holds only the tunnel UUID, so the tunnel's credentials JSON (which carries the tunnel secret) had **no** 1Password copy; and `Terraform - Semaphore - Admin API token` holds a different value from `secret/k8s/semaphore/admin-api-token`. `[Asgard] - Ansible - AdGuard - keepalived VRRP pass` (20 chars) matches neither Vault VRRP password (8 chars each): it predates them and is not a mirror.
