output "factorio_lxc_id" {
  description = "Proxmox VM ID of the Factorio LXC"
  value       = proxmox_virtual_environment_container.factorio.vm_id
}

output "factorio_lxc_ip" {
  description = "Management IP (VLAN 11) of the Factorio LXC"
  value       = "10.0.11.220"
}

output "factorio_lxc_node" {
  description = "Proxmox node hosting the Factorio LXC"
  value       = proxmox_virtual_environment_container.factorio.node_name
}
