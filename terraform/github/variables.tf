# terraform/github/variables.tf
variable "owner" {
  description = "GitHub user/org that owns the repository."
  type        = string
  default     = "XIIISins"
}

variable "repository" {
  description = "Repository name (without owner)."
  type        = string
  default     = "homelab"
}

variable "required_check" {
  description = "Name of the single required status check (the always-running aggregator job in .github/workflows/ci.yml)."
  type        = string
  default     = "CI gate"
}
