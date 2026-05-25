# terraform/tailscale/versions.tf
terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket       = "xiiisins-homelab-tfstate"
    key          = "tailscale/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    tailscale = {
      source  = "tailscale/tailscale"
      version = "0.28.0"
    }
    vault = {
      source  = "hashicorp/vault"
      version = "4.8.0"
    }
  }
}
