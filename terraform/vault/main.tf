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
# Test entry used by ansible/playbooks/test-vault-lookup.yml
# Recreated declaratively so rebuilds do not need a manual `vault kv put` step
resource "vault_kv_secret_v2" "ansible_test" {
  mount = vault_mount.kv.path
  name  = "ansible/test/hello"
  data_json = jsonencode({
    value = "world"
  })
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
