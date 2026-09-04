<!-- docs/outline/procedures/credential-rotation.md -->

# Credential rotation

Every machine-facing credential in the homelab has a defined rotation path — where it lives, how to mint a replacement, and what else needs to know about the change. This is the reference for all of them. For the Vault AppRoles specifically, **AppRole rotation** covers the deep dive; this page gives the short version plus everything else.

> **Check every mirror, not just the obvious one.** Several of these credentials are cached in more than one place. `secret/ansible/frigg/iac-env` in particular mirrors the AWS, NetBox, Authentik, AdGuard, Cloudflare, and Semaphore credentials for Frigg's own env loader — nothing syncs it automatically when you rotate the "primary" copy. Skipping it doesn't break anything immediately; it just means Frigg keeps authenticating with the old value until something forces a refresh, which shows up later as a confusing, disconnected failure. Sync it as part of every rotation below, not as an afterthought.

---

## Vault root token

1Password item `[Bootstrap] - Manual - Vault - Root token`. An interactive helper handles the whole thing — the confirm-before-revoke step reads from the terminal, so it can't run unattended:

```fish
rotate-vault-root-token
```

It mints a fresh **orphan** token, has the operator paste it into 1Password, hash-verifies the paste, proves the new token works live, then prompts before revoking the old one.

If there's no working root token to run that with, recovery is against a threshold of the Shamir recovery-key shares — no existing root token required, since this is Vault's designed unauthenticated bootstrap-recovery path:

```fish
vault operator generate-root -init
# submit recovery-key shares as prompted, then decode with the OTP it gives you
```

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

Rotate the **Terraform Cloudflare** token (the routine case) **from `https://dash.cloudflare.com/profile/api-tokens`** — the account-scoped API Tokens page mints a *different token type* for the same roll, not just a different URL. Rolling from the profile page produces a `cfut_`-prefixed **User** token; rolling from the account page produces a `cfat_`-prefixed **Account** token instead. The homelab's health check hardcodes the User-token verify endpoint, so use the profile page.

```fish
# roll it at https://dash.cloudflare.com/profile/api-tokens, paste the new
# value into 1Password (item: [Asgard] - Terraform - Cloudflare - API
# token, credential field), then:
set CF_TOKEN (op read "op://Homelab 2.0/ps4mc2hv7a777tzsef755te64m/credential")
```

Verify against the endpoint matching the token's prefix — using the wrong one reads as "invalid" for an otherwise-working token:
- `cfut_...` (User, from the profile page) → `https://api.cloudflare.com/client/v4/user/tokens/verify`
- `cfat_...` (Account, from the account page) → `https://api.cloudflare.com/client/v4/accounts/<id>/tokens/verify`

```fish
curl -s "https://api.cloudflare.com/client/v4/user/tokens/verify" -H "Authorization: Bearer $CF_TOKEN" | jq .
```

Propagate to every consumer (**not** `secret/k8s/cert-manager/cloudflare` — that path belongs to the separate dedicated token):

```fish
set-vault-token root
vault kv patch secret/ansible/cloudflare/api-token value="$CF_TOKEN"
vault kv patch secret/ansible/frigg/iac-env cloudflare_api_token="$CF_TOKEN"
printf '%s' "$CF_TOKEN" | shasum -a 256 | cut -c1-16
vault kv get -field=value secret/ansible/cloudflare/api-token | shasum -a 256 | cut -c1-16
vault kv get -field=cloudflare_api_token secret/ansible/frigg/iac-env | shasum -a 256 | cut -c1-16
set -e CF_TOKEN
```
Both hash lines should match the first one.

---

## Authentik admin API token

**1Password item: `[Asgard] - Terraform - Authentik - Admin API token`, UUID `4pxuhyvygrqqeo3vro24bjrhwa`.** API-driven, no UI needed. Old and new stay valid side by side until the delete step, so there's no window where automation breaks mid-rotation. The identifier is `authentik-bootstrap-token-<N>`, incrementing each rotation.

> **`$AUTHENTIK_TOKEN` *is* the credential this section rotates** — same 1Password field, cached by `homelab-env`. If it's already stale when you start, or a step fails and you keep going anyway, you can end up authenticating later calls with an already-deleted token, or writing a failed API response's `null` into 1Password as though it were real (both have happened). The block below checks after every step and **stops cold on the first failure** — don't skip a `STOP:` line, it means nothing further has been written.

```fish
# 0. preflight — confirm the token authenticating this sequence is alive
set PREFLIGHT (curl -s -o /dev/null -w '%{http_code}' "$AUTHENTIK_URL/api/v3/core/tokens/" -H "Authorization: Bearer $AUTHENTIK_TOKEN")
if test "$PREFLIGHT" != "200"
    echo "STOP: AUTHENTIK_TOKEN isn't authenticating (HTTP $PREFLIGHT). Refresh it and re-run this whole block."
else
    # 1. discover current + next identifier automatically — nothing to substitute
    set CUR_ID (curl -s "$AUTHENTIK_URL/api/v3/core/tokens/" -H "Authorization: Bearer $AUTHENTIK_TOKEN" | jq -r '[.results[] | select(.identifier | test("^authentik-bootstrap-token-[0-9]+$"))] | sort_by(.identifier | ltrimstr("authentik-bootstrap-token-") | tonumber) | last | .identifier')
    set NEW_ID "authentik-bootstrap-token-"(math (string replace 'authentik-bootstrap-token-' '' $CUR_ID) + 1)
    echo "rotating $CUR_ID -> $NEW_ID"

    # 2. create — verify it actually landed before trusting it.
    # jq -n (no -c) here would break: fish splits a command substitution's
    # output into a list on newlines, so a pretty-printed multi-line JSON
    # body arrives at -d as just its first line ("{"), and curl 400s on
    # "unexpected end of data". -c keeps it one line, one list element.
    set CREATE_ID (curl -s -X POST "$AUTHENTIK_URL/api/v3/core/tokens/" -H "Authorization: Bearer $AUTHENTIK_TOKEN" -H "Content-Type: application/json" -d (jq -nc --arg id "$NEW_ID" '{identifier:$id,user:6,intent:"api",expiring:false,description:"rotated"}') | jq -r '.identifier')
    if test "$CREATE_ID" != "$NEW_ID"
        echo "STOP: create failed (got '$CREATE_ID', expected '$NEW_ID'). Nothing written anywhere — investigate, then safe to retry from the top."
    else
        # 3. fetch the new key, sanity-check its shape before trusting it —
        # a failed view_key lookup renders as the literal string "null",
        # a plausible-looking but completely wrong value.
        set NEW_AUTHENTIK_TOKEN (curl -s "$AUTHENTIK_URL/api/v3/core/tokens/$NEW_ID/view_key/" -H "Authorization: Bearer $AUTHENTIK_TOKEN" | jq -r .key)
        if test (string length "$NEW_AUTHENTIK_TOKEN") -lt 20
            echo "STOP: fetched key looks wrong (too short to be real). NOT writing to 1Password. The new token record '$NEW_ID' may need manual cleanup in Authentik."
        else
            op item edit '4pxuhyvygrqqeo3vro24bjrhwa' --vault "Homelab 2.0" "credential=$NEW_AUTHENTIK_TOKEN"
            printf '%s' "$NEW_AUTHENTIK_TOKEN" | shasum -a 256 | cut -c1-16
            printf '%s' (op read 'op://Homelab 2.0/4pxuhyvygrqqeo3vro24bjrhwa/credential') | shasum -a 256 | cut -c1-16
            curl -s "$AUTHENTIK_URL/api/v3/core/tokens/" -H "Authorization: Bearer $NEW_AUTHENTIK_TOKEN" | jq '.results | length'

            curl -s -X DELETE "$AUTHENTIK_URL/api/v3/core/tokens/$CUR_ID/" -H "Authorization: Bearer $NEW_AUTHENTIK_TOKEN"
            set-vault-token root
            vault kv patch secret/ansible/frigg/iac-env authentik_token="$NEW_AUTHENTIK_TOKEN"
        end
    end
end
set -e NEW_AUTHENTIK_TOKEN CUR_ID NEW_ID CREATE_ID PREFLIGHT
```
The hash-compare and the `jq '.results | length'` call both prove the new token is genuinely live and correctly stored before the old one gets deleted.

**If Authentik ever ends up with no working credential at all** — mint one directly against the database, bypassing the API entirely, same escape hatch as NetBox's Django shell:

```fish
kubectl exec -n authentik <authentik-server-pod> -- ak shell -c "
from authentik.core.models import Token, User
u = User.objects.get(username='akadmin')
t = Token.objects.create(identifier='authentik-bootstrap-token-<next-N>', user=u, intent='api', expiring=False, description='recovered via django shell')
print('TOKEN:', t.key)
"
```
Run this yourself, not via an assistant's own tool call — the printed value should only touch your own screen. Paste it into 1Password by hand (GUI, not `op item edit`) afterward.

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

The bearer credential is **three concatenated parts**, not just the printed `TOKEN` value: `nbt_<KEY>.<TOKEN>` — literal `nbt_` prefix, the `KEY` line, a literal `.`, then the `TOKEN` line. Paste that combined string into **1Password item `[Asgard] - Terraform - NetBox - Admin API token`, UUID `lsqb4z5mbeijeqbxx43y5pkl5q`**, `credential` field, then:

```fish
set NEW_NETBOX_TOKEN (op read "op://Homelab 2.0/lsqb4z5mbeijeqbxx43y5pkl5q/credential")
curl -s "$NETBOX_SERVER_URL/api/users/tokens/" -H "Authorization: Bearer $NEW_NETBOX_TOKEN" | jq '.count'
# should return a number — confirms the reconstruction was correct

set-vault-token root
vault kv patch secret/ansible/frigg/iac-env netbox_api_token="$NEW_NETBOX_TOKEN"

# find + delete the old token's id (via the API, with the still-valid old token)
curl -s "$NETBOX_SERVER_URL/api/users/tokens/" -H "Authorization: Bearer $NETBOX_API_TOKEN" | jq -r '.results[] | "\(.id) \(.description)"'
curl -s -X DELETE "$NETBOX_SERVER_URL/api/users/tokens/<OLD_ID>/" -H "Authorization: Bearer $NETBOX_API_TOKEN"
set -e NEW_NETBOX_TOKEN
```

---

## Semaphore API token

**1Password item: `[Asgard] - Terraform - Semaphore - Admin API token`, UUID `24fmbstdhqzwk6eeru4vvaixsm`.** API-driven, and (unlike NetBox) its create response **does** return the real secret in full — worth a length sanity-check on whatever comes back (the working value is ~44 characters) before trusting it, since a masked value would otherwise look plausible.

```fish
# capture the current id automatically — nothing to copy by hand
set OLD_ID (curl -s "$SEMAPHOREUI_API_BASE_URL/user/tokens" -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN" | jq -r '.[0].id')

set NEW_TOKEN_JSON (curl -s -X POST "$SEMAPHOREUI_API_BASE_URL/user/tokens" -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN")
set NEW_SEMAPHORE_TOKEN (echo $NEW_TOKEN_JSON | jq -r .id)
set -e NEW_TOKEN_JSON
printf '%s' "$NEW_SEMAPHORE_TOKEN" | wc -c   # sanity: should be ~44, not a short masked value

op item edit '24fmbstdhqzwk6eeru4vvaixsm' --vault "Homelab 2.0" "credential=$NEW_SEMAPHORE_TOKEN"
printf '%s' "$NEW_SEMAPHORE_TOKEN" | shasum -a 256 | cut -c1-16
printf '%s' (op read 'op://Homelab 2.0/24fmbstdhqzwk6eeru4vvaixsm/credential') | shasum -a 256 | cut -c1-16

set-vault-token root
vault kv patch secret/ansible/frigg/iac-env semaphore_api_token="$NEW_SEMAPHORE_TOKEN"
curl -s -X DELETE "$SEMAPHOREUI_API_BASE_URL/user/tokens/$OLD_ID" -H "Authorization: Bearer $NEW_SEMAPHORE_TOKEN"
set -e NEW_SEMAPHORE_TOKEN OLD_ID
```
`$OLD_ID` grabs `.[0]` assuming there's normally exactly one Semaphore token live — if there's ever more than one, list them first (`jq -r '.[] | "\(.id) \(.name)"'`) and delete deliberately instead.

This is Semaphore's own UI-login token, separate from the `ansible-awx` AppRole it uses internally to run Ansible — rotating one doesn't touch the other.

---

## AdGuard admin password

**1Password item: `[Asgard] - Terraform - AdGuard - Admin login`, UUID `hvh3d7hlivcsbjqqye34f3d7a4`** (`username` = `ghost`, not the role default `admin`; `password` is what rotates). The most involved one. AdGuard Home has no API-token concept — the credential is a real login, bcrypt-hashed into each of the three nodes' own config files by the `adguard` Ansible role. That role's config-render step is deliberately guarded to never touch an already-bootstrapped node's file (it protects the live DNS rewrites/filters/clients AdGuard writes back into the same file) — so pushing a new password hash to Vault and re-running the role is a silent no-op, not a rotation.

The working path is a surgical in-place edit of the `password:` line on each node, followed by a service restart:

```fish
set NEW_ADGUARD_PASSWORD (openssl rand -base64 24)
set NEW_ADGUARD_HASH (htpasswd -bnBC 10 "" "$NEW_ADGUARD_PASSWORD" | tr -d ':\n')

set-vault-token root
vault kv patch secret/ansible/adguard/admin-password-hash hash="$NEW_ADGUARD_HASH"
vault kv patch secret/ansible/adguardhome-sync/admin-password password="$NEW_ADGUARD_PASSWORD"
vault kv patch secret/ansible/frigg/iac-env adguard_password="$NEW_ADGUARD_PASSWORD"
op item edit 'hvh3d7hlivcsbjqqye34f3d7a4' --vault "Homelab 2.0" "password=$NEW_ADGUARD_PASSWORD"
printf '%s' "$NEW_ADGUARD_PASSWORD" | shasum -a 256 | cut -c1-16
printf '%s' (op read 'op://Homelab 2.0/hvh3d7hlivcsbjqqye34f3d7a4/password') | shasum -a 256 | cut -c1-16

for h in 10.0.11.201 10.0.11.202 10.0.11.203
    ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE ansible@$h "sudo cp /opt/AdGuardHome/AdGuardHome.yaml /opt/AdGuardHome/AdGuardHome.yaml.bak-pw-rotate-<date> && sudo sed -i 's|^    password: .*|    password: \"$NEW_ADGUARD_HASH\"|' /opt/AdGuardHome/AdGuardHome.yaml && sudo systemctl restart AdGuardHome.service"
    echo "$h: $status"
end
```

**Delimiter must be `|`, not `/`** — bcrypt hashes commonly contain literal `/`.

Verify by testing the actual login, never by comparing the file's hash to what's stored elsewhere — bcrypt salts randomly per invocation, so two independently-generated hashes of the identical password will essentially never match, and that's expected, not a failure:

```fish
curl -s -X POST "http://10.0.11.201/control/login" -H "Content-Type: application/json" -d "{\"name\":\"$ADGUARD_USERNAME\",\"password\":\"$NEW_ADGUARD_PASSWORD\"}" -i | head -1
curl -s -X POST "http://10.0.11.202/control/login" -H "Content-Type: application/json" -d "{\"name\":\"$ADGUARD_USERNAME\",\"password\":\"$NEW_ADGUARD_PASSWORD\"}" -i | head -1
curl -s -X POST "http://10.0.11.203/control/login" -H "Content-Type: application/json" -d "{\"name\":\"$ADGUARD_USERNAME\",\"password\":\"$NEW_ADGUARD_PASSWORD\"}" -i | head -1
curl -s -o /dev/null -w "%{http_code}\n" https://smoketest.niflheim.xiiisins.com/anything
set -e NEW_ADGUARD_PASSWORD NEW_ADGUARD_HASH
```
All four should be `200`.

---

## See also

- **AppRole rotation** — the full deep dive on the Vault AppRole side of this page.
- **Identity & secrets** (Components) — the three-store secrets model these credentials all live inside.
- **Vault recovery** — for sealing/unsealing and token problems, a different failure class than routine rotation.
