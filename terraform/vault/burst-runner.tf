# terraform/vault/burst-runner.tf
#
# Phase 10h: the burst runner's own Vault identity (docs/procedures/k8s-burst-test.md). The runner is a root-loaded service on Frigg that builds a
# throwaway DigitalOcean cluster to test agent-authored k8s PRs. It must not borrow Frigg's broad `ansible-frigg` AppRole (read/write on all of
# secret/*). This role reads exactly three things and can write nothing:
#
#   secret/ansible/aiops/burst/*                  OPERATOR-SEEDED (below): the burst-scoped DigitalOcean token and a state-only AWS identity
#   secret/ansible/tailscale/authkeys/burst       the ephemeral tailnet auth key for tag:burst (written by terraform/tailscale); the burst
#                                                 playbook's only Vault lookup
#   secret/ansible/frigg/ssh-private-key          the fleet key, read by the runner's PRIVATE ssh-agent loader and held in memory only
#
# The role carries no sys/*, no auth/*, no other KV path. The SecretID is NEVER in Terraform state: minted by hand
# (`vault write -f auth/approle/role/aiops-burst-runner/secret-id`) and placed root-only on Frigg at /etc/aiops-burst/approle.env by the Ansible
# role (roles/aiops-burst-runner; procedure "Deploy"). secret_id_ttl is 90 days like the other Frigg-side roles: re-mint before it (a silent expiry
# stops the runner from starting).
#
# `secret/ansible/aiops/burst/env` fields (operator, `vault kv put`, values never typed into a transcript):
#   digitalocean_token                  the shared custom-scope DO token (copied from secret/ansible/frigg/iac-env)
#   aws_access_key_id, aws_secret_access_key   a NARROW state identity: s3 get/put/delete on the burst module's state key
#                                       (digitalocean-burst/terraform.tfstate and its .tflock), nothing else; minted by terraform/aws/burst-runner.tf
#   (all four fields are written by scripts/secrets/seed-burst-state-key)
#   aws_default_region                  eu-west-1

resource "vault_policy" "aiops_burst_runner" {
  name = "aiops-burst-runner"

  policy = <<-EOT
    # Read-only, exactly these paths. KV v2 data paths.
    path "secret/data/ansible/aiops/burst/*" {
      capabilities = ["read"]
    }
    path "secret/data/ansible/tailscale/authkeys/burst" {
      capabilities = ["read"]
    }
    path "secret/data/ansible/frigg/ssh-private-key" {
      capabilities = ["read"]
    }
  EOT
}

resource "vault_approle_auth_backend_role" "aiops_burst_runner" {
  backend        = vault_auth_backend.approle.path
  role_name      = "aiops-burst-runner"
  token_policies = [vault_policy.aiops_burst_runner.name]
  token_ttl      = 300
  token_max_ttl  = 600
  secret_id_ttl  = 7776000 # 90 days (mount max_lease_ttl is 2160h in main.tf)
  # No token_bound_cidrs: Frigg reaches Vault through the Traefik FQDN / MetalLB VIP, so Vault sees a SNAT'd source (same reason as ansible-frigg
  # in frigg.tf). The blast radius is bounded by the policy above instead.
}
