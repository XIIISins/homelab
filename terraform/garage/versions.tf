# terraform/garage/versions.tf
terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket       = "xiiisins-homelab-tfstate"
    key          = "garage/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    # Community provider, but the most mature TF option for Garage today.
    # First-party doesn't exist; AWS provider works for raw object I/O
    # but Garage's bucket-permission model is per-key (not S3-shaped) so
    # bucket lifecycle would have to bypass the AWS provider anyway.
    # See CLAUDE.md "Terraform — garage provider" gotchas (to be added
    # post-deploy) for known quirks.
    garage = {
      source  = "jkossis/garage"
      version = "1.0.4"
    }
    vault = {
      source  = "hashicorp/vault"
      version = "4.8.0"
    }
  }
}
