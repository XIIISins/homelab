# terraform/netbox/provider.tf
#
# Authentication is env-driven so tokens never enter Git, tfvars, or the
# transcript. The operator's homelab.sh shim loads these from 1P /
# Vault before running terraform:
#
#   NETBOX_SERVER_URL=https://netbox.niflheim.xiiisins.com
#   NETBOX_API_TOKEN=<admin API token, v2 format: nbt_<key>.<token>>
#   VAULT_ADDR=http://10.0.20.11:8200
#   VAULT_TOKEN=<root token from 1Password item 7g4grolyien2yqkm7me2jficmy>
#
# The bootstrap admin token is minted once via Django shell against the
# running NetBox pod (the chart's superuser.api_token is NOT actually
# persisted as a DB Token row — see CLAUDE.md NetBox gotchas) and stored
# in 1P "Asgard - NetBox - admin API token". This is operator-consumed,
# so 1P (not Vault) is the right home per the Homelab vault scope rule.
#
# The originally-planned dedicated `terraform` NetBox user was dropped —
# see main.tf header for the provider-incompatibility rationale.

provider "netbox" {
  # server_url + api_token sourced from env vars NETBOX_SERVER_URL +
  # NETBOX_API_TOKEN. Provider supports both v1 (`Authorization: Token
  # <40-char>`) and v2 (`Authorization: Bearer nbt_<key>.<token>`)
  # auto-detected by the `nbt_` prefix — we use v2 since NetBox 4.4+
  # peppered tokens are configured (api_token_peppers in netbox-app
  # ExternalSecret, Phase 5i.3 Stage 0 fix).
}

provider "vault" {
  # VAULT_ADDR + VAULT_TOKEN from env.
}
