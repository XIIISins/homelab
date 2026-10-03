# terraform/proxmox/asgard-pools/variables.tf
variable "proxmox_endpoint" {
  description = "Proxmox API endpoint URL (any cluster member: cluster auth propagates)"
  type        = string
  default     = "https://10.0.254.11:8006/api2/json"
}

variable "canary_pool_id" {
  description = "PVE resource pool the canary LXCs live in (terraform/proxmox/asgard-lxcs sets pool_id to this literal)"
  type        = string
  default     = "aiops-canary"
}

variable "guest_storage_ids" {
  description = "Storage IDs a rebuilt canary's rootfs is allocated on (lxc_storage in asgard-lxcs)"
  type        = list(string)
  default     = ["local-lvm"]
}

variable "template_storage_ids" {
  description = "Storage IDs holding the container template (lxc_template in asgard-lxcs): read access only"
  type        = list(string)
  default     = ["local"]
}

variable "sdn_zone_id" {
  description = "SDN zone the guest bridge belongs to (a plain vmbr0 lives in the implicit `localnetwork` zone)"
  type        = string
  default     = "localnetwork"
}

variable "canary_vmids" {
  description = "VMIDs of the canary LXCs (asgard-lxcs canary_nodes). The runner token gets the guest role (not just audit) on exactly these paths, so a DESTROYED canary (it leaves the pool) can be refreshed and re-created."
  type        = list(number)
  default     = [1190, 1191, 1192]
}
