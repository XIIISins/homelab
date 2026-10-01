# terraform/vault/backups.tf
#
# Daily Vault Raft snapshot → S3 (off-homelab). The CronJob in
# k8s/asgard/apps/backups/ logs in with its own ServiceAccount via the
# Kubernetes auth method — deliberately NOT AppRole: a SecretID would expire
# (see the ansible-frigg silent-expiry incident, 2026-09-03) and need a
# rotation helper; a projected SA token never does.
#
# Policy is the single read on the snapshot endpoint — it cannot read KV,
# touch auth methods, or generate-root. (Vault 2.x note: this endpoint is
# unaffected by the generate-root/rekey authentication change, see
# docs/operations/vault-2x-assessment.md.)
#
# The S3 write credential the Job uploads with is NOT here: it is minted by
# terraform/aws (IAM user homelab-backup-writer) and placed at
# secret/k8s/backups/aws-writer by the operator (docs/procedures/offsite-backups.md).

resource "vault_policy" "vault_snapshot" {
  name = "vault-snapshot"

  policy = <<-EOT
    path "sys/storage/raft/snapshot" {
      capabilities = ["read"]
    }
  EOT
}

resource "vault_kubernetes_auth_backend_role" "vault_snapshot" {
  backend                          = vault_auth_backend.kubernetes.path
  role_name                        = "vault-snapshot"
  bound_service_account_names      = ["vault-snapshot"]
  bound_service_account_namespaces = ["backups"]
  token_policies                   = [vault_policy.vault_snapshot.name]
  token_ttl                        = 600
  token_max_ttl                    = 900
}
