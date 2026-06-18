# terraform/proxmox/asgard-vms/provider.tf
#
# API-token auth (same token + tfvars as asgard-k3s / asgard-lxcs).
# Frigg needs no root@pam-only create-time features (a VM has its own
# kernel → native /dev/net/tun for Tailscale, unlike the LXCs), so the
# plain API-token provider suffices — no aliased root provider here.
provider "proxmox" {
  endpoint  = "https://10.0.254.11:8006"
  api_token = var.proxmox_api_token
  insecure  = true # self-signed cert
}
