# terraform/aws/provider.tf
#
# Credentials come from env (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) or
# ~/.aws/credentials. The IAM principal used to apply this module is the
# bootstrap admin user — distinct from the narrow terraform-state user this
# module mints for downstream modules to consume.
#
# See README at the bottom of main.tf for the bootstrap sequence.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      ManagedBy = "terraform"
      Repo      = "xiiisins/homelab"
      Module    = "terraform/aws"
    }
  }
}
