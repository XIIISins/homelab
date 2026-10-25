# terraform/authentik/kavita.tf
#
# Authentik Proxy Provider + Application gating the manga2 Kavita instance at manga2.niflheim.xiiisins.com behind the
# `manga2` group. Same forward_single pattern as media.tf; the Traefik Middleware is k8s/asgard/apps/kavita/
# middleware-authentik-forward-auth.yaml. The regular instance (manga.niflheim.xiiisins.com) has no Authentik gate:
# Kavita's own accounts are its only login, which keeps OPDS and mobile readers working.
#
# The provider is attached to the embedded outpost in outpost.tf (add every new proxy provider there; forgetting it
# loops the browser on the Authentik login page).

resource "authentik_provider_proxy" "kavita_manga2" {
  name               = "Kavita Manga2"
  external_host      = "https://manga2.niflheim.xiiisins.com"
  mode               = "forward_single"
  authorization_flow = data.authentik_flow.authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.invalidation.id
}

resource "authentik_application" "kavita_manga2" {
  name               = "Kavita Manga2"
  slug               = "kavita-manga2"
  protocol_provider  = authentik_provider_proxy.kavita_manga2.id
  meta_launch_url    = "https://manga2.niflheim.xiiisins.com/"
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

resource "authentik_policy_binding" "kavita_manga2_manga2_gate" {
  target = authentik_application.kavita_manga2.uuid
  group  = authentik_group.this["manga2"].id
  order  = 0
}
