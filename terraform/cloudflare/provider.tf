# CLOUDFLARE_API_TOKEN comes from env.
# CLOUDFLARE_API_TOKEN=<token from 1Password item "terraform-cloudflare">
# Token scopes: Zone:DNS:Edit + Zone:Zone:Read on xiiisins.com, Account:Cloudflare Tunnel:Edit
provider "cloudflare" {}
