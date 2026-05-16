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

