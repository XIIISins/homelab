# terraform/proxmox/overview-access/main.tf
#
# The overview page's own read-only Proxmox identity (k8s/asgard/apps/overview/).
# The page shows hypervisor health next to the K3s numbers: node status, guest counts and
# CPU / memory / network history. Same shape as aiops-access/ and zabbix-access/ (stock
# PVEAuditor on /, privilege separation off), but a SEPARATE user and token so this
# page's access can be audited, rotated or revoked on its own.
#
# PVEAuditor has no write privileges at all. The pod never holds the token in the page:
# Caddy adds it to the request, and only two exact GET paths are routable
# (cluster/resources and nodes/<node>/rrddata; see the Caddyfile).
#
# Vault path `secret/k8s/overview/pve-token` carries token_id (USER@REALM!NAME) and the
# secret (just the UUID). External Secrets turns the two into the Authorization header
# value (k8s/asgard/apps/overview/externalsecret.yaml).

resource "proxmox_virtual_environment_user" "overview" {
  user_id = "overview@pve"
  comment = "Homelab overview page (read-only). Managed by terraform/proxmox/overview-access."
  enabled = true

  acl {
    path      = "/"
    propagate = true
    role_id   = "PVEAuditor"
  }
}

resource "proxmox_virtual_environment_user_token" "overview" {
  user_id    = proxmox_virtual_environment_user.overview.user_id
  token_name = "page"
  comment    = "Overview page read-only token. Managed by terraform/proxmox/overview-access."

  # Privilege separation OFF: the token inherits the user's ACL (PVEAuditor only).
  privileges_separation = false
}

resource "vault_kv_secret_v2" "overview_pve_token" {
  mount = "secret"
  name  = "k8s/overview/pve-token"

  data_json = jsonencode({
    token_id = "${proxmox_virtual_environment_user.overview.user_id}!${proxmox_virtual_environment_user_token.overview.token_name}"
    # bpg/proxmox returns the full `USER@REALM!NAME=UUID`; keep only the UUID (same
    # handling as zabbix-access/ and aiops-access/, see the 401 they document).
    secret = element(reverse(split("=", proxmox_virtual_environment_user_token.overview.value)), 0)
  })
}
