# Smoke test for 5e.2.a: resolve the xiiisins.com zone.
# Confirms the provider authenticates and the token has the expected
# zone-scoped permissions (Zone:DNS:Edit + Zone:Zone:Read).
data "cloudflare_zone" "xiiisins" {
  filter = {
    name = "xiiisins.com"
  }
}

# -----------------------------------------------------------------------------
# 5e.2.d — Cloudflare Tunnel + apex DNS record + Vault KV for credentials
# -----------------------------------------------------------------------------

# 32-byte tunnel secret, generated via base64(sha256(random)). This is the
# format cloudflared expects in the `TunnelSecret` field of credentials.json.
# Stored in tfstate (accepted homelab-simplicity tradeoff — private repo).
resource "random_password" "tunnel_secret" {
  length  = 64
  special = false
}

locals {
  tunnel_secret_b64 = base64sha256(random_password.tunnel_secret.result)
}

# Locally-managed tunnel. `config_src = "local"` means ingress rules live in
# the cloudflared pod's config.yaml ConfigMap (Git-managed, 5e.2.e), not in
# the Cloudflare dashboard. This is the "Git is truth" decision row.
resource "cloudflare_zero_trust_tunnel_cloudflared" "asgard" {
  account_id    = var.cloudflare_account_id
  name          = "asgard-k3s"
  config_src    = "local"
  tunnel_secret = local.tunnel_secret_b64
}

# Apex CNAME pointing `authentik.xiiisins.com` at the tunnel. Internal LAN
# access goes via AdGuard rewrite to `authentik.midgard.xiiisins.com` (5e.2.h);
# this record is the public path: client → Cloudflare edge → tunnel →
# cloudflared pod → Traefik → Authentik.
#
# proxied = true is REQUIRED for tunnels — setting it to false would resolve
# the CNAME to an IP that does not accept tunnel traffic.
resource "cloudflare_dns_record" "authentik" {
  zone_id = data.cloudflare_zone.xiiisins.id
  name    = "authentik.xiiisins.com"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.asgard.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1 # 1 = automatic, required when proxied = true
}

# Write tunnel credentials.json to Vault. This is what ESO will pull from in
# 5e.2.e to materialize a K8s Secret for the cloudflared pods.
#
# KV shape: one key `credentials.json` whose value is the JSON blob in the
# format cloudflared expects (AccountTag / TunnelSecret / TunnelID). When ESO
# syncs it into a K8s Secret, the field name `credentials.json` becomes the
# filename mounted at `/etc/cloudflared/credentials/credentials.json`.
resource "vault_kv_secret_v2" "cloudflared_credentials" {
  mount = "secret"
  name  = "k8s/cloudflared/credentials"
  data_json = jsonencode({
    "credentials.json" = jsonencode({
      AccountTag   = var.cloudflare_account_id
      TunnelSecret = local.tunnel_secret_b64
      TunnelID     = cloudflare_zero_trust_tunnel_cloudflared.asgard.id
    })
  })
}
