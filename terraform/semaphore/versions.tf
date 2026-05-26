# terraform/semaphore/versions.tf
terraform {
  required_version = ">= 1.10.0"

  backend "s3" {
    bucket       = "xiiisins-homelab-tfstate"
    key          = "semaphore/terraform.tfstate"
    region       = "eu-west-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    # Community provider, but the most mature TF option for Semaphore today.
    # Resource coverage as of v0.2.2 (Dec 2025): project, project_repository,
    # project_key, project_inventory, project_environment, project_template,
    # project_schedule, project_user, project_view, user. No webhook /
    # alert resource — Hermod notification is wired Ansible-side via the
    # hermod_summary callback plugin (ansible/callback_plugins/).
    #
    # Token-only auth (no username/password). API tokens inherit the
    # creating user's perms — minted by the operator's OIDC-provisioned
    # admin account, stored at secret/k8s/semaphore/admin-api-token.
    # Local name MUST be `semaphoreui` — the provider declares its
    # resource types as `semaphoreui_*` (not `semaphore_*`). Reusing
    # the source-last-segment name as the local alias would cause
    # every resource declaration to fail with "Invalid resource type."
    semaphoreui = {
      source  = "semaphoreui/semaphore"
      version = "0.2.2"
    }
    vault = {
      source  = "hashicorp/vault"
      version = "4.8.0"
    }
  }
}
