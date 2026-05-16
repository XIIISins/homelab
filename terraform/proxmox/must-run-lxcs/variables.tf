variable "proxmox_api_token" {
  description = "Proxmox API token in format user@realm!tokenid=secret"
  type        = string
  sensitive   = true
}

variable "ssh_public_key" {
  description = "SSH public key for the ansible user inside each LXC"
  type        = string
}

variable "lxc_template" {
  description = "Container template path. Check with `pveam list local | grep debian`."
  type        = string
  default     = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"
}

variable "lxc_storage" {
  description = "Storage pool ID for LXC root volume"
  type        = string
  default     = "local-lvm"
}

variable "lxc_network_bridge" {
  description = "Proxmox bridge for VLAN-tagged interfaces"
  type        = string
  default     = "vmbr0"
}
