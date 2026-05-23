# terraform/authentik/provider.tf
#
# AUTHENTIK_URL and AUTHENTIK_TOKEN come from env (set by homelab.fish or
# manually before apply).
#
# AUTHENTIK_URL=https://authentik.xiiisins.com
# AUTHENTIK_TOKEN=<credential field from 1Password item
#                  "Asgard - Authentik - akadmin API token">
#
# Provider hits Authentik externally over the Cloudflare tunnel. Latency
# is acceptable for dev-laptop applies; DNS-magic for internal rewrite
# to authentik.midgard.xiiisins.com is a future improvement.
provider "authentik" {}

# VAULT_ADDR and VAULT_TOKEN come from env (same pattern as terraform/vault/
# and terraform/cloudflare/).
# VAULT_ADDR=http://10.0.20.11:8200
# VAULT_TOKEN=<root token from 1Password>
provider "vault" {}
