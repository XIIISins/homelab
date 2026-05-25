# terraform/aws/main.tf
#
# Bootstrap layer — creates the S3 bucket + IAM user that all OTHER terraform
# modules in this repo use as remote backend + locking authority. This module
# itself keeps LOCAL state (chicken-and-egg: can't store its own state in a
# bucket it hasn't created yet). Same pattern as terraform/vault/.
#
# Terraform 1.10+ native S3 state locking is used downstream:
#   backend "s3" {
#     bucket       = "xiiisins-homelab-tfstate"
#     key          = "<module>/terraform.tfstate"
#     region       = "eu-central-1"
#     encrypt      = true
#     use_lockfile = true   # writes <key>.tflock via PutObject + If-None-Match
#   }
# No DynamoDB needed — S3's conditional writes handle the lock.

# -----------------------------------------------------------------------------
# State bucket
# -----------------------------------------------------------------------------
resource "aws_s3_bucket" "tfstate" {
  bucket = var.state_bucket_name

  # Default false: protects against `terraform destroy` wiping every state file
  # in the bucket. Flip to true + apply + destroy if you ever genuinely want it
  # gone — separate two-step is the intended safety.
  force_destroy = false
}

# Versioning ON: every state write keeps prior version retrievable for
# `noncurrent_version_retention_days`. Recovery from corrupted state /
# accidental `terraform state rm` is a `aws s3api list-object-versions` away.
resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3 (AES256, free, AWS-managed key). SSE-KMS would couple this bucket to
# the Vault unseal key for no benefit — terraform state at homelab scale doesn't
# need KMS audit trails, and the extra IAM grant just widens the blast radius.
resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# BucketOwnerEnforced disables ACLs cluster-wide — only IAM policies grant
# access. Modern AWS default; ACLs are legacy.
resource "aws_s3_bucket_ownership_controls" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Expire prior state versions after retention window. Current version is
# untouched; lifecycle only targets `noncurrent_version`. Also clean up failed
# multipart uploads after 7 days (defensive against aborted writes leaving
# orphaned parts billed forever).
resource "aws_s3_bucket_lifecycle_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# -----------------------------------------------------------------------------
# IAM user for terraform state access (consumed by all downstream modules)
# -----------------------------------------------------------------------------
# One shared user across all modules — homelab single-operator simplicity. To
# split per-module later: mint one IAM user per module with a Resource
# condition restricting it to `<bucket>/<module>/*` (state) + `<bucket>/<module>/*.tflock`.
# Today's "any module can read any state" is acceptable; modules already share
# Vault root token at apply time.
resource "aws_iam_user" "terraform_state" {
  name = "terraform-state"
  path = "/homelab/"
}

resource "aws_iam_access_key" "terraform_state" {
  user = aws_iam_user.terraform_state.name
}

# Minimum-viable policy for TF 1.10+ S3 backend with use_lockfile:
#   - ListBucket on the bucket (backend version listing + existence check)
#   - GetObject/PutObject/DeleteObject on objects (state + .tflock)
# No DynamoDB action — no DynamoDB table.
data "aws_iam_policy_document" "terraform_state" {
  statement {
    sid    = "ListStateBucket"
    effect = "Allow"

    actions = [
      "s3:ListBucket",
      "s3:GetBucketVersioning",
    ]

    resources = [aws_s3_bucket.tfstate.arn]
  }

  statement {
    sid    = "ReadWriteStateObjects"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:DeleteObject",
    ]

    resources = ["${aws_s3_bucket.tfstate.arn}/*"]
  }
}

resource "aws_iam_user_policy" "terraform_state" {
  name   = "terraform-state-rw"
  user   = aws_iam_user.terraform_state.name
  policy = data.aws_iam_policy_document.terraform_state.json
}
