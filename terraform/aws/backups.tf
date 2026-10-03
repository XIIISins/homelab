# terraform/aws/backups.tf
#
# Off-homelab copies of the cluster's recovery state (2026-10-01 Calico
# datastore-prune incident follow-up; open-questions.md "CRITICAL — export the
# Calico datastore ... OUT-OF-HOMELAB"). One bucket, three prefixes:
#
#   etcd/        K3s native `etcd-s3-*` snapshots, all 3 CPs, every 12 h
#                (written/pruned by K3s itself → that IAM user needs Delete)
#   vault-raft/  daily `vault operator raft snapshot save` (barrier-encrypted
#                by Vault already) — K8s CronJob in ns `backups`
#   calico/      daily export of the crd.projectcalico.org datastore —
#                K8s CronJob in ns `backups`
#
# Encryption: SSE-S3 (AES256), bucket default — free, same as the tfstate
# bucket. No KMS key (a new CMK is $1/mo, ~10x the storage bill).
# etcd snapshots hold K8s Secrets in plaintext inside the snapshot, so the
# protection here is: private bucket + TLS-only + scoped keys, not KMS.
#
# Retention is S3-side, not K3s-side: K3s keeps only its default 5 snapshots
# per node (small local disks) and DELETES older ones from S3 too — with
# versioning ON those deletes become noncurrent versions that live for
# `backup_noncurrent_retention_days`, giving ~2 weeks of history for pennies
# AND making a stolen/buggy delete recoverable.
#
# Cost model (eu-west-1 Standard, $0.023/GB-mo): ~3 GB steady-state ≈ $0.07/mo.

variable "backup_bucket_name" {
  description = "Globally-unique S3 bucket name for off-homelab cluster-recovery backups (etcd snapshots, Vault Raft snapshots, Calico datastore exports)."
  type        = string
  default     = "xiiisins-homelab-backups"
}

variable "backup_noncurrent_retention_days" {
  description = "How long superseded/deleted object versions are kept. For etcd/ this is the real history depth (K3s prunes the current set to 5 per node)."
  type        = number
  default     = 14
}

variable "backup_current_retention_days" {
  description = "Expiry for current objects under vault-raft/ and calico/ (the CronJobs add one object per day and never delete)."
  type        = number
  default     = 30
}

resource "aws_s3_bucket" "backups" {
  bucket        = var.backup_bucket_name
  force_destroy = false # same two-step safety as the tfstate bucket
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket = aws_s3_bucket.backups.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Refuse any non-TLS request (defence in depth; every client here uses HTTPS).
data "aws_iam_policy_document" "backups_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.backups.arn,
      "${aws_s3_bucket.backups.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "backups" {
  bucket = aws_s3_bucket.backups.id
  policy = data.aws_iam_policy_document.backups_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.backups]
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  # etcd/: K3s owns the current set (retention 5/node); we only age out the
  # versions it deleted.
  rule {
    id     = "etcd-noncurrent"
    status = "Enabled"

    filter {
      prefix = "etcd/"
    }

    noncurrent_version_expiration {
      noncurrent_days = var.backup_noncurrent_retention_days
    }
  }

  # vault-raft/ + calico/: write-only CronJobs, so S3 does the aging.
  dynamic "rule" {
    for_each = toset(["vault-raft", "calico"])

    content {
      id     = "${rule.value}-expire"
      status = "Enabled"

      filter {
        prefix = "${rule.value}/"
      }

      expiration {
        days = var.backup_current_retention_days
      }

      noncurrent_version_expiration {
        noncurrent_days = var.backup_noncurrent_retention_days
      }
    }
  }

  rule {
    id     = "cleanup-delete-markers-and-multipart"
    status = "Enabled"

    filter {}

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# -----------------------------------------------------------------------------
# IAM — two users, least privilege
# -----------------------------------------------------------------------------

# K3s etcd-s3: HeadBucket (needs ListBucket on the bucket, no prefix condition
# — a prefix-scoped ListBucket fails the client's bucket-exists probe),
# Get/Put/Delete on etcd/* (K3s lists, uploads, prunes, restores).
resource "aws_iam_user" "k3s_etcd_backup" {
  name = "k3s-etcd-backup"
  path = "/homelab/"
}

resource "aws_iam_access_key" "k3s_etcd_backup" {
  user = aws_iam_user.k3s_etcd_backup.name
}

data "aws_iam_policy_document" "k3s_etcd_backup" {
  statement {
    sid    = "BucketProbeAndList"
    effect = "Allow"

    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]

    resources = [aws_s3_bucket.backups.arn]
  }

  statement {
    sid    = "EtcdSnapshotObjects"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]

    resources = ["${aws_s3_bucket.backups.arn}/etcd/*"]
  }
}

resource "aws_iam_user_policy" "k3s_etcd_backup" {
  name   = "k3s-etcd-backup-rw"
  user   = aws_iam_user.k3s_etcd_backup.name
  policy = data.aws_iam_policy_document.k3s_etcd_backup.json
}

# CronJob writer (Vault Raft + Calico): PutObject ONLY, two prefixes. No
# Get/List/Delete — a leaked key can add objects but not read or destroy
# earlier backups.
resource "aws_iam_user" "backup_writer" {
  name = "homelab-backup-writer"
  path = "/homelab/"
}

resource "aws_iam_access_key" "backup_writer" {
  user = aws_iam_user.backup_writer.name
}

data "aws_iam_policy_document" "backup_writer" {
  statement {
    sid    = "PutBackupObjects"
    effect = "Allow"
    # AbortMultipartUpload: lets the aws-cli clean up a failed >8 MB (multipart) upload.
    actions = ["s3:PutObject", "s3:AbortMultipartUpload"]
    resources = [
      "${aws_s3_bucket.backups.arn}/vault-raft/*",
      "${aws_s3_bucket.backups.arn}/calico/*",
    ]
  }
}

resource "aws_iam_user_policy" "backup_writer" {
  name   = "homelab-backup-writer-put"
  user   = aws_iam_user.backup_writer.name
  policy = data.aws_iam_policy_document.backup_writer.json
}

# -----------------------------------------------------------------------------
# Restore identity: READ-ONLY on the three recovery prefixes (restore drill / real DR)
# -----------------------------------------------------------------------------
# Needed because no existing identity can read vault-raft/ or calico/ (the etcd
# user is etcd/* only, the writer is PutObject-only), and because in a real
# disaster Vault is gone: whatever reads the backups must NOT depend on Vault.
#
# Deliberately has NO aws_iam_access_key here. A key created in Terraform lands
# in state and would have to be placed in Vault; this one is minted out of band
# by scripts/secrets/mint-restore-key (aws iam create-access-key straight into the
# 1Password item "[Bootstrap] - Manual - AWS - Backup restore access key") and
# lives in 1Password only. No write, no delete, no KMS here: the Vault leg of a
# drill uses the existing decrypt-only `vault-unseal` identity from 1Password.
resource "aws_iam_user" "backup_restore" {
  name = "homelab-backup-restore"
  path = "/homelab/"
}

data "aws_iam_policy_document" "backup_restore" {
  statement {
    sid    = "ListBucketAndVersions"
    effect = "Allow"

    actions = [
      "s3:ListBucket",
      "s3:ListBucketVersions",
      "s3:GetBucketLocation",
    ]

    resources = [aws_s3_bucket.backups.arn]
  }

  statement {
    sid    = "ReadRecoveryObjects"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]

    resources = [
      "${aws_s3_bucket.backups.arn}/etcd/*",
      "${aws_s3_bucket.backups.arn}/vault-raft/*",
      "${aws_s3_bucket.backups.arn}/calico/*",
    ]
  }

  # Belt and braces: a later edit that widens the Allow above still cannot make
  # this identity write or delete a backup (the negative test checks this).
  statement {
    sid    = "NeverWriteOrDelete"
    effect = "Deny"

    actions = [
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
      "s3:PutBucketPolicy",
      "s3:PutLifecycleConfiguration",
      "s3:PutBucketVersioning",
    ]

    resources = [
      aws_s3_bucket.backups.arn,
      "${aws_s3_bucket.backups.arn}/*",
    ]
  }
}

resource "aws_iam_user_policy" "backup_restore" {
  name   = "homelab-backup-restore-read"
  user   = aws_iam_user.backup_restore.name
  policy = data.aws_iam_policy_document.backup_restore.json
}
