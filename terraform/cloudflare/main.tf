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

resource "cloudflare_dns_record" "apex" {
  zone_id = data.cloudflare_zone.xiiisins.id
  name    = "xiiisins.com"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.asgard.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

# Outline wiki — public apex hostname tunnelled through cloudflared.
# Cloudflared ingress rule lives in k8s/asgard/infrastructure/cloudflared/configmap.yaml.
# Internal LAN bypass via AGH rewrite (terraform/adguard/) to Traefik VIP.
resource "cloudflare_dns_record" "outline" {
  zone_id = data.cloudflare_zone.xiiisins.id
  name    = "wiki.xiiisins.com"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.asgard.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

# Zabbix (Hugin) — public apex hostname tunnelled through cloudflared.
# Phase 8c.5 (WAN ingress). Cloudflared ingress rule lives in
# k8s/asgard/infrastructure/cloudflared/configmap.yaml — pattern mirrors
# Outline + Authentik (target https://traefik.traefik.svc.cluster.local
# with httpHostHeader: hugin.xiiisins.com + noTLSVerify: true). Internal
# LAN clients use hugin.midgard.xiiisins.com via AGH rewrite (skipping
# the Cloudflare hop). Authentik SAML round-trip terminates back at the
# user's original host (apex OR midgard alias), per the ACS URL match
# on the authentik_provider_saml resource — meaning external WAN logins
# and internal midgard logins both work end-to-end through the same
# Zabbix frontend.
resource "cloudflare_dns_record" "hugin" {
  zone_id = data.cloudflare_zone.xiiisins.id
  name    = "hugin.xiiisins.com"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.asgard.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}

# Startpage — public personal homepage tunnelled through cloudflared.
# Cloudflared ingress rule lives in k8s/asgard/infrastructure/cloudflared/
# configmap.yaml (home.xiiisins.com → Traefik backchannel). External-only:
# no AGH rewrite / LAN bypass, by operator choice.
resource "cloudflare_dns_record" "startpage" {
  zone_id = data.cloudflare_zone.xiiisins.id
  name    = "home.xiiisins.com"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.asgard.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1 # 1 = automatic, required when proxied = true
}

# MicroBin — public pastebin/file-share tunnelled through cloudflared.
# Cloudflared ingress rule in k8s/asgard/infrastructure/cloudflared/
# configmap.yaml (paste.xiiisins.com → Traefik backchannel). Anonymous
# create/view; /pastalist + /admin gated by Authentik (terraform/authentik/
# microbin.tf). External-only.
resource "cloudflare_dns_record" "microbin" {
  zone_id = data.cloudflare_zone.xiiisins.id
  name    = "paste.xiiisins.com"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.asgard.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1 # 1 = automatic, required when proxied = true
}

# n8n — public WEBHOOK endpoint tunnelled through cloudflared. Only the
# /webhook/* paths are routed at this hostname (k8s/asgard/apps/n8n/
# httproute.yaml exposes nothing else here) so third-party services can
# deliver inbound webhooks; the editor UI is internal-only at
# n8n.niflheim.xiiisins.com (Authentik ForwardAuth). Cloudflared ingress
# rule lives in k8s/asgard/infrastructure/cloudflared/configmap.yaml. LAN
# clients use the n8n.midgard.xiiisins.com AGH rewrite to skip the tunnel.
resource "cloudflare_dns_record" "n8n" {
  zone_id = data.cloudflare_zone.xiiisins.id
  name    = "n8n.xiiisins.com"
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
