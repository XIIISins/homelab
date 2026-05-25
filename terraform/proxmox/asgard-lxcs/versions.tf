terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket       = "xiiisins-homelab-tfstate"
    key          = "proxmox/asgard-lxcs/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.106.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.9.0"
    }
  }
}
