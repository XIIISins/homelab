# terraform/adguard/rewrites.tf
#
# DNS rewrites pushed to AdGuard Home (Saga origin → fanned out to
# Mimir / Kvasir by adguardhome-sync). Each entry maps a fully-qualified
# domain to a single A/AAAA/CNAME answer. AGH does NOT support multiple
# answers per domain natively — to add a second A record for the same
# name (round-robin / fallback), you'd need a second physical AGH
# rewrite, but the gmichels/adguard provider's import ID format
# (`domain||answer`) gives us only one-per-key here. If multi-answer
# ever becomes a need, restructure the locals into a list-of-pairs and
# key the resource on `${domain}||${answer}` instead.
#
# Organization mirrors the CLAUDE.md "Key IPs" table — physical hosts,
# then trios/clusters, then VIPs/K8s-fronted services, grouped by zone.
#
# Zone semantics (from CLAUDE.md "Architectural invariants → DNS"):
#   - niflheim.xiiisins.com — internal-only. Records for every LXC/VM
#     and for K8s-fronted internal services. Pointed at Traefik VIP
#     (10.0.20.10) when the answer is a K8s-fronted FQDN.
#   - midgard.xiiisins.com — internal alias for publicly-reachable
#     services. AGH rewrites point at the Traefik VIP so LAN clients
#     skip the Cloudflare tunnel hop. External resolution goes through
#     Cloudflare (apex zone) — these midgard records are the
#     "internal-fast-path" of services that ALSO live on the apex.
#   - xiiisins.com — apex / external. Normally Cloudflare-resolved;
#     rewrites here are LAN bypasses that point bare-LXC services
#     directly at the LXC IP, skipping the tunnel entirely on LAN.

locals {
  rewrites = {
    # ── niflheim.xiiisins.com — physical hosts ─────────────────────
    "urd.niflheim.xiiisins.com"   = "10.0.254.11"
    "verd.niflheim.xiiisins.com"  = "10.0.254.12"
    "skuld.niflheim.xiiisins.com" = "10.0.254.13"
    "munin.niflheim.xiiisins.com" = "10.0.254.20"
    "pbs.niflheim.xiiisins.com"   = "10.0.11.20"

    # ── niflheim.xiiisins.com — AGH trio (DNS LXCs) ────────────────
    # adguard.* is a generic alias on the primary (Saga). adguard-vip.*
    # is the keepalived VIP — used by tooling that should follow the
    # active node rather than pin to a specific one.
    "saga.niflheim.xiiisins.com"         = "10.0.11.201"
    "mimir.niflheim.xiiisins.com"        = "10.0.11.202"
    "kvasir.niflheim.xiiisins.com"       = "10.0.11.203"
    "adguard.niflheim.xiiisins.com"      = "10.0.11.201"
    "adguard-vip.niflheim.xiiisins.com"  = "10.0.10.200"

    # ── niflheim.xiiisins.com — Asgard K3s control planes ──────────
    "gondul.niflheim.xiiisins.com" = "10.0.21.11"
    "hlokk.niflheim.xiiisins.com"  = "10.0.21.12"
    "sigrun.niflheim.xiiisins.com" = "10.0.21.13"

    # ── niflheim.xiiisins.com — Asgard K3s workers (eth0) ──────────
    # eth1 IPs (10.0.20.201/202/203) are the MetalLB advertisement
    # plane; we don't expose host records for them — workers reach
    # services by VIP/ClusterIP, not by worker eth1.
    "einherjar-urd.niflheim.xiiisins.com"   = "10.0.21.21"
    "einherjar-verd.niflheim.xiiisins.com"  = "10.0.21.22"
    "einherjar-skuld.niflheim.xiiisins.com" = "10.0.21.23"

    # ── niflheim.xiiisins.com — Postgres cluster ───────────────────
    "fulla.niflheim.xiiisins.com" = "10.0.11.230"
    "vor.niflheim.xiiisins.com"   = "10.0.11.231"
    "idunn.niflheim.xiiisins.com" = "10.0.11.232"

    # ── niflheim.xiiisins.com — HAProxy/etcd trio (Frigg's handmaidens) ──
    "hlin.niflheim.xiiisins.com"   = "10.0.11.233"
    "eir.niflheim.xiiisins.com"    = "10.0.11.234"
    "snotra.niflheim.xiiisins.com" = "10.0.11.235"

    # ── niflheim.xiiisins.com — VIPs ───────────────────────────────
    "pg17-vip.niflheim.xiiisins.com" = "10.0.10.210"

    # ── niflheim.xiiisins.com — K8s-fronted services (Traefik VIP) ──
    # When the answer is 10.0.20.10, the FQDN must also have a matching
    # CoreDNS rewrite (k8s/asgard/infrastructure/coredns-custom/) so
    # in-cluster pods can reach it without VIP tromboning. See CLAUDE.md
    # "In-cluster K8s-fronted FQDNs" gotcha.
    "factorio.niflheim.xiiisins.com"  = "10.0.11.220"
    "netbox.niflheim.xiiisins.com"    = "10.0.20.10"
    # Smoketest endpoint — backed by the apex-static Caddy pod with a
    # hostname-keyed site that returns "smoketest ok" + HTTP 200 to ANY
    # path. After any AGH change, `curl https://smoketest.niflheim.xiiisins.com/`
    # from a LAN/tailnet client gives a one-step confirmation that
    # rewrites landed end-to-end (resolution → Traefik routing → backend).
    # K8s side: k8s/asgard/apps/apex-static/{configmap.yaml,smoketest-httproute.yaml}.
    "smoketest.niflheim.xiiisins.com" = "10.0.20.10"

    # ── midgard.xiiisins.com — internal-fast-path for tunnelled svcs ──
    "authentik.midgard.xiiisins.com" = "10.0.20.10"

    # ── xiiisins.com — apex LAN bypass ─────────────────────────────
    # factorio is bare-LXC (no Traefik), so the LAN bypass points
    # straight at the LXC. K8s-fronted apex services would point at
    # 10.0.20.10 instead. External resolution still goes via Cloudflare
    # — this rewrite ONLY affects clients using AGH (LAN + tailnet).
    "factorio.xiiisins.com" = "10.0.11.220"
  }
}

# All rewrites share the same shape — one resource, for_each over the
# locals map. Default `enabled = true` is left implicit; if a rewrite
# ever needs to be temporarily disabled, switch to a richer locals
# value (object with answer + enabled fields) and reference each.value.x.
resource "adguard_rewrite" "this" {
  for_each = local.rewrites

  domain = each.key
  answer = each.value
}

# The one-shot `import {}` block that retrofitted the original 27
# hand-created rewrites was removed after a clean apply confirmed
# zero-diff state (2026-05-24). To retrofit any future hand-created
# rewrites, restore the block temporarily:
#
#   import {
#     for_each = local.rewrites
#     to       = adguard_rewrite.this[each.key]
#     id       = "${each.key}||${each.value}"
#   }
#
# Import ID format is `domain||answer` per gmichels/adguard docs.
