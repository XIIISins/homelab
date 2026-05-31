# terraform/proxmox/asgard-vms/variables.tf
variable "proxmox_api_token" {
  description = "Proxmox API token in format user@realm!tokenid=secret"
  type        = string
  sensitive   = true
}

variable "ssh_public_key" {
  description = "SSH public key seeded for the ansible user via cloud-init"
  type        = string
}
