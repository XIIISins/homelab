# terraform/authentik/main.tf
#
# Cross-cutting data sources / smoke tests live here. Identity (users,
# groups) is in identity.tf. Tailscale (OAuth2Provider + Application) is
# in tailscale.tf.

# 5e.3.a smoke test — confirms provider auth on every plan/apply.
data "authentik_flow" "default_authentication" {
  slug = "default-authentication-flow"
}
