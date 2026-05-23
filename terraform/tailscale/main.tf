# terraform/tailscale/main.tf

# Tailnet policy file managed by Terraform. The ACL content lives in
# policy.hujson next to this file — HuJSON syntax (JSON + comments +
# trailing commas) is supported natively by the `acl` field.
#
# IMPORT REQUIRED before first apply:
#
#   terraform import tailscale_acl.this acl
#
# The provider rejects writes to a non-default ACL without an import
# first (defensive — prevents accidentally clobbering an existing
# policy). The "id" for the import is the literal string `acl` — the
# tailnet only has one policy file.
#
# After import, terraform plan will show whatever drift exists between
# the imported state (current Tailscale UI content) and policy.hujson.
# Resolve drift by either:
#   - Editing policy.hujson to match the current Tailscale state
#     (preserves any manual UI edits), then apply (no-op)
#   - Accepting policy.hujson as truth and letting apply overwrite
#     (loses manual UI edits — fine if we just planted the bootstrap
#     tagOwners and nothing else)
resource "tailscale_acl" "this" {
  acl = file("${path.module}/policy.hujson")

  # reset_acl_on_destroy: on `terraform destroy`, restore Tailnet's
  # default policy. Without this, destroy leaves the last-applied
  # policy in place (which would lock us out if the policy was
  # restrictive and we no longer have TF to fix it).
  reset_acl_on_destroy = true

  # overwrite_existing_content: leave at default (false). With false,
  # the import-first requirement protects against accidental overwrite.
  # Set to true only in an emergency-recovery scenario.
}
