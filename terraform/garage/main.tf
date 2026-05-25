# terraform/garage/main.tf
#
# Per-consumer Garage resources live in dedicated files (outline.tf,
# future immich.tf, etc.). Cross-cutting locals + smoke tests live here.
#
# S3 endpoint convention: in-cluster consumers (Outline, future Immich,
# anything else in K3s) hit the ClusterIP Service directly. Off-cluster
# consumers (future Velero on LXC, host-level Restic) would need a
# different endpoint — currently none exist; add an HTTPRoute on the
# niflheim Gateway when one does.
locals {
  s3_endpoint_in_cluster = "http://garage-s3.garage.svc.cluster.local:3900"
  s3_region              = "garage" # matches garage.toml's s3_region
}
