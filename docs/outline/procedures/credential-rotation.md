<!-- docs/outline/procedures/credential-rotation.md -->

# Credential rotation

Every machine-facing credential in the homelab has a defined rotation path — where it lives, how to mint a replacement, and what else needs to know about the change. This is the reference for all of them. For the Vault AppRoles specifically, **AppRole rotation** covers the deep dive; this page gives the short version plus everything else.

> **Check every mirror, not just the obvious one.** Several of these credentials are cached in more than one place. `secret/ansible/frigg/iac-env` in particular mirrors the AWS, NetBox, Authentik, AdGuard, Cloudflare, and Semaphore credentials for Frigg's own env loader — nothing syncs it automatically when you rotate the "primary" copy. Skipping it doesn't break anything immediately; it just means Frigg keeps authenticating with the old value until something forces a refresh, which shows up later as a confusing, disconnected failure. Sync it as part of every rotation below, not as an afterthought.

---

## Vault root token

1Password item `[Bootstrap] - Manual - Vault - Root token`. Rotate with `rotate-vault-root-token` (either shell dialect) — an interactive helper, since the confirm-before-revoke step reads from the terminal. It mints a fresh **orphan** token, has the operator paste it into 1Password, hash-verifies the paste, proves the new token works live, then prompts before revoking the old one.

If there's no working root token to run that with, recovery is `vault operator generate-root` against a threshold of the Shamir recovery-key shares — no existing root token required, since this is Vault's designed unauthenticated bootstrap-recovery path.

---

## Vault AppRoles

Three roles: `ansible-local` (the operator's workstation), `ansible-frigg` (the control-node watchtower), `ansible-awx` (Semaphore — the name is historical, inherited from a planned AWX deployment Semaphore replaced). `ansible-local` and `ansible-frigg` rotate with `rotate-approle <role>`; `ansible-awx` has its own helper, `rotate-semaphore-approle`, since its credentials live in Vault KV rather than 1Password. Full detail, including the two-storage-conventions trap and the RoleID/SecretID mismatch failure mode: **AppRole rotation**.

---

## AWS (Terraform state access key)

The day-to-day `terraform-state` IAM identity is deliberately scoped to S3 only — it can't rotate its own keys. The key itself is a Terraform-managed resource, so rotation goes through Terraform using the **bootstrap** identity (the one with IAM rights):

```fish
cd terraform/aws
set-aws-creds bootstrap
terraform apply -replace=aws_iam_access_key.terraform_state
# review plan, confirm

terraform output -raw terraform_state_access_key_id; echo
terraform output -raw terraform_state_secret_access_key; echo
```

`-replace` destroys the old key and creates a new one atomically — no separate cleanup step. Paste the two output values into 1Password (`[Asgard] - Terraform - AWS - State access key`: `username` = access key id, `credential` = secret), then sync the rest:

```fish
printf '%s' "$(terraform output -raw terraform_state_secret_access_key)" | shasum -a 256 | cut -c1-16
printf '%s' (op read 'op://Homelab 2.0/jnvf6aokgml7vkjj4ho2xlcvua/credential') | shasum -a 256 | cut -c1-16
AWS_ACCESS_KEY_ID=$(terraform output -raw terraform_state_access_key_id) AWS_SECRET_ACCESS_KEY=$(terraform output -raw terraform_state_secret_access_key) aws sts get-caller-identity

cd ..
set-vault-token root
vault kv patch secret/ansible/frigg/iac-env aws_access_key_id=(op read 'op://Homelab 2.0/jnvf6aokgml7vkjj4ho2xlcvua/username') aws_secret_access_key=(op read 'op://Homelab 2.0/jnvf6aokgml7vkjj4ho2xlcvua/credential')
set-aws-creds state
```

The first two lines hash-verify the 1Password write landed; the `sts get-caller-identity` call confirms the new key actually authenticates before you swap back off the bootstrap identity.

---

## Cloudflare

There are three separate API tokens in the Cloudflare account, not one — check My Profile → API Tokens before rolling anything:

| Token | Scope | Where it lives |
|---|---|---|
| **Terraform Cloudflare** | Broad — Tunnel, DNS, WAF, Zone Settings, Bot Management | 1Password `[Asgard] - Terraform - Cloudflare - API token`, mirrored to `secret/ansible/cloudflare/api-token` and `secret/ansible/frigg/iac-env` |
| **cert-manager-niflheim-dns01** | Narrow — DNS-01 only, dedicated to cert-manager | `secret/k8s/cert-manager/cloudflare` only — no 1Password mirror |
| **Admin-read-all** | Broad, read-only | Not consumed by anything in the homelab |

Rotate the **Terraform Cloudflare** token for the routine case. Cloudflare's own token-verify endpoint depends on the token's prefix — `cfut_`-prefixed (User) tokens verify at `/client/v4/user/tokens/verify`, `cfat_`-prefixed (Account) tokens need `/client/v4/accounts/<id>/tokens/verify` instead. Both are valid; using the wrong endpoint reads as "invalid" for an otherwise-working token.

Never propagate the Terraform token's value into `secret/k8s/cert-manager/cloudflare` — that path belongs to the separate dedicated token.

---

## Authentik admin API token

API-driven, no UI needed. Mint a new one via `POST /api/v3/core/tokens/`, retrieve its key via `.../view_key/`, write it to 1Password (`[Asgard] - Terraform - Authentik - Admin API token`), confirm it authenticates, then delete the old identifier. Old and new stay valid side by side until the delete step, so there's no window where automation breaks mid-rotation.

---

## NetBox admin API token

NetBox's own token-create paths (REST API, and in practice the web UI too) don't reliably hand back a directly usable value in this deployment — the API masks it, and the reliable path is minting via Django shell against the running pod:

```fish
kubectl exec -n netbox deploy/netbox -c netbox -- /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell -c "
from users.models import User, Token
u = User.objects.get(username='admin')
t = Token.objects.create(user=u, description='homelab-env admin token')
print('KEY:', t.key)
print('TOKEN:', t.token)
"
```

The bearer credential is **three concatenated parts**, not just the printed `TOKEN` value: `nbt_<KEY>.<TOKEN>` — literal `nbt_` prefix, the `KEY` line, a literal `.`, then the `TOKEN` line. Paste that combined string into 1Password (`[Asgard] - Terraform - NetBox - Admin API token`).

---

## Semaphore API token

API-driven — `POST /user/tokens` returns the real secret in full (unlike NetBox). Worth a length sanity-check on whatever comes back (the working value is ~44 characters) before trusting it, since a masked value would otherwise look plausible. This is Semaphore's own UI-login token, separate from the `ansible-awx` AppRole it uses internally to run Ansible — rotating one doesn't touch the other.

---

## AdGuard admin password

The most involved one. AdGuard Home has no API-token concept — the credential is a real login, bcrypt-hashed into each of the three nodes' own config files by the `adguard` Ansible role. That role's config-render step is deliberately guarded to never touch an already-bootstrapped node's file (it protects the live DNS rewrites/filters/clients AdGuard writes back into the same file) — so pushing a new password hash to Vault and re-running the role is a silent no-op, not a rotation.

The working path is a surgical in-place edit of the `password:` line on each node, followed by a service restart:

```fish
set NEW_ADGUARD_PASSWORD (openssl rand -base64 24)
set NEW_ADGUARD_HASH (htpasswd -bnBC 10 "" "$NEW_ADGUARD_PASSWORD" | tr -d ':\n')
# ... write to Vault + 1Password (Adguard - admin), then per node:
ssh ansible@<node> "sudo sed -i 's|^    password: .*|    password: \"$NEW_ADGUARD_HASH\"|' /opt/AdGuardHome/AdGuardHome.yaml && sudo systemctl restart AdGuardHome.service"
```

Two things worth knowing before running this: the `sed` delimiter has to avoid `/`, since bcrypt hashes commonly contain it — use `|`. And verify by testing the actual login, never by comparing the file's hash against what's stored elsewhere — bcrypt salts randomly per invocation, so two independently-generated hashes of the identical password will essentially never match, which is expected, not a failure.

---

## See also

- **AppRole rotation** — the full deep dive on the Vault AppRole side of this page.
- **Identity & secrets** (Components) — the three-store secrets model these credentials all live inside.
- **Vault recovery** — for sealing/unsealing and token problems, a different failure class than routine rotation.
