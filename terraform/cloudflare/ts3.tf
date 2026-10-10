# terraform/cloudflare/ts3.tf
#
# Phase 10a1 / decision D2 — TeamSpeak failover DNS + the offsite node's
# name, brought under Terraform (overrides the old "leave hand-managed" note
# in docs/services/teamspeak.md). All five records PRE-EXIST (hand-created
# in the dashboard); the import blocks adopt them with their CURRENT values,
# so the first apply is a no-op on live traffic. Nothing here is proxied:
# TS3 is raw UDP, and DNS-only records avoid Cloudflare touching ACME/UDP.
#
# Why the IPs are variables (terraform.tfvars, gitignored), not literals:
#   - hel_ts3_ip is the home KPN public IP — never committed to this public
#     repo (known-issues/digitalocean.md).
#   - offsite_ip is the reserved IP of the offsite node (do1), behind do-ts3 +
#     do1. It was the CUTOVER LEVER at 10a3: changing it moved both names.
#
# SRV ring (`_ts3._udp.ts3.xiiisins.com`): priority 1 = homelab (hel-ts3),
# priority 99 = offsite failover (do-ts3). Clients fall through to 99 only
# when the homelab is unreachable.

variable "hel_ts3_ip" {
  description = "Home (KPN) public IPv4 behind hel-ts3.xiiisins.com. tfvars only — do not commit."
  type        = string
}

variable "offsite_ip" {
  description = "IPv4 behind do-ts3 + do1: the reserved IP (terraform/digitalocean output reserved_ip)."
  type        = string
}

locals {
  ts3_zone_id = data.cloudflare_zone.xiiisins.id

  # name => { ip var }. All DNS-only, ttl automatic.
  ts3_a_records = {
    "hel-ts3.xiiisins.com" = var.hel_ts3_ip
    "do-ts3.xiiisins.com"  = var.offsite_ip
    "do1.xiiisins.com"     = var.offsite_ip
  }

  # Record IDs of the pre-existing hand-made records (Cloudflare API, 2026-10-01).
  ts3_a_import_ids = {
    "hel-ts3.xiiisins.com" = "0094c769636e234bf5c3434e9e908150"
    "do-ts3.xiiisins.com"  = "24f380aa4d6a3a9c878ea74013327529"
    "do1.xiiisins.com"     = "e799c630ecdc7b6ba1b0b3ca503c1d14"
  }

  ts3_srv = {
    homelab = { priority = 1, weight = 100, target = "hel-ts3.xiiisins.com", import_id = "44002bcf43052c4ac6ca6ed9f686209f" }
    offsite = { priority = 99, weight = 1, target = "do-ts3.xiiisins.com", import_id = "e00ef4accab8af08d6aa2e4961bfa2c1" }
  }
}

resource "cloudflare_dns_record" "ts3_a" {
  for_each = local.ts3_a_records

  zone_id = local.ts3_zone_id
  name    = each.key
  type    = "A"
  content = each.value
  proxied = false
  ttl     = 1
  # Preserve the dashboard comment on the one record that has one.
  comment = each.key == "do-ts3.xiiisins.com" ? "backup teamspeak" : null
}

import {
  for_each = local.ts3_a_import_ids
  to       = cloudflare_dns_record.ts3_a[each.key]
  id       = "${local.ts3_zone_id}/${each.value}"
}

resource "cloudflare_dns_record" "ts3_srv" {
  for_each = local.ts3_srv

  zone_id = local.ts3_zone_id
  name    = "_ts3._udp.ts3.xiiisins.com"
  type    = "SRV"
  proxied = false
  ttl     = 1
  # Top-level priority mirrors data.priority; omitting it plans a spurious
  # `priority = N -> null` on the imported records.
  priority = each.value.priority
  data = {
    priority = each.value.priority
    weight   = each.value.weight
    port     = 9987
    target   = each.value.target
  }
}

import {
  for_each = local.ts3_srv
  to       = cloudflare_dns_record.ts3_srv[each.key]
  id       = "${local.ts3_zone_id}/${each.value.import_id}"
}
