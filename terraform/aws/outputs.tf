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
