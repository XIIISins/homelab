# terraform/authentik/microbin.tf
#
# Authentik Proxy Provider + Application for MicroBin's gated routes
# (/pastalist + /admin at paste.xiiisins.com). Same forward_single
# ForwardAuth pattern as the observability stack (observability.tf):
# Traefik Middleware (k8s/asgard/apps/microbin/middleware-authentik-
# forward-auth.yaml) calls the embedded outpost on requests to those
# paths; the outpost matches the request Host against external_host and
# applies the policy binding below.
#
# Only /pastalist + /admin are routed through the Middleware (see the
# microbin HTTPRoute) — anonymous create/view/upload is NOT gated. So
# this provider only ever sees the trusted-janitor traffic.
#
# Gate: the paste-janitors group (groups.yaml; membership in users.yaml).
# No client secret / Vault write needed (proxy mode auth is browser↔
# Authentik with Traefik as trusted proxy).

resource "authentik_provider_proxy" "microbin" {
  name               = "MicroBin"
  external_host      = "https://paste.xiiisins.com"
  mode               = "forward_single"
  authorization_flow = data.authentik_flow.authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.invalidation.id
}

resource "authentik_application" "microbin" {
  name               = "MicroBin"
  slug               = "microbin"
  protocol_provider  = authentik_provider_proxy.microbin.id
  meta_launch_url    = "https://paste.xiiisins.com/pastalist"
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "microbin_janitors_gate" {
  target = authentik_application.microbin.uuid
  group  = authentik_group.this["paste-janitors"].id
  order  = 0
}

# -----------------------------------------------------------------------------
# Embedded outpost
# -----------------------------------------------------------------------------
# The MicroBin provider is attached to the embedded outpost in outpost.tf; without the attachment ForwardAuth 404s for
# paste.xiiisins.com.
