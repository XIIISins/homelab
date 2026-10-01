# terraform/github/main.tf
#
# Branch protection for `main` as code. Pushing `main` is the Flux deploy, so
# the ruleset is the control that makes "merged" mean "CI passed".
# Decision + rollout: docs/procedures/ci.md, decisions.md "CI gate + branch ruleset".
#
# APPLY ORDER MATTERS: merge the CI workflow (.github/workflows/ci.yml) to main
# and let it run green at least once BEFORE `terraform apply` here. Requiring a
# check that has never reported blocks every PR (including the one that would
# fix it) — admin bypass below is the escape hatch, not the plan.

# --- Repository settings we depend on ---------------------------------------
# Imported, not created. Only the two settings the CI/automerge flow needs are
# declared; everything else on the repo is left as GitHub has it. REVIEW THE
# PLAN of the first apply: it must show only allow_auto_merge / delete_branch_on_merge
# (and the ruleset) changing.
import {
  to = github_repository.homelab
  id = var.repository
}

resource "github_repository" "homelab" {
  name = var.repository

  allow_auto_merge       = true # lets a PR be set to merge itself once the required check is green
  delete_branch_on_merge = true # feat/ doc/ branches are single-use

  allow_squash_merge = true
  allow_rebase_merge = true
  allow_merge_commit = false # history is linear (ff-style) on main

  lifecycle {
    prevent_destroy = true
  }
}

# --- main ruleset ------------------------------------------------------------
resource "github_repository_ruleset" "main" {
  name        = "main"
  repository  = github_repository.homelab.name
  target      = "branch"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["~DEFAULT_BRANCH"]
      exclude = []
    }
  }

  # Repository admin (the owner) can always bypass — the 2am "CI is broken and
  # the cluster needs a fix" path. Everyone else, and every agent session, goes
  # through a PR. actor_id 5 = the built-in "Admin" repository role.
  bypass_actors {
    actor_id    = 5
    actor_type  = "RepositoryRole"
    bypass_mode = "always"
  }

  rules {
    deletion                = true # main cannot be deleted
    non_fast_forward        = true # no force-push
    required_linear_history = true # no merge commits

    # Sole human reviewer => no required approvals; the gate is CI. Unresolved
    # review threads still block (agent-authored PRs get real review comments).
    pull_request {
      required_approving_review_count   = 0
      dismiss_stale_reviews_on_push     = false
      require_code_owner_review         = false
      require_last_push_approval        = false
      required_review_thread_resolution = true
    }

    required_status_checks {
      # false: do not force "update branch" before every merge — with parallel
      # agent PRs that would serialize everything and defeat auto-merge.
      strict_required_status_checks_policy = false

      required_check {
        context        = var.required_check
        integration_id = 15368 # GitHub Actions app
      }
    }
  }
}
