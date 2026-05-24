# terraform/tailscale/dns.tf
#
# Tailnet DNS — MagicDNS + split DNS + search paths.
#
# Before this file, the tailnet had no DNS configuration: clients used
# their local resolver unchanged, which made the internal zones
# (niflheim.xiiisins.com / midgard.xiiisins.com) NXDOMAIN from any
# off-LAN tailnet client. AdGuard at 10.0.10.200 was reachable via the
# subnet routers (Bifrost / Heimdall advertising 10.0.0.0/16) but
# nothing told clients to ask it.
#
# This module configures three things:
#
#   1. MagicDNS — turns on Tailscale's in-tailnet resolver
#      (100.100.100.100). Required for split DNS to take effect on
#      clients; without MagicDNS, split-DNS settings are silently
#      ignored because the OS resolver never consults Tailscale.
#
#   2. Split DNS — routes the internal zones to AdGuard. Clients send
#      matching queries to 100.100.100.100, which forwards them over
#      the tailnet (through a subnet router) to 10.0.10.200. All other
#      queries continue to use the client's local resolver — no global
#      override, no MITM of public DNS.
#
#   3. Search paths — pushes niflheim.xiiisins.com as a DNS suffix so
#      bare hostnames resolve (`ping vor` → `vor.niflheim.xiiisins.com`).
#
# Apex (xiiisins.com) is deliberately NOT split-DNS'd. Apex records
# point at Cloudflare (e.g. authentik.xiiisins.com → tunnel); we want
# tailnet clients to follow the same public path so the apex zone has
# one resolution behavior regardless of vantage point.
#
# midgard.xiiisins.com IS split-DNS'd because AdGuard rewrites
# (e.g. authentik.midgard → 10.0.20.10) only exist on AdGuard. Public
# DNS doesn't know about midgard records, so without split DNS those
# names also NXDOMAIN.

# ---------------------------------------------------------------------
# MagicDNS — gate that makes split DNS and search paths actually
# reach the client OS. Default is off.
# ---------------------------------------------------------------------
resource "tailscale_dns_preferences" "this" {
  magic_dns = true
}

# ---------------------------------------------------------------------
# Split DNS — per-zone nameservers.
#
# Single resolver target: the AdGuard VIP. Keepalived across
# Saga / Mimir / Kvasir handles failover. Backing node IPs
# (10.0.11.201/202/203) are deliberately NOT listed as fallbacks — the
# VIP is the published contract; listing the backings would couple
# tailnet config to internal HA topology and confuse failure modes
# (a client failing over from VIP to backing node masks a real VIP
# outage we'd want to see).
#
# Reachability: AdGuard VIP is on VLAN 10 (10.0.10.0/24), inside the
# 10.0.0.0/16 supernet auto-approved for tag:subnet-router. Clients
# reach it transparently via Bifrost or Heimdall.
# ---------------------------------------------------------------------
resource "tailscale_dns_split_nameservers" "internal_zones" {
  for_each = toset([
    "niflheim.xiiisins.com",
    "midgard.xiiisins.com",
  ])

  domain      = each.value
  nameservers = ["10.0.10.200"]
}

# ---------------------------------------------------------------------
# Search paths — DNS suffixes appended to bare hostnames.
#
# Only niflheim is pushed: it's where the actual host records live
# (LXCs, VMs). midgard is a service-alias zone — entries there are
# expected to be reached by full FQDN, and adding it as a second
# search path would create ambiguity for any name that exists in both
# zones (`ping authentik` could mean either).
# ---------------------------------------------------------------------
resource "tailscale_dns_search_paths" "this" {
  search_paths = ["niflheim.xiiisins.com"]
}
