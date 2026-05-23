# terraform/tailscale/authkeys.tf
#
# Pre-authorized, reusable, auto-renewing authkeys for the Tailscale
# LXCs (Bifrost / Heimdall / Gjallarbru). Each key is scoped to a
# single tag (the consuming LXC's role) and written to Vault for the
# Ansible role to fetch at provisioning time.
#
# Per the module-ownership decision row: this module mints the keys,
# so this module writes them to Vault. The Ansible tailscale role
# reads them via community.hashi_vault lookup.
#
# Tailscale's API caps auth-key lifetime at 90 days — there is no
# truly indefinite option (design-doc "indefinite key" language pre-
# dated this discovery; see auth-key gotcha in CLAUDE.md). Mitigation
# combines four flags:
#
#   reusable             = true       Multiple `tailscale up` invocations
#                                     (Ansible re-runs, LXC rebuilds) use
#                                     the same key without consuming it.
#   ephemeral            = false      Nodes persist if offline — we want
#                                     stable identity, not auto-cleanup.
#   preauthorized        = true       No admin click required (device
#                                     approval is on for this tailnet via
#                                     custom OIDC).
#   recreate_if_invalid  = "always"   The next `terraform apply` after a
#                                     key expires or is revoked will mint
#                                     a fresh one and update the Vault
#                                     entry, making the rebuild-an-LXC
#                                     workflow self-healing as long as
#                                     it goes TF → Ansible, in that order.
#
# Defensive `lifecycle { ignore_changes = [expiry] }`: pre-PR-#521 the
# provider's read-back of the API-normalized expiry caused a diff flap
# on every plan (resource looked like it wanted to recreate). Fixed in
# provider v0.29.0+; we're on v0.28.0 (SDKv2 era; 0.29.0 was the Plugin
# Framework migration), so keep the block as belt-and-braces. Safe on
# fixed-provider versions too — `expiry` will only ever read 7776000.
#
# Operator rule: rebuilding a Tailscale-joined LXC 90+ days after
# initial mint requires `terraform apply` HERE before re-running the
# Ansible role. Otherwise the role reads a stale expired key from
# Vault and `tailscale up` fails with `auth key expired`.
#
# Vault paths (read by Ansible):
#   secret/k8s/tailscale/authkeys/bifrost
#   secret/k8s/tailscale/authkeys/heimdall
#   secret/k8s/tailscale/authkeys/gjallarbru

locals {
  # Each LXC's role determines its single tag. Matches the LXC tags
  # auto-approved by policy.hujson (autoApprovers.routes for
  # tag:subnet-router, autoApprovers.exitNode for tag:exit-node).
  tailscale_nodes = {
    bifrost    = { tag = "subnet-router" }
    heimdall   = { tag = "subnet-router" }
    gjallarbru = { tag = "exit-node" }
  }
}

resource "tailscale_tailnet_key" "lxc" {
  for_each = local.tailscale_nodes

  preauthorized       = true
  reusable            = true
  ephemeral           = false
  recreate_if_invalid = "always"

  # Tailscale API hard-caps at 90 days. The provider defaults to this
  # when omitted; explicit value makes the constraint visible.
  expiry = 7776000

  tags        = ["tag:${each.value.tag}"]
  description = "${each.key} ${each.value.tag} managed by terraform"

  lifecycle {
    # See file header — defensive against pre-#521 expiry-drift flap.
    ignore_changes = [expiry]
  }
}

# Vault writes the freshly-minted key. The Ansible role reads from
# secret/k8s/tailscale/authkeys/<hostname> via community.hashi_vault
# lookup. data_json is a single-field object; the lookup pulls
# .authkey out of the resulting map.
resource "vault_kv_secret_v2" "lxc_authkey" {
  for_each = local.tailscale_nodes

  mount = "secret"
  name  = "ansible/tailscale/authkeys/${each.key}"

  data_json = jsonencode({
    authkey = tailscale_tailnet_key.lxc[each.key].key
  })
}
