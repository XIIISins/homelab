# terraform/proxmox/asgard-pools/provider.tf
#
# root@pam ticket auth: creating users, roles, pools, ACLs and tokens needs Administrator-level privileges that an
# API token does not carry (same model as aiops-access/ and asgard-lxcs-root/). Password from PROXMOX_VE_PASSWORD
# (inline 1P fetch at apply time, so the literal never lands in a transcript):
#   PROXMOX_VE_PASSWORD="$(op read 'op://Homelab 2.0/Proxmox - root/password')" terraform apply
provider "proxmox" {
  endpoint = var.proxmox_endpoint
  username = "root@pam"
  # password from PROXMOX_VE_PASSWORD env var
  insecure = true # self-signed cert
}

# Writes the minted token's secret to Vault. Auth via VAULT_TOKEN (the operator's own login).
provider "vault" {}
