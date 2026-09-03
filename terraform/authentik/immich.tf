# terraform/authentik/immich.tf
#
# Authentik OIDC provider + Application for Immich. Group gate: members
# of immich-users (set via users.yaml) can authenticate through this
# Application.
#
# Immich has NO env-var/config-file path proven safe here for declarative
# OAuth activation (its config-file "oauth" block would need confirming
# whether it makes the Admin Settings UI read-only, which wasn't verifiable
# at design time — see open-questions.md "Immich"). So client_id/secret are
# minted to Vault as usual, but the operator pastes them into Immich's own
# Administration -> Settings -> OAuth screen as a one-time manual step
# (after first logging in via the local admin account Immich's first-run
# wizard creates) — same "operator does a one-time UI step" shape as
# NetBox's permission elevation. Automating this via the config file is a
# clean follow-up once the manual path is proven.
#
# Redirect URIs per https://docs.immich.app/administration/oauth: web
# needs /auth/login (initiates the flow) + /user-settings (re-auth from
# the settings page); mobile needs the app.immich:// custom scheme. Both
# the external apex and the internal midgard alias are registered (mirrors
# Outline) since Immich's web client computes the callback from whichever
# origin the browser used.

resource "random_password" "immich_client_secret" {
  length  = 64
  special = false
}

resource "authentik_provider_oauth2" "immich" {
  name          = "Immich"
  client_id     = "immich"
  client_secret = random_password.immich_client_secret.result
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
      url           = "https://immich.xiiisins.com/auth/login"
    },
    {
      matching_mode = "strict"
      url           = "https://immich.xiiisins.com/user-settings"
    },
    {
      matching_mode = "strict"
      url           = "https://immich.midgard.xiiisins.com/auth/login"
    },
    {
      matching_mode = "strict"
      url           = "https://immich.midgard.xiiisins.com/user-settings"
    },
    {
      matching_mode = "strict"
      url           = "app.immich:///oauth-callback"
    },
  ]
}

resource "authentik_application" "immich" {
  name               = "Immich"
  slug               = "immich"
  protocol_provider  = authentik_provider_oauth2.immich.id
  meta_launch_url    = "https://immich.xiiisins.com/"
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "immich_users_gate" {
  target = authentik_application.immich.uuid
  group  = authentik_group.this["immich-users"].id
  order  = 0
}

# Module owns the Vault KV entry for the secret it generates. Not consumed
# by an ExternalSecret today (manual paste — see header note); minted to
# Vault anyway per the standing "TF mints to Vault as primary" convention,
# and so the operator can `vault kv get secret/k8s/immich/oidc` instead of
# reading it back out of Terraform state.
resource "vault_kv_secret_v2" "immich_oidc" {
  mount = "secret"
  name  = "k8s/immich/oidc"
  data_json = jsonencode({
    client_id     = authentik_provider_oauth2.immich.client_id
    client_secret = random_password.immich_client_secret.result
    issuer_url    = "https://authentik.xiiisins.com/application/o/immich/"
  })
}
