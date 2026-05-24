# terraform/adguard/versions.tf
terraform {
  required_version = ">= 1.7.0"

  required_providers {
    adguard = {
      source  = "gmichels/adguard"
      version = "1.7.0"
    }
  }
}
