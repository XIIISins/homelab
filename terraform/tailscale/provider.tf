# terraform/tailscale/provider.tf

# OAuth client credentials, manually planted in Vault (Tailscale UI
# generated them; we stored them via vault kv put). Per the module-
# ownership decision row: secrets a module generates → module writes
# them; secrets it consumes → reads them.
data "vault_kv_secret_v2" "tailscale_oauth" {
  mount = "secret"
  name  = "k8s/tailscale/oauth-client"
}

provider "tailscale" {
  oauth_client_id     = data.vault_kv_secret_v2.tailscale_oauth.data["client_id"]
  oauth_client_secret = data.vault_kv_secret_v2.tailscale_oauth.data["client_secret"]

  # Use the OAuth client's default tailnet (the one it was created in).
  # Avoids hard-coding the tailnet name and survives rename / org
  # migrations.
  tailnet = "-"

  # Scopes requested at token-issue time. Must be a subset of what the
  # OAuth client was created with in the UI:
  #   - policy_file:write (ACL)
  #   - devices:core:write
  #   - devices:routes:write
  #   - auth_keys:write
  # Provider auto-handles requesting these via the standard OAuth flow.
}

# VAULT_ADDR and VAULT_TOKEN from env (same as terraform/authentik,
# terraform/cloudflare).
provider "vault" {}
