# terraform/digitalocean-burst/versions.tf
#
# Phase 10b2 — burst substrate. OWN state key, deliberately NOT in the `digitalocean` (do1)
# root: do1 is permanent infrastructure (reserved IP, public-facing firewall); this root is
# throwaway and is created/destroyed per test run. Separate state = `terraform destroy` here
# can never touch do1, and a `terraform apply` of do1 can never touch a running burst cluster.
terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket       = "xiiisins-homelab-tfstate"
    key          = "digitalocean-burst/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "2.103.0"
    }
  }
}
