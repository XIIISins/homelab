# terraform/garage/provider.tf
#
# Garage admin API is ClusterIP-only (k8s/asgard/infrastructure/garage/services.yaml).
# The operator runs this module from the MacBook control node, so we
# reach the admin endpoint via a kubectl port-forward established
# before `terraform plan/apply`:
#
#   kubectl port-forward -n garage svc/garage-admin 3903:3903 &
#
# Documented in docs/services/garage.md ("Operator workflow") so this
# isn't a surprise on first apply.
#
# Vault provider auth: VAULT_ADDR + VAULT_TOKEN from the operator's
# environment (set by `homelab-env`, same as every other TF module here).
provider "garage" {
  endpoint = "http://localhost:3903"
  token    = data.vault_kv_secret_v2.garage_server.data["admin_token"]
}

provider "vault" {}

data "vault_kv_secret_v2" "garage_server" {
  mount = "secret"
  name  = "k8s/garage/server"
}
