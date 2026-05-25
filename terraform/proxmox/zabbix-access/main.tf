# terraform/proxmox/zabbix-access/main.tf
#
# Zabbix monitoring user + API token for the Proxmox VE cluster. The
# `Proxmox VE by HTTP` Zabbix template makes server-side API calls
# (item type 19) to read VM/LXC/storage/cluster state — needs an
# authenticated API token. Tokens minted via API don't carry the
# user's privileges automatically; we attach PVEAuditor (stock Proxmox
# read-only role) via an ACL grant on the user.
#
# Vault path `secret/ansible/proxmox/zabbix-token` carries both the
# token_id (USER@REALM!TOKENNAME format the template expects) and
# the secret value, written from TF outputs. The zabbix-agent role
# reads both at register time and sets them as {$PVE.TOKEN.ID} +
# {$PVE.TOKEN.SECRET} host macros on each Proxmox host record.
#
# Single user/token serves all three nodes — the API is cluster-aware
# so a token created on one node authenticates against all of them.
# Per-host {$PVE.URL.HOST} macro (set in group_vars/proxmox_hosts.yml
# via {{ ansible_host }}) makes the template scrape the correct node.

resource "proxmox_virtual_environment_user" "zabbix_monitoring" {
  user_id = "zabbix-monitor@pve"
  comment = "Zabbix monitoring (read-only). Managed by terraform/proxmox/zabbix-access."
  enabled = true

  # PVEAuditor on / with propagate=true gives read access to the entire
  # datacenter tree (nodes/VMs/storage/cluster/etc.) without write
  # privileges. Stock Proxmox role — no custom role needed.
  acl {
    path      = "/"
    propagate = true
    role_id   = "PVEAuditor"
  }
}

resource "proxmox_virtual_environment_user_token" "zabbix_monitoring" {
  user_id    = proxmox_virtual_environment_user.zabbix_monitoring.user_id
  token_name = "zabbix"
  comment    = "Zabbix scrape token. Managed by terraform/proxmox/zabbix-access."

  # Privilege separation OFF — token inherits the user's ACLs directly.
  # With this true, the token would need its own ACL grant on top of the
  # user's, which adds friction without security benefit for a read-only
  # monitoring token. The user itself is scoped to PVEAuditor.
  privileges_separation = false
}

resource "vault_kv_secret_v2" "zabbix_pve_token" {
  mount = "secret"
  name  = "ansible/proxmox/zabbix-token"

  data_json = jsonencode({
    # token_id format the PVE Zabbix template expects: USER@REALM!TOKENNAME
    token_id = "${proxmox_virtual_environment_user.zabbix_monitoring.user_id}!${proxmox_virtual_environment_user_token.zabbix_monitoring.token_name}"
    secret   = proxmox_virtual_environment_user_token.zabbix_monitoring.value
  })
}
