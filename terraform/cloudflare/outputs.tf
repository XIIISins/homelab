output "tunnel_id" {
  description = "UUID of the asgard-k3s tunnel. Used as the `tunnel:` field in cloudflared's config.yaml (5e.2.e)."
  value       = cloudflare_zero_trust_tunnel_cloudflared.asgard.id
}
