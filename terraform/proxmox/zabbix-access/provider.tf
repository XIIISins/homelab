# terraform/proxmox/zabbix-access/provider.tf
#
# root@pam ticket auth — Proxmox user/role/token management requires
# Administrator-level privileges that API tokens minted via the UI
# don't carry. Mirrors the auth model in asgard-lxcs-root/. Password
# from PROXMOX_VE_PASSWORD env var (inline 1P fetch at apply time):
#   PROXMOX_VE_PASSWORD="$(op read 'op://Homelab 2.0/Proxmox - root/password')" terraform apply
# so the literal never lands in the transcript.
provider "proxmox" {
  endpoint = var.proxmox_endpoint
  username = "root@pam"
  # password from PROXMOX_VE_PASSWORD env var
  insecure = true # self-signed cert
}

# Vault provider — writes the minted API token's secret value to a
# Vault path that the zabbix-agent role reads at register time. Auth
# via the standard VAULT_TOKEN env var (already in the homelab-env
# cache).
provider "vault" {}
