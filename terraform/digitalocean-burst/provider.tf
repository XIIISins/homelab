# terraform/digitalocean-burst/provider.tf
#
# DIGITALOCEAN_TOKEN from env — the SAME custom-scope token as terraform/digitalocean
# (Vault secret/ansible/frigg/iac-env field `digitalocean_token`, exported by
# vault-homelab-env / homelab-env on Frigg). No second token.
#
# Scopes this root exercises (all already on the token per the do1 root's scope list):
#   droplet:create/read/update/delete   firewall:create/read/update/delete
#   tag:create/read/delete              ssh_key:read   (data source — keys are NOT created here)
#   vpc:read                            (data source — the region's DEFAULT VPC)
#   image:read  regions:read  sizes:read  actions:read   (droplet create + waits)
# It does NOT need: reserved_ip, project (droplets land in the account's default project),
# account, vpc:create (no dedicated VPC — see main.tf), snapshot, domain.
# If `terraform plan` 403s, widen by exactly the scope the error names.
provider "digitalocean" {}
