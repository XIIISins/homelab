# terraform/proxmox/zabbix-access/variables.tf
variable "proxmox_endpoint" {
  description = "Proxmox API endpoint URL (any cluster member — cluster auth propagates)"
  type        = string
  default     = "https://10.0.254.11:8006/api2/json"
}
