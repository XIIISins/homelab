# terraform/vault/frigg.tf
#
# Frigg (control-node watchtower, Phase 6 Stage 2) Vault wiring.
#
#   - homelab-frigg policy: full CRUD on secret/* (read every secret +
#     write the paths TF modules mint to) but NO sys/* admin. So Frigg can
#     run the full TF/Ansible/Flux cycle EXCEPT terraform/vault (Vault
#     self-config — auth methods/policies/mounts — stays a MacBook+root
#     operation) and terraform/aws (bootstrap AWS identity). This keeps the
#     AppRole well short of root: it can read/rotate secrets, not re-wire
#     Vault's own auth model.
#
#   - ansible-frigg AppRole: Frigg's secret-zero. The SecretID is NEVER in
#     TF state — minted manually (`vault write -f auth/approle/role/
#     ansible-frigg/secret-id`) and seeded onto Frigg by the MacBook at
#     provision time (root-only file). Frigg's Vault-backed env shim
#     AppRole-logs-in to mint a short-lived token and pulls its IaC creds
#     from secret/ansible/frigg/*. No `op`/1Password on the box.
#     No token_bound_cidrs: Frigg reaches Vault via the Traefik FQDN /
#     MetalLB VIP, so the source IP Vault sees is NOT Frigg's 10.0.11.30
#     (it's SNAT'd) — a CIDR bind would reject every login.
#
#   - GitHub deploy key (ed25519): the private key lives in Vault (frigg
#     role pulls it to ~/.ssh on the box); the public key is added to the
#     XIIISins/homelab repo as a READ-WRITE deploy key (so claude sessions
#     on Frigg pull + push, mirroring the MacBook GitOps flow). Minting it
#     here (not on the box) makes it rebuild-safe — a rebuilt Frigg reuses
#     the same key from Vault, no re-add to GitHub.

resource "vault_policy" "homelab_frigg" {
  name = "homelab-frigg"

  policy = <<-EOT
    # Read + write every secret: Frigg runs the full IaC cycle (reads what
    # modules consume, writes what TF modules mint/rotate). KV v2 data.
    path "secret/data/*" {
      capabilities = ["create", "read", "update", "patch", "delete", "list"]
    }
    # KV v2 metadata: list for navigation/discovery, delete for full
    # destroy of a secret (TF resource destroy).
    path "secret/metadata/*" {
      capabilities = ["read", "list", "delete"]
    }
    # NO sys/* — cannot manage Vault auth methods, policies, or mounts.
    # terraform/vault stays MacBook+root; this AppRole is not near-root.
  EOT
}

resource "vault_approle_auth_backend_role" "ansible_frigg" {
  backend        = vault_auth_backend.approle.path
  role_name      = "ansible-frigg"
  token_policies = ["homelab-frigg"]
  token_ttl      = 1800    # 30min working tokens
  token_max_ttl  = 3600    # 1h hard ceiling
  secret_id_ttl  = 7776000 # 90 days (mount max_lease_ttl tuned to 2160h in main.tf)
}

# GitHub deploy key for the homelab repo checkout on Frigg.
resource "tls_private_key" "frigg_github_deploy" {
  algorithm = "ED25519"
}

resource "vault_kv_secret_v2" "frigg_github_deploy_key" {
  mount = vault_mount.kv.path
  name  = "ansible/frigg/github-deploy-key"
  data_json = jsonencode({
    private_key = tls_private_key.frigg_github_deploy.private_key_openssh
    public_key  = tls_private_key.frigg_github_deploy.public_key_openssh
  })
}

# Public half — add to XIIISins/homelab as a read-write deploy key:
#   gh repo deploy-key add <(terraform output -raw frigg_github_deploy_public_key) \
#     --repo XIIISins/homelab --title frigg-watchtower --allow-write
output "frigg_github_deploy_public_key" {
  description = "Add as a read-write deploy key on XIIISins/homelab"
  value       = tls_private_key.frigg_github_deploy.public_key_openssh
}
