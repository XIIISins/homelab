<!-- docs/procedures/offsite-backups.md -->

# Off-homelab recovery backups (etcd · Vault Raft · Calico datastore → S3)

Closes the 2026-10-01 gap from the [Calico datastore-prune incident](../incidents/2026-10-01-calico-datastore-prune.md):
cluster-recovery state existed only inside the homelab (the Calico export on gondul was the same failure
domain as the thing it protected; K3s etcd snapshots were node-local).

## What lands where

One bucket `xiiisins-homelab-backups` (eu-west-1, `terraform/aws/backups.tf`), SSE-S3, versioned, private, TLS-only.

| Prefix | Content | Producer | Cadence | History |
|---|---|---|---|---|
| `etcd/` | K3s etcd snapshot (**contains K8s Secrets in plaintext**) | K3s `etcd-s3-*` on **all 3 CPs** (`roles/k3s/tasks/etcd-s3.yml`) | every 12 h per CP | K3s keeps 5/node current; pruned ones live on as noncurrent versions 14 d |
| `vault-raft/` | `vault operator raft snapshot save` (barrier-encrypted) | CronJob `vault-raft-snapshot` (ns `backups`) | daily 02:45 UTC | 30 d |
| `calico/` | `crd.projectcalico.org` + `operator.tigera.io` objects (no secrets) | CronJob `calico-datastore-export` (ns `backups`) | daily 02:30 UTC | 30 d |

Credentials (two IAM users, never in git or TF state files outside `terraform/aws`'s own local state):

| IAM user | Rights | Vault path | Consumer |
|---|---|---|---|
| `k3s-etcd-backup` | List bucket; Get/Put/Delete `etcd/*` (K3s prunes + restores) | `secret/ansible/backups/etcd-s3` | Ansible k3s role → `/etc/rancher/k3s/config.yaml.d/10-etcd-s3.yaml` (0600) |
| `homelab-backup-writer` | **PutObject only** on `vault-raft/*`, `calico/*` | `secret/k8s/backups/aws-writer` | ESO → Secret `aws-backup-writer` |

Fields in both Vault secrets: `access_key_id`, `secret_access_key`. The Vault Raft job authenticates to Vault with its
ServiceAccount via the Kubernetes auth role `vault-snapshot` (`terraform/vault/backups.tf`; policy = read on
`sys/storage/raft/snapshot` only) — no SecretID to expire.

**Cost** (eu-west-1 Standard $0.023/GB-mo): ~3 GB steady state ≈ **$0.07/month**; PUTs < $0.01; no KMS key.
A restore pulling everything is ~$0.30 egress, once.

## Deploy order (each step gates the next)

1. **Bucket + IAM** — `terraform/aws`, from the **main checkout**, with the **Bootstrap** AWS identity (not the env-cached
   `terraform-state` user — see [terraform-state.md](../known-issues/terraform-state.md)): `set-aws-creds bootstrap`, `terraform plan`, `terraform apply`.
2. **Mint + place credentials** (values never echoed — shell vars only; `vault` token needs write on `secret/*`, i.e. the root token or Frigg):
   ```fish
   cd terraform/aws
   vault kv put secret/ansible/backups/etcd-s3 \
     access_key_id="$(terraform output -raw k3s_etcd_backup_access_key_id)" \
     secret_access_key="$(terraform output -raw k3s_etcd_backup_secret_access_key)"
   vault kv put secret/k8s/backups/aws-writer \
     access_key_id="$(terraform output -raw backup_writer_access_key_id)" \
     secret_access_key="$(terraform output -raw backup_writer_secret_access_key)"
   ```
   Then mirror both into 1Password per the offline-mirror rule (`AWS - Terraform - Backups etcd`, `AWS - Terraform - Backups writer`;
   username = access key, credential = secret).
3. **Vault policy + role** — `terraform/vault` (root token): `terraform apply` creates `vault-snapshot`.
4. **etcd → S3** — `ansible-playbook playbooks/asgard-k3s.yml --tags k3s_etcd_s3 --limit k3s_cp` (the play is `serial: 1`; each CP restarts K3s and
   waits for Ready before the next). One playbook at a time across agents.
5. **CronJobs** — merge + push `main` (Flux applies `k8s/asgard/apps/backups`). Until step 2 landed the ExternalSecret is `SecretSyncedError` — expected.

## Verify (do all three — a backup never restored or listed is a hope)

```bash
# etcd: force one snapshot per CP and confirm the objects appear (list needs the k3s-etcd-backup key)
ssh ansible@10.0.21.11 'sudo k3s etcd-snapshot save --name s3-verify && sudo k3s etcd-snapshot list --s3 | tail -5'
# CronJobs: trigger now, read the log, then list the prefix
kubectl -n backups create job --from=cronjob/calico-datastore-export calico-now
kubectl -n backups create job --from=cronjob/vault-raft-snapshot vault-now
kubectl -n backups logs job/calico-now ; kubectl -n backups logs job/vault-now -c upload
kubectl -n backups delete job calico-now vault-now
```
Expect `uploaded s3://xiiisins-homelab-backups/...` lines and, for Calico, `ippools=<n>` ≥ 1.

## Restore (outline — drill pending, see open-questions.md)

- **etcd** — on one CP (others stopped): `k3s server --cluster-reset --cluster-reset-restore-path=<snapshot-name> --etcd-s3 --etcd-s3-bucket=… --etcd-s3-folder=etcd --etcd-s3-region=eu-west-1 --etcd-s3-access-key=… --etcd-s3-secret-key=…`
  (S3 restore is the same flag set as the config drop-in), then re-join the other CPs per [k3s-lifecycle.md](../known-issues/k3s-lifecycle.md) (`kubectl delete node` first).
- **Vault** — `vault operator raft snapshot restore -force <file>` against the active node (unsealed; restores KV, policies, auth methods, mounts). Needs a token with `sys/storage/raft/snapshot` update — i.e. root / break-glass.
- **Calico** — order matters: operator CRDs + tigera-operator addon → Installation (`calico-installation.yaml`) → wait for the datastore CRDs → `kubectl apply -f calico-datastore-<ts>.yaml` (IPPools first if the apply is split; strip `resourceVersion`/`uid`/`status` noise if the API rejects it). Then restart pods (their IPs must be in IPAM) — see the incident's recovery steps.

## Known gaps

- **No alerting on a missed/failed backup.** A failed CronJob shows in `kubectl get jobs -n backups` only; the infra-health prober doesn't check bucket freshness yet (follow-up: newest-object age per prefix → Hermod).
- **No restore drill yet** (scratch cluster) — the Calico/etcd restore steps above are the documented intent, not a proven runbook.
- The pre-upgrade Calico export written by `playbooks/calico-upgrade.yml` is still local to gondul (the daily CronJob export is the off-site copy).
