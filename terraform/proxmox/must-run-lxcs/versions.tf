terraform {
  required_version = ">= 1.0"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.77"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }
}
