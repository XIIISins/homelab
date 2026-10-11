<!-- docs/outline/procedures/approle-rotation.md -->

# AppRole rotation

Vault AppRole SecretIDs are the machine credentials for Ansible and automation. Each is rotated on a 90-day cadence, or at once if one leaks. The footgun is that there are **two storage conventions and two helpers**, and using the wrong one fails or corrupts the wrong store.

---

## The roles

| Role | Used by | Canonical store | Helper |
|---|---|---|---|
| `ansible-local` | The operator's workstation | 1Password item | `rotate-approle ansible-local` |
| `ansible-frigg` | The control node | 1Password item | `rotate-approle ansible-frigg`, then re-propagate to Frigg |
| `ansible-awx` | Semaphore (the role pre-dates the AWX → Semaphore switch) | Vault KV at `secret/k8s/semaphore/vault-approle` | `rotate-semaphore-approle` |

Running `rotate-approle ansible-awx` fails with "unknown role", because that helper is 1Password-canonical and this role has no 1Password item.

## The flow

**1Password-canonical roles.** The helper mints a new SecretID and prints it, along with its accessor and expiry. The operator pastes those three into the 1Password item, the helper hash-verifies the paste, and only then does it revoke the old SecretID. It snapshots the old accessor before minting, so there is no window where the store holds a stale value.

- **Never touch the `username` field.** It holds the RoleID, which is stable across rotations. A past rotation pasted the accessor into it and broke the next login.
- If a rotation aborts midway, run the helper with `--fix`: it lists the role's SecretIDs and offers to destroy any that 1Password does not reference.
- For `ansible-frigg`, Frigg does not read 1Password, so re-run the control-node play with the new SecretID to refresh its on-disk secret.
- For `ansible-local`, refresh the local environment and re-seed the non-interactive secret file afterwards. Re-seeding only copies what 1Password holds; it does not mint, so if 1Password is the stale copy, only a proper rotation fixes it.

**Semaphore's role.** The helper mints a SecretID, writes it to Vault KV, runs the Semaphore Terraform module to push it into Semaphore's environment, prompts you to validate with a drift-check, then revokes the old ones. No 1Password involvement.

## Why 90 days actually means 90 days

The auth mount's `max_lease_ttl` caps every role's SecretID TTL. Vault's 768-hour default silently clamped 90-day roles to 32 days; the mount is tuned to 90 days in Terraform. Verify with the auth mount tune output and a fresh mint.

SecretIDs are never in Terraform state, and roles carry no source-address binding because callers' addresses change or are NAT'd.

---

## See also

- **Credential rotation** (this section): every other credential class.
- **Identity & secrets** (Components): the auth-method model.
- **Vault recovery** (this section).
