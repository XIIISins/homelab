# terraform/authentik/outline.tf
#
# Authentik OIDC provider + Application for Outline. Group gate:
# members of outline-users (set via users.yaml) can authenticate
# through this Application. Outline-side role assignment (admin /
# member / viewer) happens inside Outline by workspace admins after
# first OIDC login — Outline has no OIDC-claim → role mapping today.
#
# Outline's OIDC callback path is /auth/oidc.callback (NOT the
# /oauth/complete/oidc/ that python-social-auth uses for NetBox).
# Three redirect URIs registered — external apex, internal midgard
# alias, and internal niflheim — because Outline computes the callback
# from its URL env var and a user opening any of the three hostnames
# triggers a redirect from that exact origin.

resource "random_password" "outline_client_secret" {
  length  = 64
  special = false
}

resource "authentik_provider_oauth2" "outline" {
  name          = "Outline"
  client_id     = "outline"
  client_secret = random_password.outline_client_secret.result
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
      url           = "https://wiki.xiiisins.com/auth/oidc.callback"
    },
    {
      matching_mode = "strict"
      url           = "https://wiki.midgard.xiiisins.com/auth/oidc.callback"
    },
    {
      matching_mode = "strict"
      url           = "https://wiki.niflheim.xiiisins.com/auth/oidc.callback"
    },
  ]
}

# Outline is exposed at both midgard (LAN bypass / public via
# cloudflared) and niflheim (internal-only). The user-portal launch URL
# points at the public apex (wiki.xiiisins.com) so off-LAN users land
# on the public-reachable hostname.
resource "authentik_application" "outline" {
  name               = "Outline"
  slug               = "outline"
  protocol_provider  = authentik_provider_oauth2.outline.id
  meta_launch_url    = "https://wiki.xiiisins.com/"
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "outline_users_gate" {
  target = authentik_application.outline.uuid
  group  = authentik_group.this["outline-users"].id
  order  = 0
}

# Module owns the Vault KV entry for the secret it generates.
# Consumed by Outline's ExternalSecret in k8s/asgard/apps/outline/.
resource "vault_kv_secret_v2" "outline_oidc" {
  mount = "secret"
  name  = "k8s/outline/oidc"
  data_json = jsonencode({
    client_id     = authentik_provider_oauth2.outline.client_id
    client_secret = random_password.outline_client_secret.result
  })
}
