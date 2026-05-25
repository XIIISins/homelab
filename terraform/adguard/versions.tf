# terraform/adguard/versions.tf
terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket       = "xiiisins-homelab-tfstate"
    key          = "adguard/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    adguard = {
      source  = "gmichels/adguard"
      version = "1.7.0"
    }
  }
}
