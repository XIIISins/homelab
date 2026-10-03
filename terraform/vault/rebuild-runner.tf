# terraform/vault/rebuild-runner.tf
#
# Phase 10g: the rebuild runner's own Vault identity (docs/procedures/aiops-rebuild.md). The runner is a root-loaded
# service on Frigg that is the only place Terraform runs unattended. It must not borrow Frigg's broad `ansible-frigg`
# AppRole (read/write on all of secret/*): a compromised runner would then hold every homelab secret. This role reads
# exactly ONE subtree and can write nothing:
#
#   secret/ansible/aiops/rebuild/pve-token   token_id + secret of aiops-rebuild@pve (pool-scoped; written by
#                                            terraform/proxmox/asgard-pools)
#   secret/ansible/aiops/rebuild/env         OPERATOR-SEEDED, not a Terraform resource (below): the Terraform state
#                                            backend credentials + the public SSH key the LXC module injects
#
# The role carries no sys/*, no auth/*, no other KV path. The SecretID is NEVER in Terraform state: minted by hand
# (`vault write -f auth/approle/role/aiops-rebuild-runner/secret-id`) and placed root-only on Frigg at
# /etc/aiops-rebuild/approle.env by the Ansible role (roles/aiops-rebuild-runner; procedure "Deploy").
#
# secret_id_ttl is 90 days like ansible-frigg (the mount ceiling is tuned to match in main.tf): a silent expiry stops the
# runner from starting, so re-mint before it (the procedure lists the date to put in the calendar).
#
# `secret/ansible/aiops/rebuild/env` fields (operator, `vault kv put`, values never typed into a transcript):
#   aws_access_key_id, aws_secret_access_key   a NARROW state identity: s3 get/put/delete on the state keys of the modules
#                                              the runner applies (proxmox/asgard-lxcs/terraform.tfstate and its .tflock),
#                                              nothing else. Minted by terraform/aws with the Bootstrap identity; until it
#                                              exists the runner cannot init, which is the safe failure.
#   ssh_public_key                             the `ansible` user's public key (`ssh_public_key` variable of asgard-lxcs)
#   aws_default_region                         eu-west-1

resource "vault_policy" "aiops_rebuild_runner" {
  name = "aiops-rebuild-runner"

  policy = <<-EOT
    # Read-only, one subtree. KV v2 data path.
    path "secret/data/ansible/aiops/rebuild/*" {
      capabilities = ["read"]
    }
  EOT
}

resource "vault_approle_auth_backend_role" "aiops_rebuild_runner" {
  backend        = vault_auth_backend.approle.path
  role_name      = "aiops-rebuild-runner"
  token_policies = [vault_policy.aiops_rebuild_runner.name]
  token_ttl      = 300
  token_max_ttl  = 600
  secret_id_ttl  = 7776000 # 90 days (mount max_lease_ttl is 2160h in main.tf)
  # No token_bound_cidrs: Frigg reaches Vault through the Traefik FQDN / MetalLB VIP, so Vault sees a SNAT'd source
  # (same reason as ansible-frigg in frigg.tf). The blast radius is bounded by the policy above instead.
}
