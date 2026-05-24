# terraform/cloudflare/security.tf
#
# Zone security posture: re-enable bot/integrity protections that were
# globally disabled during 5e.3.c to unblock Tailscale's WebFinger probe,
# with per-path WAF skip rules for protocol-public endpoints so the same
# class of probe doesn't 403 again.
#
# Apply order is two-phase ON PURPOSE:
#   1. terraform apply --target='cloudflare_ruleset.zone_custom_skip'
#      Lands the skip rule first. No protection change yet; probes still
#      work because protections are still off.
#   2. terraform apply
#      Re-enables browser_check + bot fight mode. Skip rule from phase 1
#      is already live, so protocol-public paths bypass them cleanly.
#
# Doing both in one apply would race: TF parallelises siblings, and the
# window between bot_management flip and ruleset create is where probes
# would 403. Two applies, no race.

# ---------------------------------------------------------------------------
# Phase 1 — skip ruleset
# ---------------------------------------------------------------------------
#
# The entrypoint ruleset for phase http_request_firewall_custom is auto-
# created by Cloudflare at zone setup. It must be imported into this
# resource before first apply:
#
#   terraform import 'cloudflare_ruleset.zone_custom_skip' \
#     '<zone_id>/<ruleset_id>'
#
# Zone + ruleset IDs are in the Cloudflare API discovery; ruleset ID for
# this zone as of import: 6604b5a39abe4afc93edb3c437f76e03

resource "cloudflare_ruleset" "zone_custom_skip" {
  zone_id     = data.cloudflare_zone.xiiisins.id
  name        = "default"
  description = "Zone custom rules: skip bot/WAF/integrity for protocol-public endpoints"
  kind        = "zone"
  phase       = "http_request_firewall_custom"

  rules = [
    {
      ref         = "skip_protocol_public"
      description = "WebFinger + Authentik OIDC endpoints (per-app discovery lives under /application/o/<slug>/, not at host root)"
      enabled     = true
      action      = "skip"
      expression  = "(http.host eq \"xiiisins.com\" and http.request.uri.path eq \"/.well-known/webfinger\") or (http.host eq \"authentik.xiiisins.com\" and starts_with(http.request.uri.path, \"/application/o/\"))"
      action_parameters = {
        ruleset = "current"
        phases = [
          "http_ratelimit",
          "http_request_firewall_managed",
          "http_request_sbfm",
        ]
        products = [
          "bic",
          "hot",
          "rateLimit",
          "securityLevel",
          "uaBlock",
          "waf",
          "zoneLockdown",
        ]
      }
    }
  ]
}

# ---------------------------------------------------------------------------
# Phase 2 — re-enable protections
# ---------------------------------------------------------------------------

resource "cloudflare_zone_setting" "browser_check" {
  zone_id    = data.cloudflare_zone.xiiisins.id
  setting_id = "browser_check"
  value      = "on"
}

# Bot Fight Mode. Free plan only supports fight_mode; the *_protection
# fields are Enterprise-tier and stay disabled (their current state).
# Explicit "disabled" values match the API's existing response so TF
# doesn't try to flip them.
resource "cloudflare_bot_management" "xiiisins" {
  zone_id                 = data.cloudflare_zone.xiiisins.id
  fight_mode              = true
  ai_bots_protection      = "disabled"
  content_bots_protection = "disabled"
  crawler_protection      = "disabled"
}
