# terraform/github/outputs.tf
output "ruleset_id" {
  description = "ID of the main-branch ruleset."
  value       = github_repository_ruleset.main.ruleset_id
}
