# terraform/proxmox/aiops-access/main.tf
#
# The AIOps Toolbelt API's own read-only Proxmox identity (Phase 10d2, `pve` tool):
# answers "is the hypervisor alive, and which guests share it" - the core of
# host-vs-workload in a diagnosis. Same shape as zabbix-access/ (stock PVEAuditor
# on /, privilege separation off), but a SEPARATE user and token so the agent's
# access can be audited, rotated or revoked without touching Zabbix's.
#
# PVEAuditor has no write privileges at all; the negative test (aiops/tests +
# docs/procedures/aiops-diagnosis.md "Negative tests") attempts a guest stop with
# this token and expects a 403.
#
# Vault path `secret/ansible/aiops/pve-token` carries token_id (USER@REALM!NAME) and
# the secret (just the UUID). The Frigg root loader (roles/aiops-toolbelt) copies it
# to a 0400 tmpfs file for the Toolbelt service; nothing static on disk.

resource "proxmox_virtual_environment_user" "aiops_toolbelt" {
  user_id = "aiops@pve"
  comment = "AIOps Toolbelt API (read-only). Managed by terraform/proxmox/aiops-access."
  enabled = true

  acl {
    path      = "/"
    propagate = true
    role_id   = "PVEAuditor"
  }
}

resource "proxmox_virtual_environment_user_token" "aiops_toolbelt" {
  user_id    = proxmox_virtual_environment_user.aiops_toolbelt.user_id
  token_name = "toolbelt"
  comment    = "Toolbelt API read-only token. Managed by terraform/proxmox/aiops-access."

  # Privilege separation OFF: the token inherits the user's ACL (PVEAuditor only).
  privileges_separation = false
}

resource "vault_kv_secret_v2" "aiops_pve_token" {
  mount = "secret"
  name  = "ansible/aiops/pve-token"

  data_json = jsonencode({
    token_id = "${proxmox_virtual_environment_user.aiops_toolbelt.user_id}!${proxmox_virtual_environment_user_token.aiops_toolbelt.token_name}"
    # bpg/proxmox returns the full `USER@REALM!NAME=UUID`; keep only the UUID (same
    # handling as zabbix-access/, see the 401 it documents).
    secret = element(reverse(split("=", proxmox_virtual_environment_user_token.aiops_toolbelt.value)), 0)
  })
}
