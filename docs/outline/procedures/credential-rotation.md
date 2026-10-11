<!-- docs/outline/procedures/credential-rotation.md -->

# Credential rotation

A rotation pattern for each credential class the automation uses. The same rules apply to all of them; only the endpoints and the places to propagate differ. The full command sequences are in the repository's `docs/procedures/credential-rotation.md`.

---

## Ground rules

1. **Never let a raw secret value pass through a command line or a tool's output.** Capture into a shell variable, verify writes by comparing short hashes, unset when done. If a step must print the real value (a create-once API response), the operator runs it directly.
2. **Every 1Password write is followed by an independent read-back and hash-compare** before anything downstream (such as deleting the old credential) happens. The 1Password CLI can report success while writing nothing; if hashes differ, paste into the app instead of retrying.
3. **Check every mirror, not just the primary.** The control node's environment secret mirrors AWS, NetBox, Authentik, AdGuard, Cloudflare and Semaphore credentials, and no helper syncs it automatically. A missed sync breaks nothing immediately and surfaces later as an unrelated failure. The infra health check catches a missed sync within a scheduled run.
4. **Overlap, then revoke.** Mint the new credential, prove it works, propagate, and only then revoke the old one.
5. **After rotating anything in the control node's env secret**, refresh the 3-hour environment cache on every machine that uses it (with output suppressed).

---

## By credential

| Credential | Where it lives | Notes |
|---|---|---|
| **Vault root token** | 1Password (bootstrap) | Rotated by an interactive helper that mints an orphan token (a child would be revoked with its parent), hash-verifies the paste and then revokes the old one. Recovery if it goes wrong: generate a new root. |
| **Vault AppRole SecretIDs** | See **AppRole rotation** | |
| **AWS state key** | 1Password, environment cache | IAM key for Terraform state; create the new key, update 1Password, verify, delete the old key. |
| **Cloudflare tokens** | 1Password and Vault | At least three separate tokens exist: the broad Terraform one (mirrored in 1Password), a narrow DNS-01-only one for cert-manager (Vault only; never overwrite its path with the Terraform token), and an unused read-all one. Roll the Terraform token from the profile-level tokens page, which mints the user-token type the health check verifies. Verify against the endpoint matching the token's type; the wrong endpoint gives a misleading "invalid" answer. |
| **Authentik admin API token** | 1Password | API-driven. Confirm the token authenticating the rotation is alive first; if no credential works at all, mint one directly against the database, bypassing the API. |
| **NetBox admin API token** | 1Password | Neither the create response nor the UI reliably gives a usable value in this deployment; the procedure reconstructs it and proves it before cleaning up older tokens. |
| **Semaphore API token** | 1Password | API-driven; capture the current token id first. |
| **AdGuard admin password** | 1Password, Vault (hash and plaintext) | The admin user is `ghost`. The role's idempotency guard no-ops a rotation on an existing node, so the hash is changed in place. Test by logging in, never by comparing bcrypt hashes. |

---

## After a leak

If a secret appears in a transcript or log: tell the owner and recommend rotation. Do not rotate automatically. A past incident rotated eight credentials in quick succession and produced this list.

---

## See also

- **AppRole rotation** (this section).
- **Identity & secrets** (Components): the stores involved.
- **Vault recovery** (this section).
