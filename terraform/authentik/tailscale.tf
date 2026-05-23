# terraform/authentik/tailscale.tf
#
# Authentik OIDC provider + application for Tailscale tailnet OIDC.
# Group binding gates access: only members of `tailscale-users` (set
# via users.yaml) can authenticate.

# -----------------------------------------------------------------------------
# Built-in Authentik objects referenced by the OAuth2Provider.
# -----------------------------------------------------------------------------

data "authentik_flow" "authorization_implicit_consent" {
  slug = "default-provider-authorization-implicit-consent"
}

data "authentik_flow" "invalidation" {
  slug = "default-provider-invalidation-flow"
}

# Authentik's bundled self-signed signing cert. ES256 by default.
# Adequate for OIDC token signing — Tailscale validates the signature
# against the JWKS endpoint Authentik exposes, not against a CA chain.
data "authentik_certificate_key_pair" "default" {
  name = "authentik Self-signed Certificate"
}

# Default OIDC scope mappings (managed objects, ship with Authentik).
# The `profile` scope's default expression already includes a `groups`
# claim populated from the user's group memberships — that's the day-1
# ACL hook Tailscale needs (no custom PropertyMapping required).
data "authentik_property_mapping_provider_scope" "openid" {
  name = "authentik default OAuth Mapping: OpenID 'openid'"
}

data "authentik_property_mapping_provider_scope" "email" {
  name = "authentik default OAuth Mapping: OpenID 'email'"
}

data "authentik_property_mapping_provider_scope" "profile" {
  name = "authentik default OAuth Mapping: OpenID 'profile'"
}

data "authentik_property_mapping_provider_scope" "offline_access" {
  name = "authentik default OAuth Mapping: OpenID 'offline_access'"
}

# -----------------------------------------------------------------------------
# Client secret + provider + application + policy binding
# -----------------------------------------------------------------------------

resource "random_password" "tailscale_client_secret" {
  length  = 64
  special = false
}

# The OAuth2Provider Tailscale uses for OIDC. Tailscale's discovery
# endpoint is at https://authentik.xiiisins.com/application/o/tailscale/
# — derived from the Application slug below.
#
# sub_mode = "user_email": Tailscale binds users by email at tailnet
# OIDC switchover. Picking email here lets the existing GitHub-bound
# user (ghost@xiiisins.com) carry over instead of creating a new
# opaque Tailscale user.
resource "authentik_provider_oauth2" "tailscale" {
  name          = "Tailscale"
  client_id     = "tailscale"
  client_secret = random_password.tailscale_client_secret.result
  client_type   = "confidential"
  sub_mode      = "user_email"

  authorization_flow = data.authentik_flow.authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.invalidation.id
  signing_key        = data.authentik_certificate_key_pair.default.id

  property_mappings = [
    data.authentik_property_mapping_provider_scope.openid.id,
    data.authentik_property_mapping_provider_scope.email.id,
    data.authentik_property_mapping_provider_scope.profile.id,
    data.authentik_property_mapping_provider_scope.offline_access.id,
  ]

  allowed_redirect_uris = [
    {
      matching_mode = "strict"
      url           = "https://login.tailscale.com/a/oauth_response"
    },
  ]
}

# The Application launchable from Authentik's user portal, bound to the
# OAuth2Provider above. Slug is used in Authentik's OIDC issuer URL:
# https://authentik.xiiisins.com/application/o/<slug>/
resource "authentik_application" "tailscale" {
  name              = "Tailscale"
  slug              = "tailscale"
  protocol_provider = authentik_provider_oauth2.tailscale.id
  meta_launch_url   = "https://login.tailscale.com/start"
  open_in_new_tab   = true
}

# Gate: only members of tailscale-users can use this Application. Other
# users get "you don't have access" if they try to launch it. Group
# membership is set in users.yaml.
resource "authentik_policy_binding" "tailscale_group_gate" {
  target = authentik_application.tailscale.uuid
  group  = authentik_group.this["tailscale-users"].id
  order  = 0
}

# -----------------------------------------------------------------------------
# Vault write — module owns the KV entry for the secret it generates.
# -----------------------------------------------------------------------------

resource "vault_kv_secret_v2" "tailscale_oidc" {
  mount = "secret"
  name  = "k8s/authentik/tailscale-client-secret"
  data_json = jsonencode({
    client_id     = authentik_provider_oauth2.tailscale.client_id
    client_secret = random_password.tailscale_client_secret.result
  })
}
