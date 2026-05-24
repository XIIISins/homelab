# terraform/netbox/vlans.tf
#
# All 10 VLANs scoped to site `home`. VID-as-key keeps the locals map
# self-documenting and gives a stable for_each key independent of name
# changes (renaming HL-ASG-K3S-NODE → something else doesn't move the
# resource address). VIDs match docs/architecture/network.md and
# CLAUDE.md's VLAN table.

locals {
  vlans = {
    "1"   = { name = "HL-MGMT", description = "Management" }
    "10"  = { name = "HL-ASG-VIP", description = "Asgard VIPs (keepalived)" }
    "11"  = { name = "HL-ASG-SVC", description = "Asgard LXCs" }
    "20"  = { name = "HL-ASG-K3S-VIP", description = "Asgard K3s MetalLB" }
    "21"  = { name = "HL-ASG-K3S-NODE", description = "Asgard K3s nodes (CPs + workers)" }
    "30"  = { name = "HL-JOT-K3S-VIP", description = "Jotunheim K3s MetalLB" }
    "31"  = { name = "HL-JOT-K3S-NODE", description = "Jotunheim K3s nodes" }
    "60"  = { name = "HL-CLIENT", description = "Personal devices" }
    "100" = { name = "HL-STOR", description = "Storage / NFS" }
    "222" = { name = "Untrusted", description = "Quarantine" }
  }

  # Import IDs sourced from /api/ipam/vlans/ at retrofit time. Re-derive
  # if NetBox is wiped + re-imported.
  vlan_import_ids = {
    "1"   = "1"
    "10"  = "2"
    "11"  = "3"
    "20"  = "4"
    "21"  = "5"
    "30"  = "6"
    "31"  = "7"
    "60"  = "8"
    "100" = "9"
    "222" = "10"
  }
}

resource "netbox_vlan" "this" {
  for_each = local.vlans

  vid         = tonumber(each.key)
  name        = each.value.name
  description = each.value.description
  site_id     = netbox_site.home.id
  status      = "active"
}

import {
  for_each = local.vlan_import_ids
  to       = netbox_vlan.this[each.key]
  id       = each.value
}
