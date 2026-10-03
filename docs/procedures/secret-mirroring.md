<!-- docs/procedures/secret-mirroring.md -->

# Procedure — mirroring secrets between Vault and 1Password

*Tool: [`scripts/secrets/vault-1p-mirror`](../../scripts/secrets/vault-1p-mirror), map: [`scripts/secrets/mirror-map.toml`](../../scripts/secrets/mirror-map.toml). Rule it serves: every Vault-held homelab secret has an offline 1Password mirror ([`identity-secrets.md`](../architecture/identity-secrets.md), "three stores, one rule"). Why a script: hand-run `vault kv patch` has silently stored wrong values (see [`vault.md`](../known-issues/vault.md)).*

## Prerequisites

- `vault` authenticated with a token that can **read** the paths, and for `to-vault` **write** them. The shim's AppRole token cannot write secrets: use your own Vault login (OIDC / admin) for `to-vault`.
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

A new secret: add a `[[mirror]]` block to the map (names only), `status`, then `to-1p --apply`. After a create, the script prints the new item's UUID: put it in the map as `op_id` so a later title rename is harmless.

## What it guarantees

- **Dry run unless `--apply`.** `--apply` needs explicit names or `--all`.
- **No silent overwrite.** A destination that already exists and differs is skipped and reported; `--overwrite` replaces it.
- **Verified writes.** After each write the value is read back and compared by SHA-256; a mismatch exits non-zero. A passing run means the stored bytes equal the source bytes.
- **No secret on screen.** Output carries `len=` and an 8-hex hash prefix only; error text from `vault`/`op` is scrubbed of every value handled in the run. Two runs are in sync exactly when the hashes match.
- **Refuses values that get mangled:** a value starting with `@` or equal to `-` (the vault CLI reads a file/stdin instead of storing it), leading/trailing spaces, a CR, or a one-line value with a trailing newline. `--allow-odd` overrides the whitespace checks only; the `@`/`-` refusal is absolute for Vault writes.

## 1Password conventions it follows

Created items are `API Credential` named `[Asgard] - Mirror - <Service> - <Detail>` in `Homelab 2.0`, the secret in the built-in `credential` field; ids/urls/server lists are plain-text fields (`label:text` in the map). Machine-read items still go by UUID ([`identity-secrets.md`](../architecture/identity-secrets.md), "1Password vault organisation").
