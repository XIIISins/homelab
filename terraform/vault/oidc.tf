# terraform/vault/oidc.tf
#
# Vault native OIDC auth method (Phase 6 Stage 1). Delegates human login
# to Authentik so the operator stops pasting the root token into the UI.
# The IdP side (OAuth2 provider + Application + vault-admins group gate)
# lives in terraform/authentik/vault.tf, which mints the client_id/secret
# and writes them to secret/k8s/vault/oidc — read here via data source.
#
# APPLY ORDER: terraform/authentik/ FIRST. It creates the Authentik
# Application (whose per-app OIDC discovery endpoint Vault fetches at
# config + login time) AND writes the secret/k8s/vault/oidc KV path the
# data source below reads. Planning this module before that apply fails
# on the missing KV path — expected; apply authentik, then this.
#
# Access model (single-tier, day-1): the Authentik Application's
# vault-admins policy binding is the authoritative gate — only members can
# complete the flow — so this role grants homelab-admin to anyone who
# authenticates via the vault client. bound_audiences pins acceptance to
# that client (a token minted for another Authentik app cannot be replayed
# here). A vault-readers tier later = a second Authentik app + a second
# role + a second policy.

data "vault_kv_secret_v2" "vault_oidc" {
  mount = "secret"
  name  = "k8s/vault/oidc"
}

# Human-lookup policy: read every secret + list for UI navigation.
#   - read   on secret/data/*     — KV v2 secret reads
#   - r+list on secret/metadata/* — KV v2 listing for the UI's browser
#   - update on sys/wrapping/wrap — the UI "wrap" action
# Deliberately NO create/update/delete on secret/* and NO sys/* admin —
# this is a lookup role, not an admin-of-Vault role. Mutating Vault config
# and writing secrets stays with the root token on the MacBook bootstrap
# path (the AppRole/TF rail), not this OIDC role.
resource "vault_policy" "homelab_admin" {
  name = "homelab-admin"

  policy = <<-EOT
    path "secret/data/*" {
      capabilities = ["read"]
    }
    path "secret/metadata/*" {
      capabilities = ["read", "list"]
    }
    path "sys/wrapping/wrap" {
      capabilities = ["update"]
    }
  EOT
}

resource "vault_jwt_auth_backend" "oidc" {
  path               = "oidc"
  type               = "oidc"
  oidc_discovery_url = "https://authentik.midgard.xiiisins.com/application/o/vault/"
  oidc_client_id     = data.vault_kv_secret_v2.vault_oidc.data["client_id"]
  oidc_client_secret = data.vault_kv_secret_v2.vault_oidc.data["client_secret"]
  default_role       = "homelab-admin"

  # Surface the OIDC method on the unauthenticated Vault UI login dropdown
  # so the operator gets a "Sign in with OIDC" option without typing the
  # mount path. The TTL/token_type fields are pinned to Vault's mount
  # defaults explicitly — omitting them lets Vault populate them on read,
  # which produced a perpetual `tune` diff on every plan (the role's own
  # token_ttl=1h governs issued tokens regardless of the mount default).
  tune {
    listing_visibility = "unauth"
    default_lease_ttl  = "768h"
    max_lease_ttl      = "768h"
    token_type         = "default-service"
  }
}

resource "vault_jwt_auth_backend_role" "homelab_admin" {
  backend         = vault_jwt_auth_backend.oidc.path
  role_name       = "homelab-admin"
  role_type       = "oidc"
  user_claim      = "email"
  bound_audiences = [data.vault_kv_secret_v2.vault_oidc.data["client_id"]]
  oidc_scopes     = ["openid", "email", "profile"]
  token_policies  = ["homelab-admin"]
  token_ttl       = 3600 # 1h working tokens

  allowed_redirect_uris = [
    "https://vault.niflheim.xiiisins.com/ui/vault/auth/oidc/oidc/callback",
    "http://localhost:8250/oidc/callback",
  ]
}
