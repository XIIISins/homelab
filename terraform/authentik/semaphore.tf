# terraform/authentik/semaphore.tf
#
# Authentik OIDC provider + Application for Semaphore (Phase 5h.3 —
# Ansible orchestrator at semaphore.niflheim.xiiisins.com, internal-
# only). Group gate: members of `semaphore-admins` (set via
# users.yaml) can authenticate. Semaphore-side RBAC (Admin / User /
# per-project roles) is then driven by its own `role_claim` config
# reading the `groups` claim from the OIDC token — first-login
# Admin auto-assign rather than a manual escalation step (unlike
# NetBox's deferred SOCIAL_AUTH_PIPELINE override).
#
# Semaphore's OIDC callback path is
#   /api/auth/oidc/<provider-key>/redirect
# where <provider-key> is the key used in Semaphore's `oidc_providers`
# config map (Phase 5h.3.d ExternalSecret renders this). Convention:
# key = "authentik" → callback = /api/auth/oidc/authentik/redirect.
# Single hostname (internal-only), so just one redirect URI.
#
# Semaphore launches via its own UI at the niflheim hostname; no
# meta_launch_url override needed — the default Authentik portal
# tile will point at the issuer URL, which the user clicks through.

resource "random_password" "semaphore_client_secret" {
  length  = 64
  special = false
}

resource "authentik_provider_oauth2" "semaphore" {
  name          = "Semaphore"
  client_id     = "semaphore"
  client_secret = random_password.semaphore_client_secret.result
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
      url           = "https://semaphore.niflheim.xiiisins.com/api/auth/oidc/authentik/redirect"
    },
  ]
}

resource "authentik_application" "semaphore" {
  name               = "Semaphore"
  slug               = "semaphore"
  protocol_provider  = authentik_provider_oauth2.semaphore.id
  meta_launch_url    = "https://semaphore.niflheim.xiiisins.com/"
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "semaphore_admins_gate" {
  target = authentik_application.semaphore.uuid
  group  = authentik_group.this["semaphore-admins"].id
  order  = 0
}

# Module owns the Vault KV entry for the OIDC client credentials.
# Consumed by the Semaphore ExternalSecret in k8s/asgard/apps/semaphore/
# (Phase 5h.3.d Part 3, not in this draft).
resource "vault_kv_secret_v2" "semaphore_oidc" {
  mount = "secret"
  name  = "k8s/semaphore/oidc"
  data_json = jsonencode({
    client_id     = authentik_provider_oauth2.semaphore.client_id
    client_secret = random_password.semaphore_client_secret.result
    issuer_url    = "https://authentik.xiiisins.com/application/o/semaphore/"
  })
}
