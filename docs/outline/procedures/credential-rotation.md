<!-- docs/outline/procedures/credential-rotation.md -->

# Credential rotation

Step-by-step rotation for each credential the automation uses. **Operator-only**: the helpers need an interactive terminal, the 1Password CLI signed in and, for several, the Vault root token. The blocks below are **fish** (the proven form); AppRoles have their own page (**AppRole rotation**).

**Use when:** a credential leaked, appeared in a transcript or log, or is due. If a secret leaked: tell the owner, recommend rotation, and do not rotate automatically.

---

## Ground rules for every rotation

1. **Never let a raw secret value reach a transcript.** Capture into a shell variable, verify by comparing short hashes (`shasum -a 256 | cut -c1-16`), `set -e` the variable when done. If a step must print the real value (a create-once API response), the operator runs it directly.
2. **`op item edit` can report success and write nothing** (confirmed twice). Every 1Password write is followed by an independent `op read` and a hash compare **before** anything downstream (such as deleting the old credential). If the hashes differ, paste into the 1Password **app** instead of retrying the CLI.
3. **Sync every mirror, not just the primary.** `secret/ansible/frigg/iac-env` mirrors AWS, NetBox, Authentik, AdGuard, Cloudflare and Semaphore credentials for the control node's environment loader, and **no helper updates it**. Skipping it breaks nothing immediately and shows up days later as an unrelated failure. Sync it as part of every rotation below. (The Semaphore `infra-health-check` catches a missed sync for five of the six fields; AWS has no automated check.)
4. **Overlap, then revoke.** Mint the new one, prove it works, propagate it, and only then delete the old one. Every block below stops at the first failure (`STOP:`): do not continue past a `STOP:` line.
5. After any rotation touching `frigg/iac-env`, refresh the three-hour environment cache everywhere (section 9).

Prepare the shell (fish): `homelab-env` (1Password-backed), `set-vault-token root` where a block says so, and `unset`/`set -e` the token when done.

---

## 1. Vault root token

1Password item `[Bootstrap] - Manual - Vault - Root token` (UUID `7g4grolyien2yqkm7me2jficmy`). Reference it by UUID.

```fish
rotate-vault-root-token
```

Interactive only. It mints a fresh **orphan** token (a child token would be revoked with its parent), has you paste it into 1Password, hash-verifies the paste, and only then revokes the old token. Verify:

```fish
vault token lookup                      # shows the new token; ttl and policies [root]
```

If it goes wrong and you have no root token: `vault operator generate-root` (needs the recovery keys from 1Password; see **Vault recovery**).

---

## 2. AWS state key (`terraform-state` IAM user)

The state identity is deliberately S3-only and cannot rotate its own key; rotate with the **bootstrap** identity. 1Password item `[Asgard] - Terraform - AWS - State access key` (UUID `jnvf6aokgml7vkjj4ho2xlcvua`; `username` = key id, `credential` = secret).

```fish
cd terraform/aws
set-aws-creds bootstrap
terraform apply -replace=aws_iam_access_key.terraform_state
# review the plan, confirm. -replace destroys and creates atomically; no separate old-key cleanup
terraform output -raw terraform_state_access_key_id; echo
terraform output -raw terraform_state_secret_access_key; echo
```

Paste both values into the 1Password item, then verify and propagate:

```fish
set TF_HASH (printf '%s' "$(terraform output -raw terraform_state_secret_access_key)" | shasum -a 256 | cut -c1-16)
set OP_HASH (printf '%s' (op read 'op://Homelab 2.0/jnvf6aokgml7vkjj4ho2xlcvua/credential') | shasum -a 256 | cut -c1-16)
echo "terraform: $TF_HASH  1Password: $OP_HASH"
if test "$TF_HASH" != "$OP_HASH"
    cd ..
    echo "STOP: 1Password doesn't match the new terraform output. Not propagating."
else if AWS_ACCESS_KEY_ID=$(terraform output -raw terraform_state_access_key_id) AWS_SECRET_ACCESS_KEY=$(terraform output -raw terraform_state_secret_access_key) aws sts get-caller-identity >/dev/null
    echo "new key confirmed live against AWS"
    cd ..
    set-vault-token root
    vault kv patch secret/ansible/frigg/iac-env aws_access_key_id=(op read 'op://Homelab 2.0/jnvf6aokgml7vkjj4ho2xlcvua/username') aws_secret_access_key=(op read 'op://Homelab 2.0/jnvf6aokgml7vkjj4ho2xlcvua/credential')
    set-aws-creds state
else
    cd ..
    echo "STOP: 1Password matches, but AWS rejected the key. Investigate before propagating."
end
set -e TF_HASH OP_HASH
```

---

## 3. Cloudflare

Three separate tokens exist. Check the dashboard (My Profile → API Tokens) before assuming which one you are rolling:

| Token | Scope | Where it lives |
|---|---|---|
| **"Terraform Cloudflare"** | Broad: tunnel, DNS, WAF, zone settings, bot management. This is `CLOUDFLARE_API_TOKEN`. | 1Password `[Asgard] - Terraform - Cloudflare - API token` (UUID `ps4mc2hv7a777tzsef755te64m`), Vault `secret/ansible/cloudflare/api-token`, `frigg/iac-env` |
| **"cert-manager-niflheim-dns01"** | Narrow, DNS-01 only, for cert-manager | **Only** `secret/k8s/cert-manager/cloudflare`. **Never overwrite it with the Terraform token** (this once happened; recovery was `vault kv rollback -version=1 secret/k8s/cert-manager/cloudflare`). |
| "Admin-read-all" | Not used by anything in the repo | |

Roll "Terraform Cloudflare" from **`https://dash.cloudflare.com/profile/api-tokens`** (the account-level page mints a different token type). The profile page gives a `cfut_` User token, which the health check's verify endpoint expects. Paste the new value into the 1Password item, then:

```fish
set CF_TOKEN (op read "op://Homelab 2.0/ps4mc2hv7a777tzsef755te64m/credential")
set CF_VERIFY (curl -s "https://api.cloudflare.com/client/v4/user/tokens/verify" -H "Authorization: Bearer $CF_TOKEN" | jq -c '.')
printf '%s' $CF_VERIFY | jq .
if test (printf '%s' $CF_VERIFY | jq -r '.success') != "true"
    echo "STOP: didn't verify. Wrong endpoint for this token's prefix (cfut_=user, cfat_=account), or the roll/paste didn't land. Not propagating."
else
    set-vault-token root
    vault kv patch secret/ansible/cloudflare/api-token value="$CF_TOKEN"
    vault kv patch secret/ansible/frigg/iac-env cloudflare_api_token="$CF_TOKEN"
    printf '%s' "$CF_TOKEN" | shasum -a 256 | cut -c1-16
    vault kv get -field=value secret/ansible/cloudflare/api-token | shasum -a 256 | cut -c1-16
    vault kv get -field=cloudflare_api_token secret/ansible/frigg/iac-env | shasum -a 256 | cut -c1-16
end
set -e CF_TOKEN CF_VERIFY
```

The three hashes must be equal. For an `cfat_` Account token, verify at `https://api.cloudflare.com/client/v4/accounts/<account_id>/tokens/verify` instead. A token that "fails" at the wrong endpoint is not broken.

Rotating the cert-manager token is different: mint a new DNS-01-only token in the Cloudflare dashboard, write it to `secret/k8s/cert-manager/cloudflare`, field `api-token` (from a shell variable, never a literal: `vault kv patch secret/k8s/cert-manager/cloudflare api-token="$NEW"`), force-sync the ExternalSecret (`kubectl annotate -n cert-manager externalsecret cloudflare-api-token force-sync=$(date +%s) --overwrite`), then `kubectl get certificate -A` and watch for `Ready`. This path has no 1Password mirror, so Vault's KV version history (`vault kv rollback`) is the only undo.

---

## 4. Authentik admin API token

1Password item `[Asgard] - Terraform - Authentik - Admin API token` (UUID `4pxuhyvygrqqeo3vro24bjrhwa`). API-driven; old and new are both valid during the overlap; the identifier is `authentik-bootstrap-token-<N>`.

> `$AUTHENTIK_TOKEN` is the credential being rotated and is cached in your shell. If it is already stale, or any step fails and you continue, you can authenticate with a deleted token or write a failed response's `null` into 1Password. The block checks after every step and stops on the first failure.

```fish
set PREFLIGHT (curl -s -o /dev/null -w '%{http_code}' "$AUTHENTIK_URL/api/v3/core/tokens/" -H "Authorization: Bearer $AUTHENTIK_TOKEN")
if test "$PREFLIGHT" != "200"
    echo "STOP: AUTHENTIK_TOKEN isn't authenticating (HTTP $PREFLIGHT). Run `homelab-env --refresh` and re-run the block."
else
    set CUR_ID (curl -s "$AUTHENTIK_URL/api/v3/core/tokens/" -H "Authorization: Bearer $AUTHENTIK_TOKEN" | jq -r '[.results[] | select(.identifier | test("^authentik-bootstrap-token-[0-9]+$"))] | sort_by(.identifier | ltrimstr("authentik-bootstrap-token-") | tonumber) | last | .identifier')
    set NEW_ID "authentik-bootstrap-token-"(math (string replace 'authentik-bootstrap-token-' '' $CUR_ID) + 1)
    echo "rotating $CUR_ID -> $NEW_ID"
    set CREATE_ID (curl -s -X POST "$AUTHENTIK_URL/api/v3/core/tokens/" -H "Authorization: Bearer $AUTHENTIK_TOKEN" -H "Content-Type: application/json" -d (jq -nc --arg id "$NEW_ID" '{identifier:$id,user:6,intent:"api",expiring:false,description:"rotated"}') | jq -r '.identifier')
    if test "$CREATE_ID" != "$NEW_ID"
        echo "STOP: create failed (got '$CREATE_ID', expected '$NEW_ID'). Nothing written; safe to retry from the top."
    else
        set NEW_AUTHENTIK_TOKEN (curl -s "$AUTHENTIK_URL/api/v3/core/tokens/$NEW_ID/view_key/" -H "Authorization: Bearer $AUTHENTIK_TOKEN" | jq -r .key)
        if test (string length "$NEW_AUTHENTIK_TOKEN") -lt 20
            echo "STOP: fetched key looks wrong. NOT writing to 1Password. Token record '$NEW_ID' may need manual cleanup in Authentik."
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

The two hashes must match and the `.results | length` call must print a number, before the old token is deleted.

**If Authentik has no working credential at all** (API path presumed broken): mint a token straight against the database. **The operator runs this personally**; the printed value must only touch their own screen, then goes into 1Password by hand (app, not `op item edit`).

```fish
set POD (kubectl get pod -n authentik -l app.kubernetes.io/name=authentik,app.kubernetes.io/component=server -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n authentik "$POD" -- ak shell -c "
from authentik.core.models import Token, User
import re
u = User.objects.get(username='akadmin')
nums = [int(m.group(1)) for i in Token.objects.values_list('identifier', flat=True) if (m := re.fullmatch(r'authentik-bootstrap-token-(\d+)', i))]
ident = f'authentik-bootstrap-token-{max(nums) + 1 if nums else 1}'
t = Token.objects.create(identifier=ident, user=u, intent='api', expiring=False, description='recovered via django shell')
print('IDENT:', ident)
print('TOKEN:', t.key)
"
set -e POD
```

---

## 5. NetBox admin API token

1Password item `[Asgard] - Terraform - NetBox - Admin API token` (UUID `lsqb4z5mbeijeqbxx43y5pkl5q`). Neither the REST create response nor the UI "Add token" gives a usable value in this deployment; the proven path is the Django shell. **Operator runs the first block themselves.**

```fish
set ROTATE_DATE (date -u +%Y-%m-%d)
kubectl exec -n netbox deploy/netbox -c netbox -- /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell -c "
from users.models import User, Token
u = User.objects.get(username='admin')
t = Token.objects.create(user=u, description='homelab-env admin token (rotated $ROTATE_DATE)')
print('CREDENTIAL:', 'nbt_' + t.key + '.' + t.token)
"
set -e ROTATE_DATE
```

Paste the printed `CREDENTIAL:` value (already the complete `nbt_<key>.<token>` string) into the 1Password item's `credential` field. Then verify, propagate, and clean up:

```fish
set NEW_NETBOX_TOKEN (op read "op://Homelab 2.0/lsqb4z5mbeijeqbxx43y5pkl5q/credential")
curl -s "$NETBOX_SERVER_URL/api/users/tokens/" -H "Authorization: Bearer $NEW_NETBOX_TOKEN" | jq '.count'     # must print a number
set-vault-token root
vault kv patch secret/ansible/frigg/iac-env netbox_api_token="$NEW_NETBOX_TOKEN"

# Clean up older tokens of the same family, authenticating with the NEW token (the cached one is the credential being replaced)
set TOKENS_JSON (curl -s "$NETBOX_SERVER_URL/api/users/tokens/" -H "Authorization: Bearer $NEW_NETBOX_TOKEN" | jq -c '.')
set OLD_CANDIDATES (printf '%s' $TOKENS_JSON | jq -c '[.results[] | select(.description | test("^homelab-env admin token"))] | sort_by(.created) | .[0:-1]')
set OLD_COUNT (printf '%s' $OLD_CANDIDATES | jq 'length')
if test "$OLD_COUNT" = "0"
    echo "nothing to clean up"
else if test "$OLD_COUNT" = "1"
    set OLD_ID (printf '%s' $OLD_CANDIDATES | jq -r '.[0].id')
    echo "deleting old NetBox admin token id $OLD_ID"
    curl -s -X DELETE "$NETBOX_SERVER_URL/api/users/tokens/$OLD_ID/" -H "Authorization: Bearer $NEW_NETBOX_TOKEN"
else
    echo "STOP: $OLD_COUNT old candidates found; not touching any. List and pick by hand:"
    printf '%s' $OLD_CANDIDATES | jq -r '.[] | "\(.id) \(.description) created=\(.created)"'
end
set -e TOKENS_JSON OLD_CANDIDATES OLD_COUNT OLD_ID NEW_NETBOX_TOKEN
```

Other tokens in that list (`ansible_nb_api`, `ansible_nb_inventory`, old bootstrap entries) belong to other consumers; never delete them here.

---

## 6. Semaphore API token

1Password item `[Asgard] - Terraform - Semaphore - Admin API token` (UUID `24fmbstdhqzwk6eeru4vvaixsm`). This is Semaphore's own UI/API token, **not** the `ansible-awx` AppRole (rotating one does not rotate the other).

```fish
set OLD_ID (curl -s "$SEMAPHOREUI_API_BASE_URL/user/tokens" -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN" | jq -r '.[0].id // empty')
if test -z "$OLD_ID"
    echo "STOP: SEMAPHOREUI_API_TOKEN isn't authenticating (or no existing token). Refresh the env and re-run."
else
    set NEW_TOKEN_JSON (curl -s -X POST "$SEMAPHOREUI_API_BASE_URL/user/tokens" -H "Authorization: Bearer $SEMAPHOREUI_API_TOKEN")
    set NEW_SEMAPHORE_TOKEN (echo $NEW_TOKEN_JSON | jq -r '.id // empty')
    set -e NEW_TOKEN_JSON
    if test (string length "$NEW_SEMAPHORE_TOKEN") -lt 30
        echo "STOP: create failed or returned a short/masked value. NOT writing to 1Password."
    else
        op item edit '24fmbstdhqzwk6eeru4vvaixsm' --vault "Homelab 2.0" "credential=$NEW_SEMAPHORE_TOKEN"
        set NEW_HASH (printf '%s' "$NEW_SEMAPHORE_TOKEN" | shasum -a 256 | cut -c1-16)
        set OP_HASH (printf '%s' (op read 'op://Homelab 2.0/24fmbstdhqzwk6eeru4vvaixsm/credential') | shasum -a 256 | cut -c1-16)
        echo "$NEW_HASH / $OP_HASH"
        if test "$NEW_HASH" != "$OP_HASH"
            echo "STOP: 1Password write didn't stick; paste into the app. NOT deleting the old token."
        else
            set-vault-token root
            vault kv patch secret/ansible/frigg/iac-env semaphore_api_token="$NEW_SEMAPHORE_TOKEN"
            curl -s -X DELETE "$SEMAPHOREUI_API_BASE_URL/user/tokens/$OLD_ID" -H "Authorization: Bearer $NEW_SEMAPHORE_TOKEN"
        end
        set -e NEW_HASH OP_HASH
    end
end
set -e NEW_SEMAPHORE_TOKEN OLD_ID
```

`OLD_ID` assumes exactly one token exists. If there are several, list them first (`jq -r '.[] | "\(.id) \(.name)"'`) and delete deliberately.

---

## 7. AdGuard admin password

1Password item `[Asgard] - Terraform - AdGuard - Admin login` (UUID `hvh3d7hlivcsbjqqye34f3d7a4`; `username` is `ghost`, not `admin`). AdGuard has no API-token concept, and the role never re-renders an already-bootstrapped node's config (to protect live rewrites and filters), so a plain role re-run is a **silent no-op**: the hash is changed in place on each node.

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

set ROTATE_DATE (date -u +%Y-%m-%d)
for h in 10.0.11.201 10.0.11.202 10.0.11.203
    ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE ansible@$h "sudo cp /opt/AdGuardHome/AdGuardHome.yaml /opt/AdGuardHome/AdGuardHome.yaml.bak-pw-rotate-$ROTATE_DATE && sudo sed -i 's|^    password: .*|    password: \"$NEW_ADGUARD_HASH\"|' /opt/AdGuardHome/AdGuardHome.yaml && sudo systemctl restart AdGuardHome.service"
    if test $status -ne 0
        echo "STOP: $h failed (exit $status). That node still has the OLD password; fix it before trusting the login test."
    else
        echo "$h: ok"
    end
end
set -e ROTATE_DATE
```

The `sed` delimiter must be `|`, never `/`: bcrypt hashes often contain `/`. **Verify by logging in, never by comparing hashes** (bcrypt salts randomly, so two hashes of the same password never match):

```fish
for h in 10.0.11.201 10.0.11.202 10.0.11.203
    curl -s -X POST "http://$h/control/login" -H "Content-Type: application/json" -d "{\"name\":\"$ADGUARD_USERNAME\",\"password\":\"$NEW_ADGUARD_PASSWORD\"}" -i | head -1
end
curl -s -o /dev/null -w "%{http_code}\n" https://smoketest.niflheim.xiiisins.com/anything
set -e NEW_ADGUARD_PASSWORD NEW_ADGUARD_HASH
```

All four must be `200`.

---

## 8. Other credentials

| Credential | How |
|---|---|
| Vault AppRole SecretIDs | **AppRole rotation** |
| Tailscale auth keys (90-day cap) | `terraform -chdir=terraform/tailscale apply` re-mints invalid keys and rewrites their Vault secrets. Do this **before** re-running Ansible against a rebuilt Tailscale node more than 90 days after its last mint. |
| Munin Tailscale key | Manual, 1Password only: mint in the Tailscale admin UI (reusable off, ephemeral off, pre-approved on, `tag:subnet-router`). |
| TF-minted app secrets (Garage keys, Authentik client secrets, database passwords) | `terraform -chdir=<module> apply -replace=<resource>`; then force-sync the ExternalSecret and restart the consumer; then mirror (**Secret mirroring**). Example Garage key: `kubectl -n garage port-forward svc/garage-admin 3903:3903 &` then `terraform -chdir=terraform/garage apply -replace=garage_key.outline`. |
| Hermod Discord webhooks | Create the webhook in Discord, `vault kv put secret/ansible/hermod/discord/<tag> url="…"` (from a variable, not a literal), `ansible-playbook … playbooks/asgard-hermod.yml --tags hermod-api`, then mirror. |
| SSH keys (ansible, recovery) | In 1Password only. A new `ansible` key needs the public key updated in Ansible Vault (`group_vars/all/vault.yml`) and a baseline re-run across the fleet. |

## 9. After any rotation involving `frigg/iac-env`

The control node and every `vault-homelab-env` session serve the old value until the local cache expires. Force-refresh on **every** machine, with output suppressed (this command's cache write is the mechanism behind two past transcript leaks):

```bash
vault-homelab-env --refresh >/dev/null 2>&1
```

Then confirm the Semaphore `infra-health-check` template (check #8) passes: run it from **Everyday operations** §8.

---

## See also

- **AppRole rotation** (this section).
- **Secret mirroring** (this section): after minting, mirror to 1Password.
- **Identity & secrets** (Components): what each store is for.
