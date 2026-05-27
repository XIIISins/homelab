# terraform/netbox/roles.tf
#
# Device + VM roles. NetBox 4.x shares the role list between devices
# and virtual machines — `vm_role` controls which contexts each role
# can be assigned to. Roles that only apply to physical devices
# (proxmox-host, nas, firewall) have vm_role=false; everything else
# is vm_role=true.
#
# Color: chart default grey (#9e9e9e) — colors aren't load-bearing in
# our model. Bring color discipline back if a NetBox dashboard or
# tag-based filter actually needs it.

locals {
  device_roles = {
    proxmox-host      = { vm_role = false, description = "Proxmox VE host" }
    nas               = { vm_role = false, description = "Network-attached storage" }
    firewall          = { vm_role = false, description = "Firewall / VLAN aggregator" }
    k3s-control-plane = { vm_role = true, description = "K3s control plane node (etcd + apiserver)" }
    k3s-worker        = { vm_role = true, description = "K3s worker node" }
    db                = { vm_role = true, description = "PostgreSQL cluster member" }
    dns               = { vm_role = true, description = "DNS server (AdGuard Home)" }
    tailscale-gateway = { vm_role = true, description = "Tailscale subnet router / exit node" }
    game-server       = { vm_role = true, description = "Game server (Factorio, etc.)" }
    backup-server     = { vm_role = true, description = "Proxmox Backup Server" }
    service-frontend  = { vm_role = true, description = "HAProxy + etcd + keepalived trio member" }
    monitoring        = { vm_role = true, description = "Monitoring (Zabbix, etc.)" }
    # notifications: added Phase 5h.2. No import_id — role doesn't exist
    # in NetBox yet, will be created on first apply.
    notifications = { vm_role = true, description = "Notification aggregation hub (AppriseAPI, Hermod)" }
  }

  # Import IDs sourced from /api/dcim/device-roles/ at retrofit time.
  device_role_import_ids = {
    proxmox-host      = "1"
    nas               = "2"
    firewall          = "3"
    k3s-control-plane = "4"
    k3s-worker        = "5"
    db                = "6"
    dns               = "7"
    tailscale-gateway = "8"
    game-server       = "9"
    backup-server     = "10"
    service-frontend  = "11"
    monitoring        = "12"
  }
}

resource "netbox_device_role" "this" {
  for_each = local.device_roles

  name        = each.key
  slug        = each.key
  color_hex   = "9e9e9e"
  vm_role     = each.value.vm_role
  description = each.value.description
}

import {
  for_each = local.device_role_import_ids
  to       = netbox_device_role.this[each.key]
  id       = each.value
}
