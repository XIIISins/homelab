# terraform/aws/rebuild-runner.tf
#
# Phase 10g: the rebuild runner's own, NARROW Terraform-state identity (docs/procedures/aiops-rebuild.md).
#
# The shared `terraform-state` user (main.tf) can read and write EVERY module's state, which includes secrets. The runner
# applies exactly one module unattended (terraform/proxmox/asgard-lxcs, the canary class), so it gets a user that can touch
# exactly that module's state object and its lock file, and nothing else:
#
#   s3://<tfstate bucket>/proxmox/asgard-lxcs/terraform.tfstate         get / put / delete (use_lockfile backend)
#   s3://<tfstate bucket>/proxmox/asgard-lxcs/terraform.tfstate.tflock  get / put / delete
#
# A later class (a replica, do1) adds its own state key here in a reviewed PR; never a wildcard.
#
# The key is placed by the operator into Vault `secret/ansible/aiops/rebuild/env` (aws_access_key_id,
# aws_secret_access_key; docs/procedures/aiops-rebuild.md "Deploy"), from the sensitive outputs below. Apply with the
# Bootstrap AWS identity from the main checkout, like the rest of this module.

locals {
  rebuild_runner_state_key = "proxmox/asgard-lxcs/terraform.tfstate"
}

resource "aws_iam_user" "rebuild_runner_state" {
  name = "rebuild-runner-state"
  path = "/homelab/"
}

resource "aws_iam_access_key" "rebuild_runner_state" {
  user = aws_iam_user.rebuild_runner_state.name
}

data "aws_iam_policy_document" "rebuild_runner_state" {
  # The S3 backend checks the bucket and its versioning before it reads the state object.
  statement {
    sid       = "BackendBucketChecks"
    effect    = "Allow"
    actions   = ["s3:GetBucketVersioning"]
    resources = [aws_s3_bucket.tfstate.arn]
  }

  # Listing only under the module's own prefix.
  statement {
    sid       = "ListOwnPrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.tfstate.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["proxmox/asgard-lxcs/*"]
    }
  }

  statement {
    sid    = "ReadWriteOwnStateAndLock"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:DeleteObject",
    ]

    resources = [
      "${aws_s3_bucket.tfstate.arn}/${local.rebuild_runner_state_key}",
      "${aws_s3_bucket.tfstate.arn}/${local.rebuild_runner_state_key}.tflock",
    ]
  }
}

resource "aws_iam_user_policy" "rebuild_runner_state" {
  name   = "rebuild-runner-state-own-module"
  user   = aws_iam_user.rebuild_runner_state.name
  policy = data.aws_iam_policy_document.rebuild_runner_state.json
}

output "rebuild_runner_state_access_key_id" {
  description = "Rebuild runner state IAM user access key ID -> Vault secret/ansible/aiops/rebuild/env (aws_access_key_id)."
  value       = aws_iam_access_key.rebuild_runner_state.id
}

output "rebuild_runner_state_secret_access_key" {
  description = "Rebuild runner state IAM user secret -> Vault secret/ansible/aiops/rebuild/env (aws_secret_access_key). Never print it."
  value       = aws_iam_access_key.rebuild_runner_state.secret
  sensitive   = true
}
