<!-- docs/procedures/zabbix-saml-deploy.md -->

# Zabbix SAML SSO deploy (Phase 7c.3–7c.6)

One-time procedure to apply the SAML SSO draft (worktree branch `worktree-draft-zabbix-saml`) end-to-end. Pre-conditions: network access for `terraform apply` (S3 state lock) and `git push` (Flux reconciliation).

Draft sits on branch `worktree-draft-zabbix-saml` at `.claude/worktrees/draft-zabbix-saml/` (commits: `6825295` feat + `4429ff0` docs + this procedure).

---

## 0. Integrate the worktree branch into main

Local `main` diverged from the worktree base (the Patroni-fix commits `70ad3af` + `2593133` landed on local main between worktree creation and the SAML draft). `--ff-only` refused, so pick one:

```fish
# Option A — rebase + ff (linear history, recommended)
git rebase main worktree-draft-zabbix-saml
git merge --ff-only worktree-draft-zabbix-saml

# Option B — merge commit
git merge --no-ff worktree-draft-zabbix-saml
```

Then clean up the worktree:

```fish
git worktree remove .claude/worktrees/draft-zabbix-saml
git branch -D worktree-draft-zabbix-saml
```

Review the diff before pushing — nothing in this draft was deployed, so the operator sees the final shape first.

---

## 1. Mint the SAML SP keypair (one-time, operator)

Zabbix signs AuthnRequests with this; Authentik verifies using the public half (TF registers the cert as `authentik_certificate_key_pair.zabbix_sp` from this same Vault path).

```fish
set -l tmpdir (mktemp -d)
cd $tmpdir

openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -subj '/CN=hugin.midgard.xiiisins.com' \
  -keyout sp.key -out sp.crt

vault kv put secret/ansible/zabbix/saml-sp-keypair \
  key=@sp.key cert=@sp.crt

# Verify (should show key + cert fields)
vault kv get -format=json secret/ansible/zabbix/saml-sp-keypair \
  | jq '.data.data | keys'

cd -
rm -rf $tmpdir
```

10-year self-signed. Rotation is operator-driven; long-lived because Zabbix-side cert rotation is operationally rare and the trust scope is narrow (only Authentik validates against it).

---

## 2. Apply Terraform (three modules, sequential)

```fish
# All apply from main checkout (not worktree) — TF apply rule, see CLAUDE.md
. ~/.cache/homelab/env.sh

# 2a. Authentik — creates SAML provider + Application + policy binding
#                + registers the SP cert + writes IdP info to Vault
cd terraform/authentik
terraform plan -out=zabbix-saml.tfplan
# Review the plan — expect: 1 group, 1 user-membership update,
# 1 certificate, 1 provider, 1 application, 1 policy binding, 1 vault entry
terraform apply zabbix-saml.tfplan
cd -

# 2b. Cloudflare — apex DNS CNAME hugin.xiiisins.com → tunnel
cd terraform/cloudflare
terraform plan -out=hugin.tfplan
terraform apply hugin.tfplan
cd -

# 2c. AdGuard — LAN rewrite hugin.midgard.xiiisins.com → Traefik VIP
cd terraform/adguard
terraform plan -out=hugin.tfplan
terraform apply hugin.tfplan
cd -
```

---

## 3. Push for Flux reconciliation

```fish
git push origin main
```

Wait ~1 min, then verify the cloudflared ConfigMap + zabbix-ingress resources landed:

```fish
flux reconcile kustomization apps -n flux-system
flux reconcile kustomization infrastructure -n flux-system

kubectl get configmap -n cloudflared cloudflared-config -o jsonpath='{.data.config\.yaml}' | grep hugin
kubectl get ns zabbix-ingress
kubectl get svc,endpointslice,httproute -n zabbix-ingress
```

Cloudflared pods need a restart to re-read the ConfigMap (the subPath-mount gotcha — see CLAUDE.md):

```fish
kubectl rollout restart deployment cloudflared -n cloudflared
kubectl rollout status deployment cloudflared -n cloudflared
```

---

## 4. Apply the Ansible role (SAML pass only)

```fish
. ~/.cache/homelab/env.sh
cd ansible

# Tag-scoped to the SAML pass — won't re-run packages/schema/web-config/services
ansible-playbook playbooks/zabbix-host.yml --tags zabbix:saml

# If anything looks off and you want a closer look:
ANSIBLE_NO_LOG=False ansible-playbook playbooks/zabbix-host.yml \
  --tags zabbix:saml --limit hugin -v
```

Expected outcome: three cert files at `/usr/share/zabbix/conf/certs/{sp.key,sp.crt,idp.crt}` (root:www-data 0640), one `userdirectory_saml` row in Zabbix DB (named "Authentik SAML"), global authentication settings with SAML enabled.

Quick sanity check on the LXC:

```fish
ssh ansible@10.0.11.21 'ls -la /usr/share/zabbix/conf/certs/ && sudo cat /etc/zabbix/web/zabbix.conf.php | grep -E "SP_KEY|SP_CERT|IDP_CERT"'
```

---

## 5. First-login validation (Phase 7c.6)

Three paths to validate; mark each off:

**Path A — WAN SAML (apex):**
1. From off-LAN device (phone on cellular, or laptop on a different network), browse `https://hugin.xiiisins.com`
2. Click "Sign in with SAML"
3. Authentik round-trip — login as `ghost` if not already
4. Land back on Zabbix dashboard as JIT-provisioned user with Super admin role
5. Verify: Users → Authentication → User directories shows "Authentik SAML"; Users → Users shows your JIT-created entry in "Zabbix administrators"

**Path B — LAN SAML (canonical):**
1. From a LAN device, browse `https://hugin.xiiisins.com` (same canonical hostname — `authentik_provider_saml` only supports one ACS URL, so SAML logins always start from the apex regardless of which network the client is on)
2. Same flow as Path A. Should work because the apex DNS record resolves through public Cloudflare for LAN clients too.

**Path C — Backdoor (K3s-down emergency):**
1. Browse `http://hugin-direct.niflheim.xiiisins.com` (plain HTTP — AGH rewrite bypasses Traefik)
2. Local-Admin login with the password from `secret/ansible/zabbix/admin-password`
3. Confirms no K3s dependency for emergencies

If Path A or B fails: check `/var/log/nginx/access.log` + `/var/log/php8.4-fpm.log` on the LXC for the SAML round-trip details. The most common first-deploy issues are:
- ACS URL mismatch (Authentik logs "Invalid AssertionConsumerServiceURL")
- Signature validation fails (check IdP cert PEM in `/usr/share/zabbix/conf/certs/idp.crt` matches what Authentik signs with)
- JIT mapping doesn't fire (verify the `groups` SAML attribute name in the Authentik metadata XML matches `zabbix_saml_groups_attribute` in role defaults)

---

## 6. Post-validation tightening (optional)

Once SAML round-trip works for `ghost`:

- **Disable internal user creation** in Zabbix → Users → Authentication: set "Sign-up" disabled. Keeps Internal auth enabled ONLY for the local `Admin` break-glass user; everyone else must SAML in.
- **Bookmark `hugin.xiiisins.com`** as the canonical entry point — midgard alias only works for non-SAML browsing (session cookies bound to hostname).

---

## 7. Post-flight docs (after successful deploy)

- Tick `7c.3` / `7c.4` / `7c.5` / `7c.6` in `docs/operations/open-questions.md` to `[x]` with closure date + commit refs.
- Tick the parent `Phase 7c` line if all four sub-phases land cleanly.
- Update `CLAUDE.md` "Current build status" — Phase 7c flips from 🟡 → ✅.
- Update `docs/operations/build-sequence.md` row for Phase 7c with closure narrative.
- New incident retro `docs/incidents/YYYY-MM-DD-zabbix-saml-cutover.md` if any findings surface during deploy (especially expected: SAML attribute name guesses might need adjustment based on actual Authentik metadata).

---

## Rollback (if SAML round-trip is broken and the backdoor is the only working path)

```fish
# Disable SAML via Ansible — flips zabbix_saml_enabled to false + reruns
ansible-playbook playbooks/zabbix-host.yml --tags zabbix:saml \
  -e '{"zabbix_saml_enabled": false}' --limit hugin

# OR via Zabbix API directly (faster, no full role run):
# Users → Authentication → SAML settings → uncheck "Enable SAML authentication"
```

Either path drops back to Internal-only auth (local Admin login). Backdoor URL keeps working throughout. Diagnose the failure, fix, re-enable.
