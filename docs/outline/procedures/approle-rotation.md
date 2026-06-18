<!-- docs/outline/procedures/approle-rotation.md -->

# AppRole rotation

Rotating the Vault AppRole SecretIDs that machines use to authenticate. Run this as routine credential hygiene, or immediately if a SecretID is exposed.

> **The one thing to get right:** there are **two AppRoles** with **two storage conventions** and **two rotation helpers**. Conflating them produces a mismatched RoleID/SecretID pair and a confusing `permission denied` on every Vault login. Know which one you're rotating before you start.

---

## The two AppRoles

| AppRole | Used by | Credentials stored in | Rotate with |
|---|---|---|---|
| `ansible-local` | The operator's MacBook control node | 1Password item `Ansible - Vault - k3s` | `rotate-approle ansible-local` |
| `ansible-awx` | Semaphore (in-cluster Ansible) | Vault KV `secret/k8s/semaphore/vault-approle` | `rotate-semaphore-approle` |

The names are historical — `ansible-awx` was minted for an AWX deployment that Semaphore later replaced; Semaphore inherited the role. There is **no 1Password entry for `ansible-awx`**, which is why `rotate-approle ansible-awx` fails with "unknown role": that helper is 1Password-canonical.

---

## Rotating `ansible-local` (workstation)

1. `rotate-approle ansible-local` — mints a fresh SecretID and writes it to the 1Password item.
2. Verify the next Ansible run against a Vault-backed role authenticates cleanly.

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
