# terraform/authentik/observability.tf
#
# Authentik Proxy Providers + Applications for the Phase-7 observability
# stack — VictoriaMetrics (vmui) at metric.niflheim.xiiisins.com and
# VictoriaLogs at logs.niflheim.xiiisins.com. Both internal-only, gated
# by the `monitoring-admins` group.
#
# Pattern: `forward_single` proxy provider (one per app). The Authentik
# embedded outpost (inside authentik-server pod) provides the
# ForwardAuth endpoint at /outpost.goauthentik.io/auth/traefik; Traefik
# Middleware (k8s/asgard/apps/monitoring-namespace/middleware-
# authentik-forward-auth.yaml) calls it on every request to the
# observability HTTPRoutes.
#
# `forward_single` vs `forward_domain`: forward_single matches the
# request's Host header against external_host PER provider, so we can
# bind each app to its own policy. forward_domain shares one cookie +
# one policy across all apps in a domain (would need to share the
# monitoring-admins gate across both, which we want anyway — but
# forward_single keeps per-app gating possible if VL/VM ever need
# different ACLs).
#
# No client secret resource needed (proxy mode doesn't use OIDC client
# credentials — auth is between Authentik and the user's browser, with
# Traefik as the trusted proxy). No Vault write needed.

# -----------------------------------------------------------------------------
# VictoriaMetrics
# -----------------------------------------------------------------------------

resource "authentik_provider_proxy" "victoriametrics" {
  name               = "VictoriaMetrics"
  external_host      = "https://metric.niflheim.xiiisins.com"
  mode               = "forward_single"
  authorization_flow = data.authentik_flow.authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.invalidation.id
}

resource "authentik_application" "victoriametrics" {
  name              = "VictoriaMetrics"
  slug              = "victoriametrics"
  protocol_provider = authentik_provider_proxy.victoriametrics.id
  meta_launch_url   = "https://metric.niflheim.xiiisins.com/"
  open_in_new_tab   = false
  # `any` semantics across the policy bindings (only one binding today,
  # but the explicit value guards against future Authentik default changes).
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "victoriametrics_admins_gate" {
  target = authentik_application.victoriametrics.uuid
  group  = authentik_group.this["monitoring-admins"].id
  order  = 0
}

# -----------------------------------------------------------------------------
# VictoriaLogs
# -----------------------------------------------------------------------------

resource "authentik_provider_proxy" "victorialogs" {
  name               = "VictoriaLogs"
  external_host      = "https://logs.niflheim.xiiisins.com"
  mode               = "forward_single"
  authorization_flow = data.authentik_flow.authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.invalidation.id
}

resource "authentik_application" "victorialogs" {
  name               = "VictoriaLogs"
  slug               = "victorialogs"
  protocol_provider  = authentik_provider_proxy.victorialogs.id
  meta_launch_url    = "https://logs.niflheim.xiiisins.com/"
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "victorialogs_admins_gate" {
  target = authentik_application.victorialogs.uuid
  group  = authentik_group.this["monitoring-admins"].id
  order  = 0
}

# -----------------------------------------------------------------------------
# Embedded outpost
# -----------------------------------------------------------------------------
# The embedded outpost only answers ForwardAuth for proxy providers attached to it. The attachment is Terraform-managed
# in outpost.tf (every `forward_single` provider in this module must be listed there).
