<!-- docs/outline/procedures/approle-rotation.md -->

# AppRole rotation

Rotating the Vault AppRole SecretIDs that machines use to authenticate. Run this as routine credential hygiene, or immediately if a SecretID is exposed.

> **The one thing to get right:** there are **three AppRoles** across **two storage conventions** and **two rotation helpers**. Conflating them produces a mismatched RoleID/SecretID pair and a confusing `permission denied` on every Vault login. Know which one you're rotating before you start.

---

## The three AppRoles

| AppRole | Used by | Credentials stored in | Rotate with |
|---|---|---|---|
| `ansible-local` | The operator's MacBook control node | 1Password `[Asgard] - Ansible - Vault - AppRole (ansible-local)` | `rotate-approle ansible-local` |
| `ansible-frigg` | The Frigg control-node watchtower | 1Password `[Asgard] - Ansible - Vault - AppRole (ansible-frigg)` | `rotate-approle ansible-frigg` |
| `ansible-awx` | Semaphore (in-cluster Ansible) | Vault KV `secret/k8s/semaphore/vault-approle` | `rotate-semaphore-approle` |

The `ansible-awx` name is historical — it was minted for an AWX deployment that Semaphore later replaced; Semaphore inherited the role. There is **no 1Password entry for it**, which is why `rotate-approle ansible-awx` fails with "unknown role": that helper is 1Password-canonical, and `ansible-awx` is the one exception.

No AppRole SecretID goes unwatched anymore — an active check runs on the existing twice-daily infra-health prober and posts a critical alert once any of the three drops inside 14 days of expiry, or has zero live accessors.

---

## Rotating `ansible-local` (workstation)

1. `rotate-approle ansible-local` — mints a fresh SecretID and writes it to the 1Password item.
2. Verify the next Ansible run against a Vault-backed role authenticates cleanly.

---

## Rotating `ansible-frigg` (control node)

1. `rotate-approle ansible-frigg` — same mint-and-verify flow as `ansible-local`, but the post-revoke step differs: Frigg doesn't read 1Password, so its new SecretID has to be re-propagated onto the box directly, via the `control-node:vault-env` tag on `asgard-control.yml`.
2. Confirm on Frigg itself that its env loader authenticates cleanly with the new SecretID.

---

## Rotating `ansible-awx` (Semaphore)

1. `rotate-semaphore-approle` — this helper mints a fresh SecretID, writes it to the Vault KV path, **and hash-verifies the stored value against the source** before proceeding.
2. `terraform apply` in `terraform/semaphore/` so Semaphore picks up the new credential.
3. Confirm a Semaphore drift-check authenticates (no `permission denied` on `/v1/auth/approle/login`).
4. Revoke the orphaned old SecretID.

---

## If you suspect a mismatch

The classic failure is a RoleID from one AppRole paired with a SecretID from the other. To find the authoritative RoleIDs and compare against what a consumer holds:

```
vault read -field=role_id auth/approle/role/ansible-local/role-id
vault read -field=role_id auth/approle/role/ansible-awx/role-id
```

Whichever RoleID matches the consumer's stored value tells you which AppRole that consumer actually uses — then make sure its SecretID came from the *same* role.

> **Why the hash-verify matters:** writing a SecretID through `vault kv patch <field>=-` (stdin) can silently store the wrong bytes — the length looks right but the value is corrupt. Always write with the value in a shell variable and hash-compare the read-back against the source. `rotate-semaphore-approle` does this for you.

---

## See also

- **Identity & secrets** (Components) — the three-store secrets model and how AppRole auth works.
- **Vault recovery** (Procedures) — for sealing/unsealing and token problems, a different failure class.
