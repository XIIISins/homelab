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

