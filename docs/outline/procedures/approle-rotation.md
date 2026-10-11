<!-- docs/outline/procedures/approle-rotation.md -->

# AppRole rotation

Vault AppRole **SecretIDs** are the machine credentials for Ansible and automation. They expire after 90 days and nothing but an alert tells you. Rotate on schedule, or at once if one leaked.

**Operator-only.** Needs a human at an interactive terminal (the helpers print values to paste and ask before revoking).
**Shell:** the `rotate-approle`, `seed-vault-approle`, `set-vault-token` and `homelab-env` helpers exist in both bash/zsh and fish. `rotate-semaphore-approle` exists **only in fish**.

---

## The three roles (do not mix them up)

| Role | Used by | Canonical store | Helper |
|---|---|---|---|
| `ansible-local` | The operator's workstation | 1Password: `[Asgard] - Ansible - Vault - AppRole (ansible-local)` (UUID `4srpqv2mt2vditxo7g5rqjquti`) | `rotate-approle ansible-local` |
| `ansible-frigg` | The control node | 1Password: `[Asgard] - Ansible - Vault - AppRole (ansible-frigg)` (UUID `kcmjziie2a75zk4qytdixrprpe`) | `rotate-approle ansible-frigg`, then re-propagate to Frigg |
| `ansible-awx` | Semaphore (the role pre-dates the AWX → Semaphore switch) | Vault KV only: `secret/k8s/semaphore/vault-approle` | `rotate-semaphore-approle` (**fish**) |

`rotate-approle ansible-awx` fails with "unknown role": the helper is 1Password-canonical and that role has no 1Password item. Running the wrong helper, or pasting one role's SecretID into another's slot, breaks that consumer's login until corrected.

**Field rule:** in the 1Password items, `username` is the **RoleID** and must **never** be edited; `password` (the SecretID), `secret_id_accessor` and `expires_at` are what change. A past rotation pasted the accessor into `username` and broke the next login.

## When is one due?

```bash
# Expiry dates are in the 1Password items; the Semaphore infra-health-check also alerts before expiry.
op item get 4srpqv2mt2vditxo7g5rqjquti --vault "Homelab 2.0" --fields expires_at
op item get kcmjziie2a75zk4qytdixrprpe --vault "Homelab 2.0" --fields expires_at
# Is a role's SecretID still valid right now?
vault read auth/approle/role/ansible-local/role-id >/dev/null && echo "role exists"
```

An `invalid role or secret ID` login error means the SecretID is stale or the wrong one was seeded (see Troubleshooting → Vault & Postgres).

---

## A. `ansible-local` (workstation)

```bash
set-vault-token root                    # mints VAULT_TOKEN from 1Password (operator only)
rotate-approle ansible-local            # mints a NEW SecretID and prints it + accessor + expiry
```

The helper snapshots the old accessor **before** minting, prints the new values, and waits. Then:

1. In 1Password item `4srpqv2mt2vditxo7g5rqjquti`, paste the new values into `password`, `secret_id_accessor` and `expires_at` (today + 90 days). **Do not touch `username`.**
2. Return to the helper: it hash-verifies what you pasted, and only then **revokes the old SecretID**.
3. Clean up and reload:

```bash
unset VAULT_TOKEN                        # never leave the root token in the environment
homelab-env --refresh                    # pick up the new SecretID
seed-vault-approle                       # rewrite the local secret-zero file so vault-homelab-env keeps working without 1Password
vault-homelab-env --refresh >/dev/null 2>&1
```

**If the rotation aborted** after you updated 1Password but before the revoke: Vault holds a SecretID that 1Password does not reference.

```bash
rotate-approle --fix ansible-local      # lists the role's accessors, offers to destroy any 1Password does not know
```

**Verify:**

```bash
set-vault-token approle
vault kv get -field=value secret/ansible/test/hello 2>/dev/null || echo "(no test secret seeded; token login itself succeeded)"
vault token lookup | grep -E "policies|ttl"
unset VAULT_TOKEN
```

## B. `ansible-frigg` (control node)

Same as A with the role name changed (`rotate-approle ansible-frigg`, paste into item `kcmjziie2a75zk4qytdixrprpe`). Frigg **does not read 1Password**, so after the revoke, push the new SecretID to it:

```bash
cd ~/homelab/ansible
ansible-playbook -i inventory/hosts.yml playbooks/asgard-control.yml \
  --tags control-node:vault-env -e control_node_frigg_secret_id="$(op read 'op://Homelab 2.0/kcmjziie2a75zk4qytdixrprpe/password')" -l frigg
```

(The SecretID is resolved inside the command; do not paste it literally.) Then on Frigg: `vault-homelab-env --refresh`.

## C. `ansible-awx` (Semaphore) — fish only

```fish
set-vault-token root
rotate-semaphore-approle
```

It mints a new SecretID, writes it to Vault KV (`secret/k8s/semaphore/vault-approle`), runs `terraform apply` in `terraform/semaphore/` to push it into Semaphore's environment, prompts you to validate with a drift-check run, then revokes the old SecretIDs. No 1Password involvement. After it finishes, run the drift check from **Everyday operations** section 8 and confirm it logs in to Vault.

---

## Why "90 days" really is 90

The AppRole auth mount's `max_lease_ttl` caps every role's SecretID TTL. Vault's 768 h default silently clamps a 90-day role to 32 days; the mount is tuned to 90 days in `terraform/vault/`. If a SecretID dies after about a month, check:

```bash
vault read sys/auth/approle/tune                                   # max_lease_ttl should be 2160h
vault read -field=secret_id_ttl auth/approle/role/ansible-local    # 7776000 (90 d)
```

SecretIDs are **never** in Terraform state, and the roles carry no source-address binding (callers' addresses change or are NAT'd).

---

## See also

- **Credential rotation** (this section): everything that is not an AppRole.
- **Vault recovery** (this section).
- **Vault & Postgres** (Troubleshooting): login failures.
