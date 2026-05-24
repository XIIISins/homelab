# terraform/netbox/prefixes.tf
#
# One /24 prefix per VLAN. Prefix-as-key (CIDR string) is the natural
# identifier — VID is what links it to a VLAN, CIDR is what makes the
# prefix itself unique. The `vid` field on each entry is the lookup key
# into local.vlans (NOT the netbox vid number value — those happen to
# match here but the local.vlans map is keyed by stringified VID).
#
# site_id deliberately UNSET: NetBox-side reality has prefixes scoped
# only via their VLAN, not directly to the site. The VLAN already
# carries the site relationship. Adding site_id to prefixes is an
# enrichment we can do later if a NetBox view/dashboard needs it.

locals {
  prefixes = {
    "10.0.254.0/24" = { vlan = "1" }
    "10.0.10.0/24"  = { vlan = "10" }
    "10.0.11.0/24"  = { vlan = "11" }
    "10.0.20.0/24"  = { vlan = "20" }
    "10.0.21.0/24"  = { vlan = "21" }
    "10.0.30.0/24"  = { vlan = "30" }
    "10.0.31.0/24"  = { vlan = "31" }
    "10.0.60.0/24"  = { vlan = "60" }
    "10.0.100.0/24" = { vlan = "100" }
    "10.0.222.0/24" = { vlan = "222" }
  }

  # Import IDs sourced from /api/ipam/prefixes/ at retrofit time.
  prefix_import_ids = {
    "10.0.254.0/24" = "1"
    "10.0.10.0/24"  = "2"
    "10.0.11.0/24"  = "3"
    "10.0.20.0/24"  = "4"
    "10.0.21.0/24"  = "5"
    "10.0.30.0/24"  = "6"
    "10.0.31.0/24"  = "7"
    "10.0.60.0/24"  = "8"
    "10.0.100.0/24" = "9"
    "10.0.222.0/24" = "10"
  }
}

resource "netbox_prefix" "this" {
  for_each = local.prefixes

  prefix  = each.key
  status  = "active"
  vlan_id = netbox_vlan.this[each.value.vlan].id
}

import {
  for_each = local.prefix_import_ids
  to       = netbox_prefix.this[each.key]
  id       = each.value
}
