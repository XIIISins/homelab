# terraform/netbox/custom_fields.tf
#
# Custom field definitions for the VirtualMachine object type.
#
# VMID — cross-reference to the Proxmox virtual-machine ID (vmid)
# for LXCs + VMs. Hand-created during 5i.e as type=integer, which is
# semantically correct but TF-incompatible: provider issue #349
# (open since 2026, not in v5.3.0) means e-breuninger/netbox cannot
# write to integer-typed custom_fields on VMs — the Map(String)
# schema fails NetBox's strict integer coercion. Recreated as type=text
# so the provider works; numeric discipline is preserved TF-side via
# validation_regex = "^[0-9]*$".
#
# Required is also relaxed to false because the AGH trio (Saga/Mimir/
# Kvasir, manually installed) doesn't have a known VMID yet; Phase
# 5b.2 will retrofit when AdGuard lifts into IaC.
#
# The 17 hand-entered VMID values are NOT lost: they're already
# declared in local.vms in vms.tf (mirroring terraform/proxmox/*/
# lxcs.tf locals), so the VM apply step re-populates NetBox after
# the field's recreation.

resource "netbox_custom_field" "vmid" {
  name             = "VMID"
  type             = "text" # see header for why text, not integer
  content_types    = ["virtualization.virtualmachine"]
  required         = false # AGH trio (no IaC vmid yet) gets empty
  label            = "VMID"
  description      = "Proxmox VM ID (vmid). Cross-references the LXC/VM record in the corresponding terraform/proxmox/* module."
  validation_regex = "^[0-9]*$" # digits-only or empty (numeric-string discipline restored TF-side)
}

import {
  to = netbox_custom_field.vmid
  id = "1"
}
