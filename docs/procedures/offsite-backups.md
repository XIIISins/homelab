<!-- docs/procedures/offsite-backups.md -->

# Off-homelab recovery backups (etcd · Vault Raft · Calico datastore → S3)

Closes the 2026-10-01 gap from the [Calico datastore-prune incident](../incidents/2026-10-01-calico-datastore-prune.md):
cluster-recovery state existed only inside the homelab (the Calico export on gondul was the same failure
domain as the thing it protected; K3s etcd snapshots were node-local).

## Status (2026-10-01)

- **Live + verified:** bucket/IAM (`terraform/aws` applied, 13 resources), both keys placed in Vault, `terraform/vault` role + policy, **etcd → S3 on all 3 CPs** (drop-in rolled one CP at a time; a forced snapshot from each CP landed under `etcd/`, ~21 MB each; the writer key can put but not list/read, the etcd key can list).
- **CronJobs live + verified (pushed, Flux-applied, ExternalSecret synced):** one manual run of each succeeded — `calico/calico-datastore-<ts>.yaml` (99 KiB, `ippools=1`) and `vault-raft/vault-raft-<ts>.snap` (135 KiB, uploaded via the Kubernetes-auth `vault-snapshot` role) are in the bucket. First scheduled runs: 02:30 / 02:45 UTC. Test objects in the bucket: `calico/probe.txt` (expires after 30 d via lifecycle) and three manual `etcd/s3-verify-*` snapshots (~63 MB total, one per CP — K3s retention only prunes its own scheduled names and `etcd/` has no current-version expiry, so delete them with `k3s etcd-snapshot delete s3-verify-<node>-<ts>` if you want them gone; ≈ $0.0015/mo otherwise).

- **Restore drill run 2026-10-03: all three S3 legs PASS** (read-only restore key; a one-node burst cluster with prod's CIDRs and token, destroyed afterwards; procedure and the corrections it needed in [`burst-substrate.md`](burst-substrate.md)). **etcd:** the newest snapshot (27.3 MB, about 6 h old) restored with `k3s server --cluster-reset --cluster-reset-restore-path=...` in **7 s**, API ready **12 s** after the start (**19 s** total, the droplet downloading the object itself in about 1 s); the datastore came back with prod's 31 namespaces and 129 secrets and prod's node objects. **Calico:** after deleting the IPAM handles and block affinities on the scratch cluster, applying the S3 export restored them in **4 s** (108 created, 28 configured, 0 errors; 74 handles, 6 affinities, 7 blocks, IPPool `10.42.0.0/16`; 78 of the 80 deleted names returned, the other 2 were created after the export), but only with `kubectl apply --validate=false` (IPAMBlock allocation arrays contain nulls that client-side validation rejects) and after stripping `resourceVersion`/`uid`/`generation` at the item level. **Vault Raft:** a scratch single-node Vault 1.21.2 with `seal "awskms"` and prod's key auto-unsealed a fresh store in 10 s, `vault operator raft snapshot restore -force` returned at once and the node was **unsealed with prod's data 6 s** after the restore started; its cluster ID became `0c79ac36-edf2-74e8-a3e0-e47783d61d88`, identical to prod's, at Raft index 1187892 (snapshot 1187525). **PBS leg (same day, on Urd, scratch VMIDs 15001 and 15002, no network so no IP clash, destroyed afterwards; PBS storage `pbs-backup`):** the newest backup of canary-1 (CT 1190, 753 MiB) restored in **17 s** and booted in 9 s with Debian 13, `vlagent` and `zabbix_agent2` present and enabled and the `ansible` user intact; the newest backup of Mimir (CT 1111, a real AdGuard replica, 1.5 GiB) restored in **23 s** and booted with the AdGuardHome unit and its config present and keepalived enabled. Command shape: `pct restore <scratch> pbs-backup:backup/ct/<vmid>/<ts> --storage local-lvm --onboot 0 --start 0`, then `pct set <scratch> --delete net0` BEFORE the first start, check with `pct exec`, then `pct stop` + `pct destroy --purge`. Not covered: a KV read from the restored Vault (no prod-valid token was used) and a VM (worker) restore from PBS.

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
   Then mirror both into 1Password per the offline-mirror rule (`[Asgard] - Mirror - Backups - K3s etcd-s3 IAM key`, `[Asgard] - Mirror - Backups - CronJob writer IAM key`; API Credential, username = access key, credential = secret). ✅ Mirrored 2026-10-01.
3. **Vault policy + role** — `terraform/vault` (root token): `terraform apply` creates `vault-snapshot`.
4. **etcd → S3** — `ansible-playbook playbooks/asgard-k3s.yml --tags k3s_etcd_s3 --limit k3s_cp` (the play is `serial: 1`; each CP restarts K3s and
   waits for Ready before the next). One playbook at a time across agents.
5. **CronJobs** — merge + push `main` (Flux applies `k8s/asgard/apps/backups`). Until step 2 landed the ExternalSecret is `SecretSyncedError` — expected.

## Verify (do all three — a backup never restored or listed is a hope)

```bash
# etcd: force one snapshot per CP and confirm the objects appear (list needs the k3s-etcd-backup key).
# Full path: `sudo`'s secure_path on the RHEL VMs has no /usr/local/bin, so bare `k3s` is "command not found".
ssh ansible@10.0.21.11 'sudo /usr/local/bin/k3s etcd-snapshot save --name s3-verify && sudo /usr/local/bin/k3s etcd-snapshot ls | grep s3://'
# CronJobs: trigger now, read the log, then list the prefix
kubectl -n backups create job --from=cronjob/calico-datastore-export calico-now
kubectl -n backups create job --from=cronjob/vault-raft-snapshot vault-now
kubectl -n backups logs job/calico-now ; kubectl -n backups logs job/vault-now -c upload
kubectl -n backups delete job calico-now vault-now
```
Expect `uploaded s3://xiiisins-homelab-backups/...` lines and, for Calico, `ippools=<n>` ≥ 1.

## Restore (outline — the burst substrate for the drill exists (10b2, code written, not yet applied); drill not yet run)

The drill procedure (scratch burst K3s with prod CIDRs + token, per-leg pass criteria, the credentials gap and the Vault KMS-seal caveat) is in [`burst-substrate.md`](burst-substrate.md) ("Restore drill"). What remains: apply the substrate, run the three legs, record the RTOs here.

- **etcd** — on one CP (others stopped): `k3s server --cluster-reset --cluster-reset-restore-path=<snapshot-name> --etcd-s3 --etcd-s3-bucket=… --etcd-s3-folder=etcd --etcd-s3-region=eu-west-1 --etcd-s3-access-key=… --etcd-s3-secret-key=…`
  (S3 restore is the same flag set as the config drop-in), then re-join the other CPs per [k3s-lifecycle.md](../known-issues/k3s-lifecycle.md) (`kubectl delete node` first).
- **Vault** — `vault operator raft snapshot restore -force <file>` against the active node (unsealed; restores KV, policies, auth methods, mounts). Needs a token with `sys/storage/raft/snapshot` update — i.e. root / break-glass.
- **Calico** — order matters: operator CRDs + tigera-operator addon → Installation (`calico-installation.yaml`) → wait for the datastore CRDs → `kubectl apply -f calico-datastore-<ts>.yaml` (IPPools first if the apply is split; strip `resourceVersion`/`uid`/`status` noise if the API rejects it). Then restart pods (their IPs must be in IPAM) — see the incident's recovery steps.

## Restore credentials (two identities, both in 1Password only, neither in Vault)

A restore must work when Vault is gone, so the credentials for it live in 1Password, never in Vault.

| Need | Identity | Where it lives |
|---|---|---|
| Read the three backup prefixes | IAM user `homelab-backup-restore` (`terraform/aws/backups.tf`): List + Get on `etcd/`, `vault-raft/`, `calico/`; explicit Deny on write/delete. **Terraform creates no key for it.** | 1P item `[Bootstrap] - Manual - AWS - Backup restore access key` |
| Decrypt/encrypt with the Vault unseal key (Vault leg only) | the existing `vault-unseal` IAM user: `kms:Encrypt`, `kms:Decrypt`, `kms:DescribeKey` on exactly one key (checked 2026-10-03; a scratch Vault needs Encrypt to initialise) | 1P item `[Bootstrap] - Manual - AWS - KMS unseal access key` |

Create the first one without the AWS console, from the main checkout (the key never appears on screen):

```bash
cd terraform/aws && set-aws-creds bootstrap && terraform apply      # creates the user + its read-only policy (2 resources)
scripts/secrets/mint-restore-key mint                                # aws iam create-access-key -> straight into 1Password, read back, verified
scripts/secrets/mint-restore-key verify                              # any time: reads all 3 prefixes, write/delete/IAM must be DENIED
scripts/secrets/mint-restore-key rotate                              # new key -> 1P -> verified -> old key deleted
```

`check` is a read-only preflight. The tool reads the Terraform bootstrap key from its 1Password item itself, so nothing needs loading. A key that cannot be stored in 1Password is deleted again, so no key exists only in a process.

## Known gaps

- **No alerting on a missed/failed backup.** A failed CronJob shows in `kubectl get jobs -n backups` only; the infra-health prober doesn't check bucket freshness yet (follow-up: newest-object age per prefix → Hermod).
- **No restore drill yet** — the scratch-cluster substrate now exists as code (`terraform/digitalocean-burst/`, [`burst-substrate.md`](burst-substrate.md); 10b2, not applied), but the restore steps above are still the documented intent, not a proven runbook.
- **Restore credentials: decided 2026-10-03 (path B).** No existing credential could read `vault-raft/` or `calico/` (the etcd user is `etcd/*` only, the writer is PutObject-only), so a dedicated read-only `homelab-backup-restore` user is declared in `terraform/aws` and its key is minted by `scripts/secrets/mint-restore-key` into 1Password only (see above). The Vault Raft restore also needs the KMS identity: the existing `vault-unseal` user already has Encrypt/Decrypt/DescribeKey on that one key, so no new KMS grant exists. **Applied and minted 2026-10-03:** the user exists, the key is in 1Password, and `mint-restore-key verify` passes (reads all three prefixes; write, delete and IAM denied). The restore drill is unblocked.
- The pre-upgrade Calico export written by `playbooks/calico-upgrade.yml` is still local to gondul (the daily CronJob export is the off-site copy).
