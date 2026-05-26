# KV-v2 secrets engine at secret/
resource "vault_mount" "kv" {
  path    = "secret"
  type    = "kv"
  options = { version = "2" }
}

# Kubernetes auth method
resource "vault_auth_backend" "kubernetes" {
  type = "kubernetes"
  path = "kubernetes"
}

# Auth method config — no token_reviewer_jwt: Vault uses its own
# pod SA token via the system:auth-delegator ClusterRoleBinding.
resource "vault_kubernetes_auth_backend_config" "kubernetes" {
  backend                = vault_auth_backend.kubernetes.path
  kubernetes_host        = "https://kubernetes.default.svc"
  disable_iss_validation = true
}

# ESO policy: read on secret/data/*
resource "vault_policy" "eso" {
  name = "eso"

  policy = <<-EOT
    path "secret/data/*" {
      capabilities = ["read"]
    }
  EOT
}

# ESO role: binds SA external-secrets/external-secrets to the eso policy
resource "vault_kubernetes_auth_backend_role" "eso" {
  backend                          = vault_auth_backend.kubernetes.path
  role_name                        = "eso"
  bound_service_account_names      = ["external-secrets"]
  bound_service_account_namespaces = ["external-secrets"]
  token_policies                   = ["eso"]
  token_ttl                        = 3600
}

# -----------------------------------------------------------------------------
# AppRole auth method for Ansible
# -----------------------------------------------------------------------------
# AppRole is Vault's service-account login mechanism. RoleID + SecretID combine
# to obtain a short-lived token; the token reads secrets per attached policies.
#
# SecretIDs are NEVER generated through Terraform — keeping them out of TF state.
# Generate manually: `vault write -f auth/approle/role/<role>/secret-id`
# See homelab-design.md "AppRole bootstrap runbook" for the full procedure.

resource "vault_auth_backend" "approle" {
  type = "approle"
  path = "approle"
}

# ansible policy: read on secret/data/ansible/*
# Scoped narrower than the ESO policy — Ansible only reads its own subtree.
# K8s workload secrets (under secret/k8s/*) are not visible.
resource "vault_policy" "ansible" {
  name = "ansible"

  policy = <<-EOT
    path "secret/data/ansible/*" {
      capabilities = ["read"]
    }
  EOT
}

# ansible-local AppRole: MacBook control node, manual playbook runs.
# SecretID generated after first apply, stored locally
# (~/.config/ansible/vault-approle.env, mode 0600) with a recovery copy in the
# 1Password Homelab vault.
resource "vault_approle_auth_backend_role" "ansible_local" {
  backend        = vault_auth_backend.approle.path
  role_name      = "ansible-local"
  token_policies = ["ansible"]
  token_ttl      = 1800     # 30min — short-lived working tokens
  token_max_ttl  = 3600     # 1h hard ceiling
  secret_id_ttl  = 7776000  # 90 days — manual rotation cadence
  # No token_bound_cidrs: MacBook IP changes (home, travel, tethering).
}

# ansible-awx AppRole: AWX automated runs (deployed later, in prod K3s).
# Role exists now so the policy binding is captured in IaC; SecretID is
# generated at AWX deploy time and stored in AWX's credential store.
# token_bound_cidrs should be set to the prod K3s pod CIDR once AWX is deployed.
resource "vault_approle_auth_backend_role" "ansible_awx" {
  backend        = vault_auth_backend.approle.path
  role_name      = "ansible-awx"
  token_policies = ["ansible"]
  token_ttl      = 1800
  token_max_ttl  = 3600
  secret_id_ttl  = 7776000
}
# -----------------------------------------------------------------------------
# keepalived VRRP shared secret
# -----------------------------------------------------------------------------
# VRRPv2 auth_type PASS uses an 8-byte (max) shared secret carried in
# each advertisement. All peers in a VRRP instance must agree on it,
# otherwise they ignore each other's adverts and elect independently
# (dual-MASTER, split VIP). This is not cryptographic — it prevents
# accidental VRRP storms from misconfigured devices on the same L2
# broadcast domain, not deliberate attack. 8 chars is the protocol cap;
# keepalived silently truncates anything longer.
#
# Consumed by the keepalived role on the HAProxy/etcd trio
# (Hlin/Eir/Snotra) via the standard community.hashi_vault AppRole
# lookup pattern.
resource "random_password" "keepalived_pg_vrrp" {
  length  = 8
  special = false   # VRRP auth field is ASCII-only; keep it printable
}

resource "vault_kv_secret_v2" "keepalived_pg_vrrp" {
  mount = vault_mount.kv.path
  name  = "ansible/keepalived/pg_vrrp"
  data_json = jsonencode({
    auth_pass = random_password.keepalived_pg_vrrp.result
  })
}

# Same pattern for the AdGuard trio (Saga/Mimir/Kvasir). Different VRID
# (51 vs PG's 61) + different L2 segment behavior, but the auth_pass is
# the same shape — 8-byte printable ASCII shared secret consumed by the
# keepalived role on each AGH node.
resource "random_password" "keepalived_adguard_vrrp" {
  length  = 8
  special = false
}

resource "vault_kv_secret_v2" "keepalived_adguard_vrrp" {
  mount = vault_mount.kv.path
  name  = "ansible/keepalived/adguard_vrrp"
  data_json = jsonencode({
    auth_pass = random_password.keepalived_adguard_vrrp.result
  })
}

# -----------------------------------------------------------------------------
# Hermod (notifications hub) — soft-auth config-key
# -----------------------------------------------------------------------------
# AppriseAPI exposes endpoints under `/notify/<config-key>` where the
# config-key acts as a soft secret in the URL path. Primary access gate is
# the Caddy `remote_ip` allowlist (see roles/caddy-reverse-proxy + Hermod
# group_vars); this config-key is belt-and-braces depth.
#
# 32 chars, alphanumeric only (special=false) — URL-path-safe, no escaping
# in any consumer's HTTP client.
#
# Discord webhook paths (secret/ansible/hermod/discord/{critical,alert,media,
# untagged}) are deliberately NOT TF-managed — they're operator-minted
# (Discord UI), operator-rotated, operator-written. TF managing them would
# create-time-overwrite the operator's real values, and the rotation cadence
# differs (Discord webhook tokens reset via the UI, not via TF).
#
# Bound to the ansible policy at `secret/data/ansible/*` — the hermod-api
# role looks up both the config-key and the four Discord URL fields at
# config-render time via community.hashi_vault.vault_kv2_get.
resource "random_password" "hermod_config_key" {
  length  = 32
  special = false # URL-path-safe; no special chars to escape in producer HTTP clients
}

resource "vault_kv_secret_v2" "hermod_config_key" {
  mount = vault_mount.kv.path
  name  = "ansible/hermod/config-key"
  data_json = jsonencode({
    value = random_password.hermod_config_key.result
  })
}

# -----------------------------------------------------------------------------
# Per-service PG passwords — single mint, dual path
# -----------------------------------------------------------------------------
# For every per-service PG consumer, one random_password is generated and
# written to BOTH Vault paths:
#   - ansible/postgres/<service>-password   (read by the postgres Ansible
#                                            role via AppRole policy
#                                            `secret/data/ansible/*`)
#   - k8s/<service>/postgres-password       (read by ESO on behalf of the
#                                            workload via the broader
#                                            `secret/data/*` policy)
#
# Same secret in two locations is operational debt — rotation needs both
# paths updated. With both managed by TF from a single random_password
# resource, "rotate" is just `terraform taint random_password.<service>` +
# apply, and both paths update atomically. Rotation cadence is manual today
# (no schedule); revisit when secret-rotation tooling lands.
#
# Path convention preserved (ansible vs k8s domain) despite the duplication
# because (a) Ansible's narrow policy can't read `k8s/...` and (b) widening
# Ansible's policy to read K8s paths would couple secret-domain boundaries
# in ways that don't compose with future per-workload policies.
#
# Backfill TODO: authentik PG password (currently manually put in Vault
# during 5e initial deploy) is not yet TF-managed — surfaced 2026-05-24
# during 5i.a write. Bundle into the next vault TF apply.

resource "random_password" "netbox_postgres" {
  length  = 32
  special = false   # PG password field; avoid quoting issues in env vars / config files
}

resource "vault_kv_secret_v2" "netbox_postgres_ansible" {
  mount = vault_mount.kv.path
  name  = "ansible/postgres/netbox-password"
  data_json = jsonencode({
    value = random_password.netbox_postgres.result
  })
}

resource "vault_kv_secret_v2" "netbox_postgres_k8s" {
  mount = vault_mount.kv.path
  name  = "k8s/netbox/postgres-password"
  data_json = jsonencode({
    value = random_password.netbox_postgres.result
  })
}

# -----------------------------------------------------------------------------
# NetBox app + superuser secrets (K8s-only, no Ansible consumer)
# -----------------------------------------------------------------------------
# secret_key is NetBox's Django SECRET_KEY — must be ≥50 chars; 64 chars of
# alphanumeric gives 380 bits of entropy. Rotating it invalidates all signed
# data (sessions, password reset tokens, etc.) — operator only, not scheduled.
#
# superuser is the bootstrap admin (`admin` user) created at first NetBox
# startup. Used as a break-glass account when OIDC is down — password lives
# in 1Password's Homelab vault as the canonical operator-readable copy after
# first deploy (per the "local admin = 1P break-glass" rule in
# docs/architecture/identity-secrets.md). The Vault entry here is only the
# bootstrap-time source for the chart's `superuser.existingSecret` reference;
# can be rotated independently in Vault if the 1P value drifts.
#
# api_token is the superuser's NetBox API token (also bootstrap). Provided
# pre-minted so any TF integration (Phase 5i.3 — e-breuninger/netbox provider)
# has a stable token to authenticate with without manual web-UI generation.

resource "random_password" "netbox_secret_key" {
  length  = 64
  special = false # printable alphanumeric, no escaping pain in Python literals
}

resource "vault_kv_secret_v2" "netbox_app" {
  mount = vault_mount.kv.path
  name  = "k8s/netbox/app"
  data_json = jsonencode({
    secret_key = random_password.netbox_secret_key.result
  })
}

resource "random_password" "netbox_superuser_password" {
  length  = 32
  special = false
}

resource "random_password" "netbox_superuser_api_token" {
  length  = 40 # NetBox API tokens are 40 chars by convention
  special = false
  upper   = false # NetBox-side convention: lowercase hex-ish
}

resource "vault_kv_secret_v2" "netbox_superuser" {
  mount = vault_mount.kv.path
  name  = "k8s/netbox/superuser"
  data_json = jsonencode({
    password  = random_password.netbox_superuser_password.result
    api_token = random_password.netbox_superuser_api_token.result
  })
}

# -----------------------------------------------------------------------------
# NetBox inventory token (Phase 5h.3) — read-only API token used by
# Ansible's `netbox.netbox.nb_inventory` plugin to query NetBox for
# dynamic inventory.
#
# Placeholder shape only — the actual token CANNOT be minted via TF
# (e-breuninger/netbox provider v5.3.0's `netbox_token` resource is
# broken against NetBox 4.4+ per CLAUDE.md "Terraform — netbox provider"
# gotchas: POST+PUT update flow rejected, v2 token plaintext only
# available in the create response). Operator post-flight steps:
#   1. NetBox UI → Admin → API Tokens → Add Token
#   2. User: a dedicated read-only user (preferred) OR the existing
#      admin user (overpermissive but works for solo-dev).
#   3. Permissions: enabled, scope read-only on dcim/ipam/virtualization.
#   4. Stash the plaintext token (shown ONCE) in 1P "Asgard - NetBox -
#      Ansible inventory token".
#   5. Write to Vault:
#        vault kv put secret/ansible/netbox/inventory-token \
#          value=$(op read "op://Homelab/Asgard - NetBox - Ansible inventory token/credential")
#
# The placeholder below ensures the Vault path exists at apply time so
# ESO / Ansible can resolve it; lifecycle.ignore_changes on data_json
# prevents TF from clobbering the operator-written real value on
# subsequent applies (same pattern as zabbix_api_token).
resource "vault_kv_secret_v2" "netbox_inventory_token" {
  mount = vault_mount.kv.path
  name  = "ansible/netbox/inventory-token"
  data_json = jsonencode({
    value = "placeholder-pending-operator-mint"
  })

  lifecycle {
    ignore_changes = [data_json]
  }
}

# -----------------------------------------------------------------------------
# Teamspeak PG password — dual-path mint (same pattern as netbox_postgres)
# -----------------------------------------------------------------------------
# TS3 3.13+ supports PostgreSQL natively via the first-party ts3db_postgresql
# plugin. The K8s workload (k8s/asgard/apps/teamspeak/) consumes the password
# via ESO from k8s/teamspeak/postgres-password; the Ansible postgres-common
# role provisions the matching PG role + database on the Patroni leader using
# ansible/postgres/teamspeak3-password (configured in
# inventory/group_vars/postgres_hosts.yml).
#
# Role/DB name `teamspeak3` (not `teamspeak`) intentionally — matches the
# default DB name the TS3 server's create_postgresql plugin assumes when
# auto-bootstrapping schema. If renamed, set TS3SERVER_DB_NAME accordingly.
resource "random_password" "teamspeak_postgres" {
  length  = 32
  special = false # plain alphanumeric — no env-var / connection-string quoting hazards
}

resource "vault_kv_secret_v2" "teamspeak_postgres_ansible" {
  mount = vault_mount.kv.path
  name  = "ansible/postgres/teamspeak3-password"
  data_json = jsonencode({
    value = random_password.teamspeak_postgres.result
  })
}

resource "vault_kv_secret_v2" "teamspeak_postgres_k8s" {
  mount = vault_mount.kv.path
  name  = "k8s/teamspeak/postgres-password"
  data_json = jsonencode({
    value = random_password.teamspeak_postgres.result
  })
}

# -----------------------------------------------------------------------------
# Zabbix LXC secrets (Phase 7c.1) — four paths, all consumed by Ansible roles
# on the Zabbix LXC (10.0.11.21). No K8s consumers in 7c.1 — the K8s SAML IdP
# secret at secret/k8s/zabbix/saml-idp lands separately in 7c.3 from the
# terraform/authentik/ module.
#
#   - secret/ansible/postgres/zabbix-password — PG role pw for the postgres-
#     common-databases provisioning loop on the Patroni leader (same shape
#     as netbox_postgres, teamspeak_postgres). Ansible-only consumer; no
#     dual k8s path needed.
#
#   - secret/ansible/zabbix/admin-password — break-glass local Admin pw for
#     Zabbix's internal auth. Used at first deploy (rotated from default
#     "zabbix" via Ansible) and as the fallback login when Authentik SAML
#     is unavailable. Operator-readable copy ALSO stashed in 1Password
#     (Homelab vault, item "Zabbix - Admin (break-glass)") per the local-
#     admin = 1P break-glass convention; that copy is operator-minted
#     out-of-band after first vault apply (read from Vault inline, never
#     echoed in transcripts).
#
#   - secret/ansible/zabbix/saml-sp-keypair — RSA 2048 self-signed cert
#     that Zabbix's SAML SP uses to sign AuthnRequests + decrypt assertions.
#     10-year validity: SP cert rotation is a disruptive event for SAML
#     federation (Authentik needs the new cert + the IdP-side trust update),
#     so we rotate on a deliberate schedule rather than risk surprise
#     renewal. Stored as JSON with `key` (PEM private key) + `cert` (PEM
#     certificate) fields, dropped to disk by roles/zabbix-server/tasks/saml.yml
#     during Phase 7c.4.
#
#   - secret/ansible/zabbix/api-token — placeholder. The 7c.7 bootstrap
#     playbook mints a real Zabbix API token via the Zabbix API and writes
#     it back to this path. lifecycle.ignore_changes on data_json prevents
#     future TF applies from clobbering the playbook-written value.
# -----------------------------------------------------------------------------

resource "random_password" "zabbix_postgres" {
  length  = 32
  special = false # PG password — avoid env-var / connection-string quoting hazards
}

resource "vault_kv_secret_v2" "zabbix_postgres_ansible" {
  mount = vault_mount.kv.path
  name  = "ansible/postgres/zabbix-password"
  data_json = jsonencode({
    value = random_password.zabbix_postgres.result
  })
}

# Zabbix PG monitoring user — created on the Patroni leader by
# postgres-common with `pg_monitor` predefined-role membership.
# Read by the zabbix-agent role at register time and passed to each
# PG host's Zabbix host record as the {$PG.PASSWORD} macro (type=
# secret). The agent2 PG plugin uses this credential to query
# monitoring views via SCRAM over loopback. Phase 7c.8b — PG
# monitoring depth.
resource "random_password" "zabbix_monitor_postgres" {
  length  = 32
  special = false # PG password — same quoting concern as the other PG creds
}

resource "vault_kv_secret_v2" "zabbix_monitor_postgres_ansible" {
  mount = vault_mount.kv.path
  name  = "ansible/postgres/zabbix-monitor-password"
  data_json = jsonencode({
    value = random_password.zabbix_monitor_postgres.result
  })
}

resource "random_password" "zabbix_admin" {
  length  = 24
  special = true # operator-typed via 1P break-glass; symbol set OK for web login
}

resource "vault_kv_secret_v2" "zabbix_admin_ansible" {
  mount = vault_mount.kv.path
  name  = "ansible/zabbix/admin-password"
  data_json = jsonencode({
    value = random_password.zabbix_admin.result
  })
}

resource "tls_private_key" "zabbix_saml_sp" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "zabbix_saml_sp" {
  private_key_pem = tls_private_key.zabbix_saml_sp.private_key_pem

  # CN matches the SAML SP's entity ID (zabbix_saml_sp_entity_id =
  # https://hugin.xiiisins.com in ansible/roles/zabbix-server/defaults/
  # main.yml). SAML doesn't validate CN against hostname — the cert is
  # identity-binding, not transport-binding — but matching the entity
  # ID's host is hygienic + saves explaining "why does the cert say X
  # when the SP is Y?" later.
  subject {
    common_name  = "hugin.xiiisins.com"
    organization = "Asgard Homelab"
  }

  validity_period_hours = 87600 # 10 years
  early_renewal_hours   = 720   # 30 days — TF auto-rotates 30 days before expiry on next apply

  allowed_uses = [
    "digital_signature",
    "key_encipherment",
    "data_encipherment",
  ]
}

resource "vault_kv_secret_v2" "zabbix_saml_sp_keypair" {
  mount = vault_mount.kv.path
  name  = "ansible/zabbix/saml-sp-keypair"
  data_json = jsonencode({
    key  = tls_private_key.zabbix_saml_sp.private_key_pem
    cert = tls_self_signed_cert.zabbix_saml_sp.cert_pem
  })
}

resource "vault_kv_secret_v2" "zabbix_api_token" {
  mount = vault_mount.kv.path
  name  = "ansible/zabbix/api-token"
  data_json = jsonencode({
    value = "placeholder-pending-7c.7-bootstrap"
  })

  # 7c.7 bootstrap playbook overwrites this path with a real API token via
  # the Zabbix API. Without ignore_changes, subsequent TF applies would
  # clobber the playbook-written value back to the placeholder.
  lifecycle {
    ignore_changes = [data_json]
  }
}
# Garage server secrets — RPC secret + admin token
# -----------------------------------------------------------------------------
# rpc_secret authenticates internal Garage RPC between pods. Required
# even single-node (Garage refuses to start without it). Must be a
# 64-char lowercase hex string (32 bytes encoded) — `random_id` is the
# right primitive (random_password's lowercase alphanumeric is too
# permissive a charset).
#
# admin_token authenticates the admin API at :3903. Opaque bearer; any
# non-empty string works. Consumed by:
#   - the Garage pod itself (set via GARAGE_ADMIN_TOKEN env from the
#     garage-server ExternalSecret)
#   - the jkossis/garage Terraform provider in terraform/garage/, which
#     calls the admin API to mint buckets + access keys per consumer
#
# One Vault KV entry, two fields — see
# k8s/asgard/infrastructure/garage/externalsecret.yaml for the consumer
# shape (property: rpc_secret / property: admin_token).
resource "random_id" "garage_rpc_secret" {
  byte_length = 32 # → 64-char hex string; matches Garage's docs
}

resource "random_password" "garage_admin_token" {
  length  = 40
  special = false
}

resource "vault_kv_secret_v2" "garage_server" {
  mount = vault_mount.kv.path
  name  = "k8s/garage/server"
  data_json = jsonencode({
    rpc_secret  = random_id.garage_rpc_secret.hex
    admin_token = random_password.garage_admin_token.result
  })
}

# -----------------------------------------------------------------------------
# Outline — PG password (dual-path) + app secrets (k8s-only)
# -----------------------------------------------------------------------------
# PG password follows the dual-path pattern (ansible/postgres/* for the
# postgres-common role, k8s/outline/postgres-password for ESO).
#
# Outline app secrets live in a single k8s/outline/app KV with three
# fields (consolidated so the Outline ExternalSecret has fewer data
# blocks to enumerate):
#   - secret_key   — Outline's primary signing key (cookies, OIDC state).
#                    Rotating invalidates all sessions; treat immutable.
#   - utils_secret — used for short-lived token signing (email confirm,
#                    share links). Rotation invalidates outstanding tokens.
#   - redis_password — auth for the in-namespace Redis StatefulSet.
#
# OIDC client_id+secret are NOT minted here — those come from
# terraform/authentik/outline.tf (which owns the Authentik provider
# resource that generates them).
#
# S3 access_key+secret are NOT minted here — those come from
# terraform/garage/outline.tf (Garage admin API mints them at TF apply
# time and writes to secret/k8s/outline/s3).
resource "random_password" "outline_postgres" {
  length  = 32
  special = false
}

resource "vault_kv_secret_v2" "outline_postgres_ansible" {
  mount = vault_mount.kv.path
  name  = "ansible/postgres/outline-password"
  data_json = jsonencode({
    value = random_password.outline_postgres.result
  })
}

resource "vault_kv_secret_v2" "outline_postgres_k8s" {
  mount = vault_mount.kv.path
  name  = "k8s/outline/postgres-password"
  data_json = jsonencode({
    value = random_password.outline_postgres.result
  })
}

resource "random_id" "outline_secret_key" {
  byte_length = 32 # → 64-char hex; Outline docs recommend `openssl rand -hex 32`
}

resource "random_id" "outline_utils_secret" {
  byte_length = 32
}

resource "random_password" "outline_redis_password" {
  length  = 32
  special = false
}

resource "vault_kv_secret_v2" "outline_app" {
  mount = vault_mount.kv.path
  name  = "k8s/outline/app"
  data_json = jsonencode({
    secret_key     = random_id.outline_secret_key.hex
    utils_secret   = random_id.outline_utils_secret.hex
    redis_password = random_password.outline_redis_password.result
  })
}

# -----------------------------------------------------------------------------
# Semaphore (Phase 5h.3 — Ansible orchestrator, asgard K3s)
# -----------------------------------------------------------------------------
# Three secret paths:
#   - secret/ansible/postgres/semaphore-password — PG role pw for the
#     postgres-common-databases provisioning loop on the Patroni leader
#     (same dual-path shape as netbox / outline / teamspeak).
#   - secret/k8s/semaphore/postgres-password — same value, projected
#     under the k8s/ tree for ESO in the semaphore namespace.
#   - secret/k8s/semaphore/app — access_key_encryption + cookie_hash,
#     the two cryptographic secrets Semaphore needs to keep stable
#     across restarts:
#       * access_key_encryption: AES key used to encrypt the access
#         keys (SSH keys, Vault AppRole creds, etc.) in the database.
#         Loss = every stored access key undecryptable; recoverable
#         by recreating each access key in the UI.
#       * cookie_hash: HMAC key for session cookies. Loss = all live
#         sessions invalidated (forces re-login). Lower impact.
#     Both Vault-minted; consumed via env vars
#     SEMAPHORE_ACCESS_KEY_ENCRYPTION + SEMAPHORE_COOKIE_HASH from the
#     k8s/semaphore/app ExternalSecret.
#
# OIDC credentials live separately at secret/k8s/semaphore/oidc, minted
# in terraform/authentik/semaphore.tf — same module-ownership rule as
# the rest of the OIDC providers.

resource "random_password" "semaphore_postgres" {
  length  = 32
  special = false # PG password — avoid env-var / connection-string quoting hazards
}

resource "vault_kv_secret_v2" "semaphore_postgres_ansible" {
  mount = vault_mount.kv.path
  name  = "ansible/postgres/semaphore-password"
  data_json = jsonencode({
    value = random_password.semaphore_postgres.result
  })
}

resource "vault_kv_secret_v2" "semaphore_postgres_k8s" {
  mount = vault_mount.kv.path
  name  = "k8s/semaphore/postgres-password"
  data_json = jsonencode({
    value = random_password.semaphore_postgres.result
  })
}

resource "random_password" "semaphore_access_key_encryption" {
  # Base64-encoded 32-byte AES key per Semaphore docs. random_password
  # with length=32 + no special chars produces a 32-char alphanumeric
  # string — Semaphore accepts the raw string and derives the AES key
  # from it (HKDF). Plain base64 also works; the alphanumeric form is
  # safer to round-trip through env vars + shell tooling.
  length  = 32
  special = false
}

resource "random_password" "semaphore_cookie_hash" {
  length  = 64
  special = false
}

# cookie_encryption — Semaphore auto-generates this if missing from
# config.json, but auto-generated values can't persist back to a
# read-only Secret-mounted file → every pod restart would mint a new
# value, invalidating every active session. Vault-mint instead so the
# value is stable across rebuilds.
resource "random_password" "semaphore_cookie_encryption" {
  length  = 32
  special = false
}

resource "vault_kv_secret_v2" "semaphore_app" {
  mount = vault_mount.kv.path
  name  = "k8s/semaphore/app"
  data_json = jsonencode({
    access_key_encryption = random_password.semaphore_access_key_encryption.result
    cookie_hash           = random_password.semaphore_cookie_hash.result
    cookie_encryption     = random_password.semaphore_cookie_encryption.result
  })
}
