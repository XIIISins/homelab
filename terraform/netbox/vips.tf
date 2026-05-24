# terraform/netbox/vips.tf
#
# Three standalone /32 VIP records — IP addresses with no owning
# interface, used as virtual-IP holders. NetBox lets ip_address rows
# exist unattached for this exact pattern (anycast, VIP reservation,
# load-balancer fronts).
#
# `role = "vip"` for all three (NetBox's specific value for keepalived/
# MetalLB-style floating IPs). The procedure originally suggested
# `anycast` but `vip` more precisely captures "a single owner at a
# time, decided by the floating mechanism."

locals {
  vips = {
    agh_vip = {
      address     = "10.0.10.200/32"
      dns_name    = "adguard-vip.niflheim.xiiisins.com"
      description = "AGH primary VIP, keepalived-floated across Saga/Mimir/Kvasir on VLAN 10. VRID 51."
    }
    pg_vip = {
      address     = "10.0.10.210/32"
      dns_name    = "pg17-vip.niflheim.xiiisins.com"
      description = "PostgreSQL write endpoint, keepalived-floated across Hlin/Eir/Snotra on VLAN 10. VRID 61. HAProxy routes to current Patroni leader."
    }
    traefik_vip = {
      address     = "10.0.20.10/32"
      dns_name    = "traefik-vip.niflheim.xiiisins.com"
      description = "K3s edge LB, MetalLB L2 announcement on workers' eth1 (VLAN 20)."
    }
  }

  vip_import_ids = {
    agh_vip     = "28"
    pg_vip      = "29"
    traefik_vip = "30"
  }
}

resource "netbox_ip_address" "vip" {
  for_each = local.vips

  ip_address  = each.value.address
  status      = "active"
  role        = "vip"
  dns_name    = each.value.dns_name
  description = each.value.description
}

import {
  for_each = local.vip_import_ids
  to       = netbox_ip_address.vip[each.key]
  id       = each.value
}
