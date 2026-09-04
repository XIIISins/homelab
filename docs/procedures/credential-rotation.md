<!-- docs/procedures/credential-rotation.md -->

# Credential rotation

Step-by-step rotation for every homelab-env credential type. Written up 2026-09-04 after two same-week transcript-leak incidents forced rotating all eight in quick succession — see [`../incidents/2026-09-03-claude-transcript-secret-leak.md`](../incidents/2026-09-03-claude-transcript-secret-leak.md) for the narrative and the gotchas each one surfaced. This doc is the reusable "what to run" reference; `docs/known-issues/` carries the "why it's shaped this way."

## Ground rules for every rotation below

- **Never let a raw secret value pass through a Claude tool call's output.** Capture into a shell variable, hash-compare (`shasum -a 256 | cut -c1-16`) to verify a write landed, `set -e`/`unset` when done. If a step *must* print the real value (a create-once API response, a Django-shell result), the operator runs it directly — Claude hands over the command, not the execution.
- **`op item edit` can report success while silently not writing the value** (confirmed live, twice). Every 1Password write below is followed by an independent `op read` + hash-compare before anything downstream (deleting an old credential, etc.) happens. If the hashes don't match, paste into the 1Password **GUI** instead of retrying the CLI.
- **Check every mirror, not just the "primary" one.** Several of these credentials live in more places than the obvious 1Password item — `secret/ansible/frigg/iac-env` in particular mirrors AWS, NetBox, Authentik, AdGuard, Cloudflare, and Semaphore's credentials for `vault-homelab-env` (Frigg's own env loader, and Claude's), and **no rotation helper syncs it automatically**. Skipping it doesn't break anything immediately — it just means Frigg/`vault-homelab-env` keeps authenticating with the old credential until it independently expires or gets revoked, which surfaces later as a confusing, disconnected failure. Sync it as part of every rotation below, not as an afterthought.
- **After rotating `ansible-local`'s AppRole specifically**, re-run `homelab-env --refresh && seed-vault-approle` (own machine) so `vault-homelab-env` keeps working — see the "two AppRoles" and `seed-vault-approle` gotchas in `known-issues/vault.md`.

## Vault root token

**1Password item: `[Bootstrap] - Manual - Vault - Root token`, UUID `7g4grolyien2yqkm7me2jficmy`** (renamed from `Asgard - Vault - Root Token` during the S1 reorg — always reference by UUID). Interactive-only (needs a real terminal for the confirm-before-revoke prompt): `rotate-vault-root-token` in either shim dialect. Full mechanism + the `op item edit` silent-fail history: `known-issues/vault.md`. Recovery path if it goes wrong: `vault operator generate-root` (documented inline in `2026-09-03-vault-root-token-recovery.md`; a standalone runbook is still an open item).

## Vault AppRole (`ansible-local`, `ansible-frigg`, `ansible-awx`)

- `ansible-local`: **1Password item `[Asgard] - Ansible - Vault - AppRole (ansible-local)`, UUID `4srpqv2mt2vditxo7g5rqjquti`.** `rotate-approle ansible-local` — prints the new SecretID for the operator to paste into that item, hash-verifies, prompts before revoking the old one. **Only `username` = RoleID, never touch it; `password`/`secret_id_accessor`/`expires_at` are what change.**
- `ansible-frigg`: **1Password item `[Asgard] - Ansible - Vault - AppRole (ansible-frigg)`, UUID `kcmjziie2a75zk4qytdixrprpe`.** `rotate-approle ansible-frigg` — same pattern, but its post-revoke step is different: Frigg doesn't read 1P, so also re-propagate via `ansible-playbook ... asgard-control.yml --tags control-node:vault-env -e control_node_frigg_secret_id=<new-secret-id> -l frigg`.
- `ansible-awx` (Semaphore's role): **no 1Password item — Vault KV only**, `secret/k8s/semaphore/vault-approle`. Separate helper, `rotate-semaphore-approle` — writes straight there, then `terraform apply` in `terraform/semaphore/` propagates it.
- Full detail on why these are two different helpers, the mount `max_lease_ttl` clamp, and the `username`-vs-`role_id` footgun: `known-issues/vault.md`.

## AWS (`terraform-state` IAM user's access key)

The `terraform-state` identity is deliberately S3-only — it **cannot** rotate its own keys (`AccessDenied` on `iam:ListAccessKeys`). The key is a Terraform-managed resource; rotate via `-replace` using the **bootstrap** identity, which is the one with IAM rights:

```fish
cd terraform/aws
set-aws-creds bootstrap
terraform apply -replace=aws_iam_access_key.terraform_state
# review plan, confirm

terraform output -raw terraform_state_access_key_id; echo
terraform output -raw terraform_state_secret_access_key; echo
```
**1Password item: `[Asgard] - Terraform - AWS - State access key`, UUID `jnvf6aokgml7vkjj4ho2xlcvua`.** Paste both values in: `username` = access key id, `credential` = secret. `-replace` destroys-then-creates atomically — no separate old-key cleanup.

```fish
# item: [Asgard] - Terraform - AWS - State access key (jnvf6aokgml7vkjj4ho2xlcvua)
printf '%s' "$(terraform output -raw terraform_state_secret_access_key)" | shasum -a 256 | cut -c1-16
printf '%s' (op read 'op://Homelab 2.0/jnvf6aokgml7vkjj4ho2xlcvua/credential') | shasum -a 256 | cut -c1-16
AWS_ACCESS_KEY_ID=$(terraform output -raw terraform_state_access_key_id) AWS_SECRET_ACCESS_KEY=$(terraform output -raw terraform_state_secret_access_key) aws sts get-caller-identity

cd ..
set-vault-token root
vault kv patch secret/ansible/frigg/iac-env aws_access_key_id=(op read 'op://Homelab 2.0/jnvf6aokgml7vkjj4ho2xlcvua/username') aws_secret_access_key=(op read 'op://Homelab 2.0/jnvf6aokgml7vkjj4ho2xlcvua/credential')
set-aws-creds state
```

## Cloudflare

**There are (at least) three separate Cloudflare API tokens in the account**, not one — check the dashboard (My Profile → API Tokens) before assuming which one you're rolling:
- **"Terraform Cloudflare"** (Cloudflare dashboard name) — broad scope (Tunnel, DNS, WAF, Zone Settings, Bot Management). This is the one `CLOUDFLARE_API_TOKEN` refers to, mirrored to **1Password item `[Asgard] - Terraform - Cloudflare - API token`, UUID `ps4mc2hv7a777tzsef755te64m`**.
- **"cert-manager-niflheim-dns01"** (Cloudflare dashboard name) — narrow, DNS-01-only, dedicated to cert-manager. **Separate from the Terraform token.** Its only home is `secret/k8s/cert-manager/cloudflare` (no 1Password mirror) — do not overwrite that path with the Terraform token's value (this happened once; recovered via `vault kv rollback -version=1`, since Vault keeps KV version history by default and nothing else does).
- **"Admin-read-all"** (Cloudflare dashboard name) — not consumed by anything in this repo as far as traced. No 1Password mirror.

Rotate the **Terraform Cloudflare** token (the common case) **from `https://dash.cloudflare.com/profile/api-tokens` — the account-scoped page (`https://dash.cloudflare.com/<account_id>/api-tokens`) mints a different token *type* for the same roll, not just a different URL.** Rolling from the profile page produces a `cfut_`-prefixed **User** token; rolling from the account page produces a `cfat_`-prefixed **Account** token. `infra-health-check.yml`'s Cloudflare check hardcodes the `user` verify endpoint (below), so use the profile page unless you have a specific reason to want an Account token.

```fish
# roll "Terraform Cloudflare" at https://dash.cloudflare.com/profile/api-tokens
# (NOT the account-level API Tokens page), paste the new value into
# 1Password item: [Asgard] - Terraform - Cloudflare - API token
# (ps4mc2hv7a777tzsef755te64m), credential field
set CF_TOKEN (op read "op://Homelab 2.0/ps4mc2hv7a777tzsef755te64m/credential")
```

**Verify against the correct endpoint — it depends on the token's prefix**, and using the wrong one produces a misleading `Invalid API Token` for an otherwise-valid token:
- `cfut_...` (User token, from the profile page) → `https://api.cloudflare.com/client/v4/user/tokens/verify`
- `cfat_...` (Account token, from the account page) → `https://api.cloudflare.com/client/v4/accounts/<account_id>/tokens/verify`

```fish
curl -s "https://api.cloudflare.com/client/v4/user/tokens/verify" -H "Authorization: Bearer $CF_TOKEN" | jq .
```

Propagate to every consumer (**not** `secret/k8s/cert-manager/cloudflare` — that's the separate dedicated token):

```fish
set-vault-token root
vault kv patch secret/ansible/cloudflare/api-token value="$CF_TOKEN"
vault kv patch secret/ansible/frigg/iac-env cloudflare_api_token="$CF_TOKEN"
printf '%s' "$CF_TOKEN" | shasum -a 256 | cut -c1-16
vault kv get -field=value secret/ansible/cloudflare/api-token | shasum -a 256 | cut -c1-16
vault kv get -field=cloudflare_api_token secret/ansible/frigg/iac-env | shasum -a 256 | cut -c1-16
set -e CF_TOKEN
```

## Authentik admin API token

**1Password item: `[Asgard] - Terraform - Authentik - Admin API token`, UUID `4pxuhyvygrqqeo3vro24bjrhwa`.** API-driven, no UI needed. Old-and-new both valid during the safety window (delete last). The identifier is `authentik-bootstrap-token-<N>`, N incrementing each rotation.

> **`$AUTHENTIK_TOKEN` *is* the credential this section rotates** — it's the exact same 1Password field, cached into your shell by `homelab-env`. That has a sharp edge: if it's already stale when you start (a leftover from a previous rotation), or if any step below fails and you keep going anyway, you can end up authenticating later calls with a token that's already been deleted, or writing a failed API response's `null` straight into 1Password as though it were a real credential (both have happened — surfaced 2026-09-05). The block below **checks after every step and stops cold on the first failure** rather than cascading — don't skip a `STOP:` line, it means nothing further has been written.

```fish
# 0. preflight — confirm the token authenticating this whole sequence is
# actually alive before touching anything. If this fails, run
# `homelab-env --refresh` (or read the credential fresh from 1Password)
# and re-paste this whole block — don't proceed past a STOP line.
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
        # this is what would have caught the "null" poisoning: a failed
        # view_key lookup renders as the literal 4-character string "null",
        # which is a plausible-looking but completely wrong value.
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

**If Authentik ever ends up with no working credential at all** (env stale, 1Password poisoned, no API token authenticates) — mint one directly against the database, bypassing the API entirely, same escape hatch as NetBox's Django shell:

```fish
kubectl exec -n authentik <authentik-server-pod> -- ak shell -c "
from authentik.core.models import Token, User
u = User.objects.get(username='akadmin')
t = Token.objects.create(identifier='authentik-bootstrap-token-<next-N>', user=u, intent='api', expiring=False, description='recovered via django shell')
print('TOKEN:', t.key)
"
```
Run this yourself, not via an assistant's own tool call — the printed value should only ever touch your own screen. Paste it into 1Password by hand (GUI, not `op item edit`) afterward.

## NetBox admin API token

**Neither the REST API's create response nor the web UI's "Add token" reliably hand you a usable value** in this deployment — see `known-issues/netbox.md` for why. The proven path is Django shell, and **the full credential is three concatenated parts**: `nbt_<key>.<token>` — not just the `.token` field alone.

```fish
kubectl exec -n netbox deploy/netbox -c netbox -- /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell -c "
from users.models import User, Token
u = User.objects.get(username='admin')
t = Token.objects.create(user=u, description='homelab-env admin token (rotated <date>)')
print('KEY:', t.key)
print('TOKEN:', t.token)
"
```
Construct the real value yourself: `nbt_<KEY>.<TOKEN>` (literal `nbt_` + the `KEY` line + a literal `.` + the `TOKEN` line). Paste that combined string into **1Password item `[Asgard] - Terraform - NetBox - Admin API token`, UUID `lsqb4z5mbeijeqbxx43y5pkl5q`**, `credential` field, then:

```fish
# item: [Asgard] - Terraform - NetBox - Admin API token (lsqb4z5mbeijeqbxx43y5pkl5q)
set NEW_NETBOX_TOKEN (op read "op://Homelab 2.0/lsqb4z5mbeijeqbxx43y5pkl5q/credential")
curl -s "$NETBOX_SERVER_URL/api/users/tokens/" -H "Authorization: Bearer $NEW_NETBOX_TOKEN" | jq '.count'
# should return a number — that confirms the reconstruction was correct

set-vault-token root
vault kv patch secret/ansible/frigg/iac-env netbox_api_token="$NEW_NETBOX_TOKEN"

# find + delete the old token's id first (via the API, with the still-valid old token)
curl -s "$NETBOX_SERVER_URL/api/users/tokens/" -H "Authorization: Bearer $NETBOX_API_TOKEN" | jq -r '.results[] | "\(.id) \(.description)"'
curl -s -X DELETE "$NETBOX_SERVER_URL/api/users/tokens/<OLD_ID>/" -H "Authorization: Bearer $NETBOX_API_TOKEN"
set -e NEW_NETBOX_TOKEN
```

## Semaphore API token

**1Password item: `[Asgard] - Terraform - Semaphore - Admin API token`, UUID `24fmbstdhqzwk6eeru4vvaixsm`.** API-driven, and (unlike NetBox) its create response **does** return the real secret in full — confirm by checking the length before trusting it (44 chars observed; NetBox's masked values were 12).

```fish
# capture the current id automatically — nothing to copy by hand
set OLD_ID (curl -s "$SEMAPHOREUI_API_BASE_URL/user/tokens" -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN" | jq -r '.[0].id')

set NEW_TOKEN_JSON (curl -s -X POST "$SEMAPHOREUI_API_BASE_URL/user/tokens" -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN")
set NEW_SEMAPHORE_TOKEN (echo $NEW_TOKEN_JSON | jq -r .id)
set -e NEW_TOKEN_JSON
printf '%s' "$NEW_SEMAPHORE_TOKEN" | wc -c   # sanity: should be ~44, not a short masked value

# item: [Asgard] - Terraform - Semaphore - Admin API token (24fmbstdhqzwk6eeru4vvaixsm)
op item edit '24fmbstdhqzwk6eeru4vvaixsm' --vault "Homelab 2.0" "credential=$NEW_SEMAPHORE_TOKEN"
printf '%s' "$NEW_SEMAPHORE_TOKEN" | shasum -a 256 | cut -c1-16
printf '%s' (op read 'op://Homelab 2.0/24fmbstdhqzwk6eeru4vvaixsm/credential') | shasum -a 256 | cut -c1-16

set-vault-token root
vault kv patch secret/ansible/frigg/iac-env semaphore_api_token="$NEW_SEMAPHORE_TOKEN"
curl -s -X DELETE "$SEMAPHOREUI_API_BASE_URL/user/tokens/$OLD_ID" -H "Authorization: Bearer $NEW_SEMAPHORE_TOKEN"
set -e NEW_SEMAPHORE_TOKEN OLD_ID
```
`$OLD_ID` grabs `.[0]` on the assumption there's normally exactly one Semaphore token live — if there's ever more than one, list them first (`jq -r '.[] | "\(.id) \(.name)"'`) and delete deliberately instead.
Note: this is Semaphore's own UI-login API token — distinct from the `ansible-awx` Vault AppRole Semaphore uses internally to run Ansible (see the Vault AppRole section above; rotating one does not rotate the other).

## AdGuard admin password

**1Password item: `[Asgard] - Terraform - AdGuard - Admin login`, UUID `hvh3d7hlivcsbjqqye34f3d7a4`** (`username` = `ghost`, not the role default `admin`; `password` is what rotates). The most involved one — AGH has no API-token concept, and the `adguard` role's config-render is guarded to never touch an already-bootstrapped node's file (protects live rewrites/filters/clients). A plain role re-run + Vault write is a **silent no-op**. Full detail on why, plus the `sed` delimiter and bcrypt-hash-compare gotchas: `known-issues/dns-adguard.md`. Condensed runbook:

```fish
set NEW_ADGUARD_PASSWORD (openssl rand -base64 24)
set NEW_ADGUARD_HASH (htpasswd -bnBC 10 "" "$NEW_ADGUARD_PASSWORD" | tr -d ':\n')

set-vault-token root
vault kv patch secret/ansible/adguard/admin-password-hash hash="$NEW_ADGUARD_HASH"
vault kv patch secret/ansible/adguardhome-sync/admin-password password="$NEW_ADGUARD_PASSWORD"
vault kv patch secret/ansible/frigg/iac-env adguard_password="$NEW_ADGUARD_PASSWORD"
# item: [Asgard] - Terraform - AdGuard - Admin login (hvh3d7hlivcsbjqqye34f3d7a4)
op item edit 'hvh3d7hlivcsbjqqye34f3d7a4' --vault "Homelab 2.0" "password=$NEW_ADGUARD_PASSWORD"
printf '%s' "$NEW_ADGUARD_PASSWORD" | shasum -a 256 | cut -c1-16
printf '%s' (op read 'op://Homelab 2.0/hvh3d7hlivcsbjqqye34f3d7a4/password') | shasum -a 256 | cut -c1-16

for h in 10.0.11.201 10.0.11.202 10.0.11.203
    ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE ansible@$h "sudo cp /opt/AdGuardHome/AdGuardHome.yaml /opt/AdGuardHome/AdGuardHome.yaml.bak-pw-rotate-<date> && sudo sed -i 's|^    password: .*|    password: \"$NEW_ADGUARD_HASH\"|' /opt/AdGuardHome/AdGuardHome.yaml && sudo systemctl restart AdGuardHome.service"
    echo "$h: $status"
end
```
**Delimiter must be `|`, not `/`** — bcrypt hashes commonly contain literal `/`.

**Verify by testing login, never by comparing the file's hash to Vault's** — bcrypt salts randomly per invocation, so two independently-generated hashes of the identical password will essentially never match, and that's expected, not a failure:

```fish
curl -s -X POST "http://10.0.11.201/control/login" -H "Content-Type: application/json" -d "{\"name\":\"$ADGUARD_USERNAME\",\"password\":\"$NEW_ADGUARD_PASSWORD\"}" -i | head -1
curl -s -X POST "http://10.0.11.202/control/login" -H "Content-Type: application/json" -d "{\"name\":\"$ADGUARD_USERNAME\",\"password\":\"$NEW_ADGUARD_PASSWORD\"}" -i | head -1
curl -s -X POST "http://10.0.11.203/control/login" -H "Content-Type: application/json" -d "{\"name\":\"$ADGUARD_USERNAME\",\"password\":\"$NEW_ADGUARD_PASSWORD\"}" -i | head -1
curl -s -o /dev/null -w "%{http_code}\n" https://smoketest.niflheim.xiiisins.com/anything
set -e NEW_ADGUARD_PASSWORD NEW_ADGUARD_HASH
```
All four should be `200`.

## After any rotation involving `frigg/iac-env`

Frigg and any `vault-homelab-env` session (including Claude's own, per `CLAUDE.md`'s guidance to prefer the Vault-backed shim) won't pick up the new value until their local 3h cache expires or is force-refreshed:
```fish
vault-homelab-env --refresh
```
Run this **with full output redirection** (`>/dev/null 2>&1` at minimum) if a Claude session is the one running it — this exact command's cache-write step is the mechanism behind both 2026-09-03/04 transcript leaks.
