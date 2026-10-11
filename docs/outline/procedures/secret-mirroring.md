<!-- docs/outline/procedures/secret-mirroring.md -->

# Secret mirroring (Vault ↔ 1Password)

The rule: **every secret stored in Vault has an offline copy in 1Password** (so it is recoverable when Vault is down), and 1Password items that machines read are referenced by UUID. A script, `scripts/secrets/vault-1p-mirror`, does the copying and verifies every write. Do not copy secrets by hand with `vault kv patch`: a value starting with `@` is read as a file, `-` as stdin, and hand-typed patches have stored wrong-but-plausible values before.

**Use when:** you added or changed a Vault secret, or want to prove the mirror is complete, or must restore a secret into Vault from 1Password.
**Operator-only:** it needs write access to Vault and a signed-in `op`. The non-interactive shim's token cannot write; use your own login.

**Prerequisites**

```bash
cd ~/homelab
op vault list | grep "Homelab 2.0"         # 1Password CLI signed in
vault token lookup >/dev/null && echo ok    # a token that can read/write secret/* (warm `homelab-env` cache or `vault login`)
python3 --version                           # 3.11+, standard library only
```

---

## 1. Check the mirror (read-only)

```bash
scripts/secrets/vault-1p-mirror status      # every mapped secret: SYNC / SYNC~ / 1P-MISSING / VAULT-MISSING / DIFFERS
scripts/secrets/vault-1p-mirror unmapped    # Vault paths in neither [[mirror]] nor [[ignore]]; exit 2 if any
```

Output shows only lengths and eight-character hash prefixes, never values. Two sides are in sync exactly when the hashes match. `SYNC~` means they differ only by a trailing newline (1Password trims it); that is fine.

## 2. Mirror a new Vault secret to 1Password

1. Run `unmapped`. For each new Vault path, add a block to `scripts/secrets/mirror-map.toml` (names only, never values):

   ```toml
   [[mirror]]
   vault_path = "secret/k8s/myapp/api-key"
   op_title   = "[Asgard] - Mirror - MyApp - API key"
   fields     = { value = "credential" }      # vault_field = "1Password field label"
   ```

   or an `[[ignore]]` block with a reason if it should not be mirrored.
2. Dry run, then apply:

   ```bash
   scripts/secrets/vault-1p-mirror to-1p NAME            # dry run for one entry (or --all)
   scripts/secrets/vault-1p-mirror to-1p NAME --apply
   scripts/secrets/vault-1p-mirror status                 # confirm SYNC
   scripts/secrets/vault-1p-mirror pin                    # write the new item's UUID into the map
   ```

3. Commit the map change through a normal PR (**Landing a change**). The new item's UUID is pinned automatically so a later title change in 1Password is harmless.

New items are `API Credential` objects titled `[Asgard] - Mirror - <Service> - <Detail>` in the `Homelab 2.0` vault. Human web logins are `Login` items with `category = "Login"`, `url = "https://..."` in the map.

## 3. Restore a secret from 1Password into Vault

```bash
scripts/secrets/vault-1p-mirror to-vault NAME            # dry run: shows what would be written
scripts/secrets/vault-1p-mirror to-vault NAME --apply
```

PEMs and OpenSSH keys get their final newline back automatically (an OpenSSH key without it will not load).

## 4. One-off copy of a single field

```bash
# 1Password item UUID/field  ->  Vault path/field
scripts/secrets/vault-1p-mirror mirror-to vault <1p-item-uuid>/<field> <vault-path>/<field> --apply
# Vault path/field  ->  1Password (create a new item with a title)
scripts/secrets/vault-1p-mirror mirror-to 1p <vault-path>/<field> <new-name>/credential \
  --title '[Asgard] - Mirror - X - Y' --apply
```

## Guarantees and refusals

- **Dry run unless `--apply`.** `--apply` needs explicit names or `--all`.
- **No silent overwrite.** If the destination exists and differs, the entry is skipped and reported. `--overwrite` replaces it; use it only when you know which side is right.
- **Every write is read back and compared by SHA-256.** A mismatch exits non-zero: treat that as a failed write.
- **It refuses** values starting with `@` or equal to `-`, leading or trailing spaces, carriage returns, or a one-line value with a trailing newline. `--allow-odd` overrides the whitespace checks only.
- SSH-Key items cannot be edited by the `op` CLI; for those only `to-vault` and `status` work. `WRONG-TYPE` in `status` means the item type must change: archive it (`op item delete ID --archive`) and re-run `to-1p`.

## Rule direction

Everything in Vault is mirrored in 1Password. **Not** everything in 1Password is in Vault (web logins, bootstrap material, Terraform-only tokens), so the tool only looks for Vault secrets missing from 1Password, never the reverse.

---

## See also

- **Credential rotation** (this section): rotations end with a mirror check.
- **Identity & secrets** (Components): the three-store model.
