# terraform/authentik/vault.tf
#
# Authentik OIDC provider + Application for HashiCorp Vault's UI (Phase 6
# Stage 1). The Vault UI is fronted by Traefik at
# vault.niflheim.xiiisins.com (internal-only) and uses Vault's native OIDC
# auth method (terraform/vault/oidc.tf) to delegate human login to
# Authentik — replacing "operator pastes the root token into the UI".
#
# Access gate: the vault-admins policy binding below is the AUTHORITATIVE
# gate — only members can complete the authorization flow for this
# Application, so Vault's homelab-admin role can grant its policy to anyone
# who authenticates here (pinned to this client via bound_audiences)
# without re-checking a group claim. Single-tier, day-1. A vault-readers
# tier later = a second group + a second Application/provider + a second
# Vault role; no groups-claim plumbing needed for the current model.
#
# Discovery host is authentik.midgard.xiiisins.com — the in-cluster
# CoreDNS rewrite (k8s/asgard/infrastructure/coredns-custom/) lets the
# Vault pod reach it without VIP tromboning, the same backchannel path
# NetBox uses. The browser-facing callbacks are on vault.niflheim (the
# operator is always on LAN/tailnet for this internal-only UI) and
# localhost:8250 (the `vault login -method=oidc` CLI helper listener).

resource "random_password" "vault_client_secret" {
  length  = 64
  special = false
}

resource "authentik_provider_oauth2" "vault" {
  name          = "Vault"
  client_id     = "vault"
  client_secret = random_password.vault_client_secret.result
  client_type   = "confidential"
  sub_mode      = "user_email"

  authorization_flow = data.authentik_flow.authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.invalidation.id
  signing_key        = data.authentik_certificate_key_pair.default.id

  property_mappings = [
    data.authentik_property_mapping_provider_scope.openid.id,
    data.authentik_property_mapping_provider_scope.email.id,
    data.authentik_property_mapping_provider_scope.profile.id,
  ]

  allowed_redirect_uris = [
    {
      matching_mode = "strict"
      # Vault UI's built-in OIDC callback (auth mount path "oidc").
      url = "https://vault.niflheim.xiiisins.com/ui/vault/auth/oidc/oidc/callback"
    },
    {
      matching_mode = "strict"
      # Vault CLI helper listener — `vault login -method=oidc`.
      url = "http://localhost:8250/oidc/callback"
    },
  ]
}

resource "authentik_application" "vault" {
  name               = "Vault"
  slug               = "vault"
  protocol_provider  = authentik_provider_oauth2.vault.id
  meta_launch_url    = "https://vault.niflheim.xiiisins.com/ui/"
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "vault_admins_gate" {
  target = authentik_application.vault.uuid
  group  = authentik_group.this["vault-admins"].id
  order  = 0
}

# Module owns the Vault KV entry for the OIDC client creds it generates.
# Consumed by terraform/vault/oidc.tf (data source) to wire the auth
# method. Apply order: this module FIRST, then terraform/vault/.
resource "vault_kv_secret_v2" "vault_oidc" {
  mount = "secret"
  name  = "k8s/vault/oidc"
  data_json = jsonencode({
    client_id     = authentik_provider_oauth2.vault.client_id
    client_secret = random_password.vault_client_secret.result
  })
}
