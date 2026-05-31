# terraform/vault/versions.tf
terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket       = "xiiisins-homelab-tfstate"
    key          = "vault/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    vault = {
      source  = "hashicorp/vault"
      version = "4.8.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.9.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "4.1.0"
    }
  }
}
