# terraform/authentik/outpost.tf
#
# The embedded outpost (inside authentik-server) only answers ForwardAuth for proxy providers attached to it, so every
# `forward_single` provider must be listed here: a new provider without an entry loops the browser on the Authentik
# login page. This replaces the old one-time UI attach step.
#
# Ownership split on the one Authentik object:
#   - Terraform owns `protocol_providers` (this file).
#   - The blueprint k8s/asgard/infrastructure/authentik/blueprints/02-embedded-outpost.yaml owns `config`
#     (authentik_host etc.), so `config` is ignored here; Terraform must never write it.
#
# `protocol_providers` REPLACES the live list on apply: a proxy provider that exists in Authentik but is missing below is
# detached and its app starts looping. Add new `authentik_provider_proxy` resources here in the same change.
#
# Adopted with an import block (the outpost is auto-created by Authentik, id = its uuid). The block is idempotent once
# the object is in state and can be removed afterwards.

import {
  to = authentik_outpost.embedded
  id = "5a0ceb0a-e0c2-4c04-9b05-8fc42342f91e"
}

resource "authentik_outpost" "embedded" {
  name = "authentik Embedded Outpost"
  type = "proxy"
  protocol_providers = [
    authentik_provider_proxy.victorialogs.id,
    authentik_provider_proxy.victoriametrics.id,
    authentik_provider_proxy.microbin.id,
    authentik_provider_proxy.sonarr.id,
    authentik_provider_proxy.sabnzbd.id,
    authentik_provider_proxy.kavita_manga2.id,
  ]

  lifecycle {
    ignore_changes = [config]
  }
}
