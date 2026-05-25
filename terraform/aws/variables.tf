# terraform/aws/variables.tf
variable "aws_region" {
  description = "AWS region for the state bucket. Matches the region of the existing Vault KMS unseal key (eu-west-1) to keep blast radius in one place."
  type        = string
  default     = "eu-west-1"
}

variable "state_bucket_name" {
  description = "Globally-unique S3 bucket name for terraform state. Override if the default is already taken in S3's global namespace."
  type        = string
  default     = "xiiisins-homelab-tfstate"
}

variable "noncurrent_version_retention_days" {
  description = "How long to keep prior state versions after a new write. 90 days lets you recover from a corrupted apply detected weeks later, and keeps S3 storage cost negligible at homelab scale."
  type        = number
  default     = 90
}
