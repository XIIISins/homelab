# terraform/semaphore/provider.tf
#
# Semaphore admin API is internal-only at semaphore.niflheim.xiiisins.com
# (Traefik HTTPRoute on niflheim Gateway, K8s-fronted FQDN reachable
# from the operator's tailnet-joined MacBook via the AGH split-DNS
# entry pointing at the Traefik VIP). No port-forward needed (unlike
# garage which is admin-ClusterIP-only).
#
# Provider auth: SEMAPHOREUI_API_BASE_URL + SEMAPHOREUI_API_TOKEN are
# set by the homelab-env shim (env-map entries added 2026-05-26 — see
# .config/scripts/homelab.sh + .config/fish/conf.d/homelab.fish).
# Provider block stays empty so env vars are the single source of truth.
provider "semaphoreui" {}

provider "vault" {}

# === Vault data sources (operator-supplied secrets) ===
#
# All four paths seeded manually before first apply — see README.md
# "Bootstrap" section. TF reads from Vault rather than minting these
# because three of the four are operator-pasted (existing SSH keys,
# pre-existing ansible-vault password from 1P "Ansible Vault") and
# the fourth (GitHub deploy key) belongs in 1P+Vault end-to-end
# regardless of who created it.

data "vault_kv_secret_v2" "github_deploy_key" {
  mount = "secret"
  name  = "k8s/semaphore/github-deploy-key"
}

data "vault_kv_secret_v2" "host_ssh_key" {
  mount = "secret"
  name  = "k8s/semaphore/host-ssh-key"
}

data "vault_kv_secret_v2" "ansible_vault_password" {
  mount = "secret"
  name  = "k8s/semaphore/ansible-vault-password"
}

# ansible-awx AppRole creds for the community.hashi_vault lookup plugin
# inside playbooks. Shared with future AWX consumers per the design doc
# (per-service AppRole isolation deferred). secret_id rotates every 90d
# — operator re-mints + overwrites this path (see README.md "Rotation").
data "vault_kv_secret_v2" "vault_approle" {
  mount = "secret"
  name  = "k8s/semaphore/vault-approle"
}

# Hermod config-key — soft-auth gate for the /notify/<key> URL.
# Lives at the Hermod-owned path; this module just reads it to build
# the full URL passed to Semaphore's environment as HERMOD_URL.
data "vault_kv_secret_v2" "hermod_config_key" {
  mount = "secret"
  name  = "ansible/hermod/config-key"
}
