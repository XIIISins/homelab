# terraform/aws/outputs.tf
#
# After first apply: capture the access_key_id + secret_access_key outputs and
# store them in 1Password as "Homelab - AWS - terraform-state IAM user".
# Every downstream module's operator/agent sources these creds as
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (or via an aws-cli profile) before
# running `terraform init` / `apply`.

output "state_bucket_name" {
  description = "Bucket name to use in downstream modules' backend blocks."
  value       = aws_s3_bucket.tfstate.id
}

output "state_bucket_region" {
  description = "Region to use in downstream modules' backend blocks."
  value       = var.aws_region
}

output "terraform_state_access_key_id" {
  description = "AWS access key ID for the terraform-state IAM user. Save to 1Password after first apply."
  value       = aws_iam_access_key.terraform_state.id
  sensitive   = true
}

output "terraform_state_secret_access_key" {
  description = "AWS secret access key for the terraform-state IAM user. Save to 1Password after first apply."
  value       = aws_iam_access_key.terraform_state.secret
  sensitive   = true
}

# -----------------------------------------------------------------------------
# Off-homelab backups (backups.tf)
# -----------------------------------------------------------------------------
# After apply, place the two key pairs in Vault WITHOUT echoing them — see
# docs/procedures/offsite-backups.md ("Mint + place credentials").

output "backup_bucket_name" {
  description = "Bucket holding etcd/ vault-raft/ calico/ recovery backups."
  value       = aws_s3_bucket.backups.id
}

output "k3s_etcd_backup_access_key_id" {
  description = "K3s etcd-s3 IAM user access key ID → Vault secret/ansible/backups/etcd-s3."
  value       = aws_iam_access_key.k3s_etcd_backup.id
  sensitive   = true
}

output "k3s_etcd_backup_secret_access_key" {
  description = "K3s etcd-s3 IAM user secret → Vault secret/ansible/backups/etcd-s3."
  value       = aws_iam_access_key.k3s_etcd_backup.secret
  sensitive   = true
}

output "backup_writer_access_key_id" {
  description = "CronJob writer IAM user access key ID → Vault secret/k8s/backups/aws-writer."
  value       = aws_iam_access_key.backup_writer.id
  sensitive   = true
}

output "backup_writer_secret_access_key" {
  description = "CronJob writer IAM user secret → Vault secret/k8s/backups/aws-writer."
  value       = aws_iam_access_key.backup_writer.secret
  sensitive   = true
}

output "backup_restore_user" {
  description = "Read-only restore IAM user. No access key is created by Terraform: run scripts/secrets/mint-restore-key (stores it in 1Password only)."
  value       = aws_iam_user.backup_restore.name
}
