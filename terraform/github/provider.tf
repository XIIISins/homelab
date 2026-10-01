# terraform/github/provider.tf
#
# GITHUB_TOKEN comes from env (never in tfvars/state). Fine-grained PAT scoped to
# ONLY the XIIISins/homelab repository with "Administration: Read and write"
# (rulesets + repo settings) and "Metadata: Read". 1Password item:
# "Terraform - GitHub - token" (operator mirrors; non-homelab-vault-consumable
# secrets stay human-only — this token is minted by hand in the GitHub UI).
# Load for one command, never echo:
#   GITHUB_TOKEN="$(op read 'op://Homelab 2.0/Terraform - GitHub - token/credential')" terraform plan
provider "github" {
  owner = var.owner
}
