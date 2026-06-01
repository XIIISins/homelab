# terraform/authentik/n8n.tf
#
# Authentik Proxy Provider + Application gating the n8n EDITOR UI
# (n8n.niflheim.xiiisins.com). Same forward_single ForwardAuth pattern as
# the observability stack (observability.tf) and MicroBin (microbin.tf):
# a Traefik Middleware (k8s/asgard/apps/n8n/middleware-authentik-forward-
# auth.yaml) calls the embedded outpost; the outpost matches the request
# Host against external_host and applies the policy binding below.
#
# WHY ForwardAuth and not native OIDC: n8n's built-in SSO (SAML/OIDC) is an
# Enterprise-licensed feature — the community edition cannot do it. So SSO
# is enforced at the edge (Authentik gate in front of Traefik), with n8n's
# own owner account sitting behind it as defence-in-depth. See
# docs/services/n8n.md.
#
# SCOPE: this provider gates ONLY the internal editor host
# (n8n.niflheim.xiiisins.com). The EXTERNAL apex host (n8n.xiiisins.com) is
# webhook-only and deliberately NOT gated — external services POST to
# /webhook/* and must reach n8n without an Authentik round-trip. n8n
# authenticates webhooks per-workflow. The webhook paths live on a separate
# HTTPRoute with no ForwardAuth filter (k8s/asgard/apps/n8n/httproute.yaml).
#
# Gate: the n8n-admins group (groups.yaml; membership in users.yaml). No
# client secret / Vault write needed (proxy mode auth is browser↔Authentik
# with Traefik as the trusted proxy).

resource "authentik_provider_proxy" "n8n" {
  name               = "n8n"
  external_host      = "https://n8n.niflheim.xiiisins.com"
  mode               = "forward_single"
  authorization_flow = data.authentik_flow.authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.invalidation.id
}

resource "authentik_application" "n8n" {
  name               = "n8n"
  slug               = "n8n"
  protocol_provider  = authentik_provider_proxy.n8n.id
  meta_launch_url    = "https://n8n.niflheim.xiiisins.com/"
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "n8n_admins_gate" {
  target = authentik_application.n8n.uuid
  group  = authentik_group.this["n8n-admins"].id
  order  = 0
}

# -----------------------------------------------------------------------------
# Embedded outpost — manual provider attachment (one-time, per new provider)
# -----------------------------------------------------------------------------
# As with the observability + MicroBin providers, the embedded outpost needs
# the n8n proxy provider explicitly attached or the ForwardAuth endpoint
# 404s for n8n.niflheim.xiiisins.com. After the first apply:
#
#   1. Authentik admin UI → Applications → Outposts
#   2. Edit "authentik Embedded Outpost"
#   3. In Providers, add: n8n   →   Save
#
# (Or PATCH the embedded outpost via the API to append this provider id.)
# Symptom of forgetting: the editor loops on the login page or the outpost
# endpoint 500s.
