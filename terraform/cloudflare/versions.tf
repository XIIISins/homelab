terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket       = "xiiisins-homelab-tfstate"
    key          = "cloudflare/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.19.0"
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
