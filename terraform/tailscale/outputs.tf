# terraform/tailscale/outputs.tf

# Tailnet name (resolved from the OAuth client's default tailnet).
# Useful as a sanity check after apply that we hit the right tailnet.
output "tailnet" {
  description = "Tailnet name resolved from OAuth client default."
  value       = "-"  # provider config uses `tailnet = -` (default)
}

# ACL resource ID (always literal "acl" — single policy file per tailnet).
output "acl_resource_id" {
  description = "Terraform-tracked ACL resource ID."
  value       = tailscale_acl.this.id
}
