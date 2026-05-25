# terraform/authentik/versions.tf
terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket       = "xiiisins-homelab-tfstate"
    key          = "authentik/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    authentik = {
      source  = "goauthentik/authentik"
      version = "2026.2.0"
    }
    vault = {
      source  = "hashicorp/vault"
      version = "4.8.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.6.3"
    }
  }
}
