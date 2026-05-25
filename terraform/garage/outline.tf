# terraform/garage/outline.tf
#
# Garage bucket + access key + per-bucket grant for Outline's file
# storage. Outline uses S3 for user-uploaded attachments, document
# images, avatars (FILE_STORAGE=s3 + AWS_S3_* env vars). One bucket
# per consumer (no sharing — keeps access scoping per-app).
#
# secret_access_key is returned by the Garage admin API ONCE on key
# create. The provider stores it in state at create time; Vault gets
# the canonical operator-readable copy via the vault_kv write below.
# If state is ever lost, the key needs to be re-minted (`terraform apply
# -replace=garage_key.outline`) — Outline's ESO will then pick up the
# rotated value on the next refresh cycle (1h).

resource "garage_bucket" "outline" {
  # Global alias = bucket name visible to S3 clients
  # (AWS_S3_UPLOAD_BUCKET_NAME=outline in the Outline ExternalSecret).
  global_aliases = ["outline"]
}

resource "garage_key" "outline" {
  name = "outline"
}

resource "garage_bucket_permission" "outline" {
  bucket_id     = garage_bucket.outline.id
  access_key_id = garage_key.outline.access_key_id
  read          = true
  write         = true
}

# Module owns the Vault KV entry for the secret it generates (CLAUDE.md
# "Module ownership of Vault KV secrets"). ESO reads this via the
# Outline namespace's outline-s3 ExternalSecret.
resource "vault_kv_secret_v2" "outline_s3" {
  mount = "secret"
  name  = "k8s/outline/s3"
  data_json = jsonencode({
    access_key_id     = garage_key.outline.access_key_id
    secret_access_key = garage_key.outline.secret_access_key
    bucket            = garage_bucket.outline.id
    endpoint          = local.s3_endpoint_in_cluster
    region            = local.s3_region
  })
}
