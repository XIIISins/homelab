provider "proxmox" {
  endpoint  = "https://10.0.254.11:8006"
  api_token = var.proxmox_api_token
  insecure  = true  # self-signed cert, change later with proper cert
}
provider "proxmox" {
  alias    = "root"
  endpoint = var.proxmox_endpoint
  username = "root@pam"
  # password from PROXMOX_VE_PASSWORD env var
  insecure = true   # matches default block, presumably
}
