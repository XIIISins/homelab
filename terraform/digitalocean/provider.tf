# terraform/digitalocean/provider.tf
#
# DIGITALOCEAN_TOKEN comes from env — the least-privilege token (D4 in
# docs/operations/aiops-roadmap.md) kept in Vault at
# secret/ansible/frigg/iac-env field `digitalocean_token` and exported by
# `vault-homelab-env` (same path as GITHUB_TOKEN). It is NOT the broad
# operator `doctl` token; that one is revoked at 10a3 cleanup.
#
# Minting the token is a manual DO-console step (DO has no API to mint API
# tokens). Create a *custom-scope* token (not "Full Access") with:
#   droplet:create/read/update/delete   firewall:create/read/update/delete
#   reserved_ip:create/read/update/delete   ssh_key:create/read/update/delete
#   project:create/read/update/delete   tag:create/read/delete
#   image:read  regions:read  sizes:read  actions:read  vpc:read
# If `terraform plan` 403s on a resource type, widen by exactly that scope.
provider "digitalocean" {}
