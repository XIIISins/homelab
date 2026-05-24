# terraform/netbox/versions.tf
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    netbox = {
      source  = "e-breuninger/netbox"
      version = "5.3.0"
    }
    vault = {
      source  = "hashicorp/vault"
      version = "5.9.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.9.0"
    }
  }
}
