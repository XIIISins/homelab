# terraform/aws/burst-runner.tf
#
# Phase 10h: the burst runner's own, NARROW Terraform-state identity (docs/procedures/k8s-burst-test.md "Deploy the runner").
# Same shape as rebuild-runner.tf.
#
# The runner builds and destroys exactly one module unattended (terraform/digitalocean-burst), so it gets a user that can touch
# exactly that module's state object and its lock file, and nothing else:
#
#   s3://<tfstate bucket>/digitalocean-burst/terraform.tfstate         get / put / delete (use_lockfile backend)
#   s3://<tfstate bucket>/digitalocean-burst/terraform.tfstate.tflock  get / put / delete
#
# The key is placed by the operator into Vault `secret/ansible/aiops/burst/env` (aws_access_key_id, aws_secret_access_key,
# aws_default_region), from the sensitive outputs below, with `scripts/secrets/seed-burst-state-key` (values never printed).
# Apply with the Bootstrap AWS identity from the main checkout, like the rest of this module.

locals {
  burst_runner_state_key = "digitalocean-burst/terraform.tfstate"
}

resource "aws_iam_user" "burst_runner_state" {
  name = "burst-runner-state"
  path = "/homelab/"
}

resource "aws_iam_access_key" "burst_runner_state" {
  user = aws_iam_user.burst_runner_state.name
}

data "aws_iam_policy_document" "burst_runner_state" {
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
      values   = ["digitalocean-burst/*"]
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
      "${aws_s3_bucket.tfstate.arn}/${local.burst_runner_state_key}",
      "${aws_s3_bucket.tfstate.arn}/${local.burst_runner_state_key}.tflock",
    ]
  }
}

resource "aws_iam_user_policy" "burst_runner_state" {
  name   = "burst-runner-state-own-module"
  user   = aws_iam_user.burst_runner_state.name
  policy = data.aws_iam_policy_document.burst_runner_state.json
}

output "burst_runner_state_access_key_id" {
  description = "Burst runner state IAM user access key ID -> Vault secret/ansible/aiops/burst/env (aws_access_key_id)."
  value       = aws_iam_access_key.burst_runner_state.id
}

output "burst_runner_state_secret_access_key" {
  description = "Burst runner state IAM user secret -> Vault secret/ansible/aiops/burst/env (aws_secret_access_key). Never print it."
  value       = aws_iam_access_key.burst_runner_state.secret
  sensitive   = true
}
