# terraform/authentik/netbox.tf
#
# Authentik OIDC provider + application for NetBox. Group gates:
# members of netbox-admins OR netbox-viewers (set via users.yaml) can
# authenticate via this Application. NetBox-side permissions (admin
# vs view-only) are assigned manually in the NetBox UI after first
# OIDC login — automatic group→permission sync from OIDC claims
# requires a custom SOCIAL_AUTH_PIPELINE override in NetBox config,
# deferred per the 5i.c research notes.
#
# python-social-auth (NetBox 4.6's auth backend) requires the
# redirect URI's trailing slash exactly: /oauth/complete/oidc/

# -----------------------------------------------------------------------------
# Client secret + provider + application + policy bindings
# -----------------------------------------------------------------------------

resource "random_password" "netbox_client_secret" {
  length  = 64
  special = false
}

resource "authentik_provider_oauth2" "netbox" {
  name          = "NetBox"
  client_id     = "netbox"
  client_secret = random_password.netbox_client_secret.result
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
      url           = "https://netbox.niflheim.xiiisins.com/oauth/complete/oidc/"
    },
  ]
}

# The Application launchable from Authentik's user portal. NetBox is
# internal-only — launch URL is the NetBox FQDN (resolved via AGH
# rewrite to Traefik VIP), open_in_new_tab=false so the portal-link
# behaves like every other in-tab navigation.
#
# policy_engine_mode = "any" → the two group bindings below are OR'd
# (membership in EITHER netbox-admins OR netbox-viewers grants access
# at the OIDC layer). Explicit even though "any" is the current
# Authentik default — guards against the default changing upstream.
resource "authentik_application" "netbox" {
  name               = "NetBox"
  slug               = "netbox"
  protocol_provider  = authentik_provider_oauth2.netbox.id
  meta_launch_url    = "https://netbox.niflheim.xiiisins.com/"
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "netbox_admins_gate" {
  target = authentik_application.netbox.uuid
  group  = authentik_group.this["netbox-admins"].id
  order  = 0
}

resource "authentik_policy_binding" "netbox_viewers_gate" {
  target = authentik_application.netbox.uuid
  group  = authentik_group.this["netbox-viewers"].id
  order  = 10
}

# -----------------------------------------------------------------------------
# Vault write — module owns the KV entry for the secret it generates.
# Consumed by NetBox's ExternalSecret (see k8s/asgard/apps/netbox/).
# -----------------------------------------------------------------------------

resource "vault_kv_secret_v2" "netbox_oidc" {
  mount = "secret"
  name  = "k8s/netbox/oidc-client-secret"
  data_json = jsonencode({
    client_id     = authentik_provider_oauth2.netbox.client_id
    client_secret = random_password.netbox_client_secret.result
  })
}
