<!-- docs/services/garage.md -->

# Garage (asgard K3s)

S3-compatible object store for in-cluster consumers (first user: Outline). Single-node, replication-factor 1, backed by Synology iSCSI. Lives in `k8s/asgard/infrastructure/garage/` (Flux Kustomization `infrastructure`).

Picked Garage over MinIO + SeaweedFS in 2026-05-25 after the MinIO OSS pivot made it no-longer-trustable upstream and SeaweedFS's POSIX/WebDAV surfaces turned out redundant with the existing iSCSI + NFS paths. Garage is purpose-built as a small-scale S3 — Rust, distroless image, LMDB metadata, no JVM, no shell. Fits the homelab's "as little surface as possible" posture.

## Architecture

| Piece | Where | Notes |
|-------|-------|-------|
| Image | `dxflrs/garage:v2.3.0` | Distroless/scratch — no shell, no debug. `kubectl exec` is dead, use the admin API. |
| Workload | StatefulSet `garage/garage` | 1 replica, UID 1000, fsGroup 1000 |
| Metadata | PVC `meta-garage-0`, 10Gi, `synology-csi-iscsi-retain` | LMDB store (`db_engine="lmdb"`) |
| Object data | PVC `data-garage-0`, 200Gi, `synology-csi-iscsi-retain` | Per-object chunked layout |
| RPC service | `garage-headless` (port 3901, headless, `publishNotReadyAddresses: true`) | Cluster RPC; not-ready publishing is load-bearing — see "Bootstrap chicken-and-egg" below |
| S3 service | `garage-s3` ClusterIP, port 3900 (S3) + 3902 (web) | Consumers dial this |
| Admin service | `garage-admin` ClusterIP, port 3903 | `/health`, `/v2/*` — operator-only, no external exposure |
| Vault — server | `secret/k8s/garage/server` | `rpc_secret` (64-byte hex, `random_id`) + `admin_token` (40-char alphanum, `random_password`). Minted in `terraform/vault/main.tf` |
| Vault — consumer | `secret/k8s/<consumer>/s3` | Per-consumer bucket creds. Minted in `terraform/garage/<consumer>.tf` |

### Traffic flow (in-cluster S3)

```
Consumer pod (e.g. outline)
  → DNS garage-s3.garage.svc.cluster.local → ClusterIP 10.43.x.x:3900
  → kube-proxy → garage-0 pod (10.42.x.x:3900)
  → garage S3 API → LMDB meta + chunked data on the two iSCSI LUNs
Reply path is the inverse — all in pod network, never touches MetalLB.
```

The admin API (3903) is ClusterIP-only by design. Operator workflows hit it via `kubectl port-forward` — never exposed externally, never tunnelled.

## Single-node design tradeoffs

Garage is built for 3+ nodes with replication. Running one node with `replication_factor=1` + `consistency_mode="dangerous"` is an explicit homelab choice:

- The data already sits on the Synology via iSCSI. Replicating *inside* Garage on top of a single NAS adds zero safety — both copies live on the same RAID1 — but does block startup until quorum is met. The "second layer of replication" is a fiction here.
- `consistency_mode="dangerous"` skips quorum reads. With one node there's no quorum to satisfy; the alternative modes hang on writes waiting for peers that will never exist.
- LUN-level snapshots (PBS via Synology iSCSI snapshots) are the actual durability story. Garage is the *protocol* layer, not the *durability* layer.

If a second NAS lands later (jotunheim-storage, B2 offsite mirror, anything), scale-out is `terraform apply` to add the node + a layout edit assigning capacity in a second zone. The data path doesn't change.

## Why raw manifests, not the Helm chart

The official Helm chart is v1.x-only and git-only (no OCI registry). v2.x — which is what we want, mainline Rust + LMDB defaults + admin API v2 — is unsupported by the chart. The full deploy is ~120 lines of YAML across six files; the chart would be a thin wrapper around the same shape. Revisit when the chart catches up to v2 + ships from OCI.

## Bootstrap chicken-and-egg

garage-0's readiness probe (`/health` on the admin port) returns 503 until a cluster layout is assigned. The layout-init Job needs to reach `garage-admin` to assign that layout. By default, headless Service DNS only resolves pods that are Ready — which garage-0 isn't, until layout-init runs.

Resolution: `publishNotReadyAddresses: true` on `garage-headless`. The Service publishes the pod's DNS as soon as it has an IP, regardless of readiness. The Job hits the admin API immediately, posts the layout, and garage-0 transitions to Ready ~1s later. Without this flag the deploy deadlocks indefinitely.

Same class as the K3s "ClusterIP companion for host-network consumers" pattern — a deliberate readiness-bypass for a known-good reason, scoped to one Service.

## Why a layout-init Job, not a postStart hook

- A postStart that blocks would mask actual Garage startup failures behind init failures.
- A postStart that doesn't block races the readiness probe.
- The Job has its own retry semantics + visible Completed/Failed state, separable from the pod.
- Flux preserves the Job indefinitely; re-runs for layout edits (scale-out) are `kubectl delete job/layout-init && flux reconcile kustomization infrastructure` away.

The Job is `alpine:3.20 + apk add curl jq`, idempotent (skips if cluster layout version >= 1), `backoffLimit: 30` with a 5-minute wait loop per attempt — total ~2.5h budget. The budget exists because the first-deploy `mkfs.ext4` on the 200Gi data LUN takes ~20 minutes (Synology iSCSI is TRIM-bound at ~10GB/min). Subsequent runs return in seconds.

## Bootstrap order

1. **Mint server secrets in Vault.** `(cd terraform/vault && terraform apply)` — creates `rpc_secret` + `admin_token` at `secret/k8s/garage/server`.
2. **Deploy via Flux.** `k8s/asgard/infrastructure/garage/` lands via the `infrastructure` Kustomization. ExternalSecret resolves; StatefulSet pod schedules.
3. **mkfs (~20 min).** First boot only. The 200Gi data PVC's iSCSI LUN goes through TRIM + mkfs.ext4 on the worker. Watch via `kubectl describe pod -n garage garage-0` — `Initialized` event lands when both PVCs are mounted.
4. **layout-init runs.** Job hits the admin API, assigns garage-0 to zone `dc1` with 200GiB capacity, applies layout version 1. Job goes `Completed`.
5. **garage-0 Ready.** `/health` flips to 200. Service endpoints publish normally.
6. **Provision buckets.** `kubectl port-forward -n garage svc/garage-admin 3903:3903 &`, then `(cd terraform/garage && terraform apply)`. Per-consumer files (`terraform/garage/outline.tf`, etc.) mint bucket + key + permission + write to the consumer's Vault path.

## Operator workflow — onboarding a new consumer

One file per consumer in `terraform/garage/<consumer>.tf`, with four resources:

```hcl
resource "garage_bucket" "X" { ... }
resource "garage_key" "X" { name = "X" }
resource "garage_bucket_permission" "X" {
  bucket_id   = garage_bucket.X.id
  access_key_id = garage_key.X.access_key_id
  read = true
  write = true
}
resource "vault_kv_secret_v2" "X_s3" {
  mount = "secret"
  name  = "k8s/X/s3"
  data_json = jsonencode({
    access_key_id     = garage_key.X.access_key_id
    secret_access_key = garage_key.X.secret_access_key
    bucket            = garage_bucket.X.id
    endpoint          = "http://garage-s3.garage.svc.cluster.local:3900"
    region            = "garage"
  })
}
```

Then port-forward the admin API + `terraform apply`. Consumer's ExternalSecret reads `secret/k8s/X/s3`. The admin API isn't externally reachable, so apply runs only from a machine with kubectl access — which is the same machine running TF anyway.

## Operations

**Health check.** `curl localhost:3903/v2/GetClusterHealth -H "Authorization: Bearer $TOKEN"` after port-forward — returns JSON with `status: "healthy"` once layout is applied + node is connected.

**Bucket inventory.** `curl localhost:3903/v2/ListBuckets` for the list; `/v2/GetBucketInfo?id=<id>` for object count + bytes used. No per-bucket-usage Prometheus exporter today — vmagent scrape of `/metrics` on the admin port emits cluster-wide gauges only.

**Key rotation.** `terraform apply -replace=garage_key.X` rotates an access key. Vault entry rewrites atomically; consumer's ExternalSecret refreshes on its poll cycle (default 1h). Force-refresh via `kubectl annotate externalsecret X force-sync=$(date +%s) --overwrite`.

**Layout scaling.** When a second node lands: edit `layout-init-job.yaml` to include the new node's hostname + zone, `kubectl delete job/layout-init`, `flux reconcile kustomization infrastructure`. Job re-runs against the now-2-node cluster, posts layout version 2. Garage rebalances data in the background.

**No shell, no exec.** Distroless image. Anything you'd normally `kubectl exec` for goes through the admin API. Debug pods (`kubectl debug node/X --image=alpine:3.20`) for filesystem-level work on the LUNs.

## Why not (MinIO | SeaweedFS | democratic-csi)

- **MinIO** — post-OSS pivot (2025), upstream removed the web console + made breaking licensing changes. No longer a safe long-term bet. Migration cost from MinIO would be higher in two years than the Garage learning cost today.
- **SeaweedFS** — its POSIX + WebDAV surfaces are the differentiator, but iSCSI (block) + NFS (file) already cover those access patterns from Synology. Paying complexity for a third path adds nothing.
- **democratic-csi** — orthogonal. It's a CSI driver, not an object store. Object storage is an explicit need (Outline, future Immich, future restic backup targets) that block-or-file CSI can't satisfy.

See `docs/operations/decisions.md` row "Object storage — Garage" for the dated rationale.

## Known gotchas referenced

- CLAUDE.md "Synology CSI iSCSI volumes need a chown initContainer for non-root pods" — applied for both PVCs. Init container runs as UID 0 with default capabilities (no `drop: [ALL]` — would strip `CAP_CHOWN`/`CAP_FOWNER` per the chown-init sub-gotcha).
- CLAUDE.md "K3s host network can't reach MetalLB-announced VIPs from the same cluster" — N/A here. Garage is ClusterIP-only; nothing on a K3s host network needs to dial it directly.
- New gotcha class: **distroless images have no shell**. Standard debug habits (`kubectl exec ... -- sh`) fail with `executable file not found`. All operational access is via admin API + debug pods. Document the admin-API command set per workflow rather than relying on muscle memory.
- New gotcha class: **headless Service `publishNotReadyAddresses: true` for bootstrap-deadlock breakers**. Same shape may apply to any future workload whose readiness depends on another in-cluster API call against itself.
- CLAUDE.md (pending entry): **Synology DS223J 10-LUN ceiling**. This deploy added LUNs #9 + #10 (meta + data). The next PVC to land (outline-redis, would have been #11) failed with `Failed to create LUN, err: Number of LUN reach limit`. Tactical fix: outline-redis switched to emptyDir (cache-only, ephemeral state is fine). Open question: J-series LUN cap — is it per-volume, per-DSM, raisable via UI, or hardware-limited? See `docs/operations/open-questions.md`.
- **First-deploy mkfs.ext4 on Synology iSCSI is TRIM-bound** at ~10GB/min. 200Gi LUN = ~20 min. The layout-init Job's `backoffLimit: 30` + 5min/attempt budget exists for this.
