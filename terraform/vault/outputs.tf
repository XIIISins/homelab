output "ansible_local_role_id" {
  description = "AppRole RoleID for ansible-local. Combined with a SecretID (generated separately, stored on disk + 1Password recovery copy) authenticates from MacBook for manual playbook runs. RoleIDs are not secret — they're identifiers."
  value       = vault_approle_auth_backend_role.ansible_local.role_id
}

output "ansible_awx_role_id" {
  description = "AppRole RoleID for ansible-awx. SecretID generated at AWX deploy time and stored in AWX's credential store."
  value       = vault_approle_auth_backend_role.ansible_awx.role_id
}
