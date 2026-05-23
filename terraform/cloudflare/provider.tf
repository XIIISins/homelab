# CLOUDFLARE_API_TOKEN comes from env.
# CLOUDFLARE_API_TOKEN=<token from 1Password item "terraform-cloudflare">
# Token scopes: Zone:DNS:Edit + Zone:Zone:Read on xiiisins.com, Account:Cloudflare Tunnel:Edit
provider "cloudflare" {}

# VAULT_ADDR and VAULT_TOKEN come from env (same pattern as terraform/vault/).
# VAULT_ADDR=http://10.0.20.11:8200
# VAULT_TOKEN=<root token from 1Password>
provider "vault" {}
