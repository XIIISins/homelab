# terraform/authentik/media.tf
#
# Authentik Proxy Providers + Applications for the media automation stack (plan 5h M1): Sonarr at
# sonarr.niflheim.xiiisins.com and SABnzbd at sabnzbd.niflheim.xiiisins.com. Internal-only, gated by `media-admins`.
# Same forward_single pattern as observability.tf; the Traefik Middleware is k8s/asgard/apps/media/middleware-
# authentik-forward-auth.yaml. Both apps are configured for external auth, so this is the only login.
#
# One-time UI step after the FIRST apply (see the end of observability.tf for why this is not TF-managed): Authentik
# admin -> Applications -> Outposts -> "authentik Embedded Outpost" -> add the Sonarr + SABnzbd providers -> Save.
# Symptom of forgetting it: the browser loops on the Authentik login page.

resource "authentik_provider_proxy" "sonarr" {
  name               = "Sonarr"
  external_host      = "https://sonarr.niflheim.xiiisins.com"
  mode               = "forward_single"
  authorization_flow = data.authentik_flow.authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.invalidation.id
}

resource "authentik_application" "sonarr" {
  name               = "Sonarr"
  slug               = "sonarr"
  protocol_provider  = authentik_provider_proxy.sonarr.id
  meta_launch_url    = "https://sonarr.niflheim.xiiisins.com/"
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "sonarr_media_admins_gate" {
  target = authentik_application.sonarr.uuid
  group  = authentik_group.this["media-admins"].id
  order  = 0
}

resource "authentik_provider_proxy" "sabnzbd" {
  name               = "SABnzbd"
  external_host      = "https://sabnzbd.niflheim.xiiisins.com"
  mode               = "forward_single"
  authorization_flow = data.authentik_flow.authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.invalidation.id
}

resource "authentik_application" "sabnzbd" {
  name               = "SABnzbd"
  slug               = "sabnzbd"
  protocol_provider  = authentik_provider_proxy.sabnzbd.id
  meta_launch_url    = "https://sabnzbd.niflheim.xiiisins.com/"
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "sabnzbd_media_admins_gate" {
  target = authentik_application.sabnzbd.uuid
  group  = authentik_group.this["media-admins"].id
  order  = 0
}
