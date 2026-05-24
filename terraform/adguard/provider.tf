# terraform/adguard/provider.tf
#
# Authentication is env-driven so the admin password never enters Git,
# tfvars, or the transcript. The operator's homelab.sh / homelab.fish
# shim loads these from 1Password before running terraform:
#
#   ADGUARD_HOST=10.0.11.201   (Saga — the origin; see main.tf for why)
#   ADGUARD_SCHEME=http        (AGH listener is plaintext on the LAN)
#   ADGUARD_USERNAME=ghost     (from 1P "Adguard - admin")
#   ADGUARD_PASSWORD=<sensitive> (from 1P "Adguard - admin")
#
# All four are populated by `homelab-env` from the static + 1P maps.
# AGH admin creds are operator-consumed (humans log in via the UI with
# the same account) so 1P is the right home per the Homelab vault scope
# rule — not Vault.

provider "adguard" {
  # All four fields sourced from env vars (ADGUARD_HOST / ADGUARD_SCHEME
  # / ADGUARD_USERNAME / ADGUARD_PASSWORD). Leaving the block empty is
  # the documented way to opt fully into env-driven config.
}
