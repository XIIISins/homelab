# terraform/proxmox/asgard-lxcs-root/provider.tf
#
# Single root@pam provider — this module is the bucket for LXCs whose
# create-time config touches Proxmox API endpoints that ONLY accept
# ticket auth (root@pam username + password), not API tokens. Today
# that's just device_passthrough (Tailscale's /dev/net/tun); future
# additions: fuse, keyctl, anything else from the "bpg/proxmox API
# token can change nesting, NOT other LXC features" gotcha class.
#
# Username pinned to root@pam, password sourced from PROXMOX_VE_PASSWORD
# in env (never literal in tfvars or chat). Fetch inline before apply,
# e.g. `PROXMOX_VE_PASSWORD="$(op read 'op://Homelab/Proxmox - root/password')" terraform apply`,
# so the literal value never lands in the transcript. (`homelab-env` does
# not currently load PROXMOX_VE_PASSWORD into the shared cache — adding it
# is open follow-up.)
provider "proxmox" {
  endpoint = var.proxmox_endpoint
  username = "root@pam"
  # password from PROXMOX_VE_PASSWORD env var
  insecure = true # self-signed cert
}
