# terraform/authentik/outputs.tf

# Used directly in Tailscale's "Use my own identity provider" config form:
#   Issuer URL: <output below>
#   Client ID + Client Secret: pulled from Vault path
#     secret/k8s/authentik/tailscale-client-secret
output "tailscale_oidc_issuer_url" {
  description = "OIDC issuer URL for Tailscale tailnet OIDC config. Must match the WebFinger response href."
  value       = "https://authentik.xiiisins.com/application/o/${authentik_application.tailscale.slug}/"
}

output "tailscale_oidc_client_id" {
  description = "OAuth2 client_id for the Tailscale Application. Stored alongside the secret in Vault."
  value       = authentik_provider_oauth2.tailscale.client_id
}
