# terraform/vault/author.tf
#
# Phase 10h2 (docs/operations/10h-predictive-change.md): the identity of the PR author on Frigg. Three parts:
#
#   author_token        the dispatcher -> the Toolbelt's AUTHOR role (claim a change request, report its outcome)
#   author_tools_token  the drafting session -> the Toolbelt's AUTHOR-TOOLS role (the read-only /tool/* routes ONLY)
#   aiops-author AppRole  what the root credential loader on Frigg logs in with. One policy, four read-only paths:
#
#     secret/ansible/aiops/author-pat           OPERATOR-SEEDED (the GitHub token the dispatcher pushes with; field `token`)
#     secret/ansible/aiops/anthropic-api-key    existing (field `key`): the drafting session's model access
#     secret/ansible/aiops/author-token         minted below
#     secret/ansible/aiops/author-tools-token   minted below
#
# No other KV path, no sys/*, no auth/*, no write. The SecretID is NEVER in Terraform state: minted by hand
# (`vault write -f auth/approle/role/aiops-author/secret-id`), placed root-only on Frigg by the aiops-author role.
# The two bearer tokens are separate on purpose: a prompt-injected session holds only the tools token, which cannot claim,
# report, file or decide anything.

resource "random_password" "author_token" {
  length  = 48
  special = false # sent in an Authorization header
}

resource "vault_kv_secret_v2" "author_token" {
  mount = vault_mount.kv.path
  name  = "ansible/aiops/author-token"
  data_json = jsonencode({
    value = random_password.author_token.result
  })
}

resource "random_password" "author_tools_token" {
  length  = 48
  special = false
}

resource "vault_kv_secret_v2" "author_tools_token" {
  mount = vault_mount.kv.path
  name  = "ansible/aiops/author-tools-token"
  data_json = jsonencode({
    value = random_password.author_tools_token.result
  })
}

resource "vault_policy" "aiops_author" {
  name = "aiops-author"

  policy = <<-EOT
    # Read-only, four documents. KV v2 data paths.
    path "secret/data/ansible/aiops/author-pat" {
      capabilities = ["read"]
    }
    path "secret/data/ansible/aiops/anthropic-api-key" {
      capabilities = ["read"]
    }
    path "secret/data/ansible/aiops/author-token" {
      capabilities = ["read"]
    }
    path "secret/data/ansible/aiops/author-tools-token" {
      capabilities = ["read"]
    }
  EOT
}

resource "vault_approle_auth_backend_role" "aiops_author" {
  backend        = vault_auth_backend.approle.path
  role_name      = "aiops-author"
  token_policies = [vault_policy.aiops_author.name]
  token_ttl      = 300
  token_max_ttl  = 600
  secret_id_ttl  = 7776000 # 90 days (mount max_lease_ttl is 2160h in main.tf)
  # No token_bound_cidrs: Frigg reaches Vault through the Traefik FQDN / MetalLB VIP, so Vault sees a SNAT'd source
  # (same reason as ansible-frigg in frigg.tf). The blast radius is bounded by the policy above instead.
}
