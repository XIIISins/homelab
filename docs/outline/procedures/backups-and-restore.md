<!-- docs/outline/procedures/backups-and-restore.md -->

# Backups & restore drills

A backup nobody restores is a hope. This page covers what is backed up where, and the drills that prove each path works.

---

## What is backed up

| State | Mechanism | Cadence | Where |
|---|---|---|---|
| LXCs and VMs (including worker `/data` disks) | PBS job | Nightly (01:00 UTC) | Munin Volume 1 |
| Postgres | PBS of each node, plus a logical dump taken by whichever node is the leader | Daily | Munin |
| K3s etcd | Snapshot from each control plane to S3 | Every 12 h per node | S3 bucket outside the homelab |
| Vault | Raft snapshot (CronJob, Kubernetes auth) | Daily 02:45 UTC | S3, 30-day history |
| Calico datastore | Export of the Calico objects (no secrets) | Daily 02:30 UTC | S3, 30-day history |
| Sealed-secrets keypair, Vault root and unseal credentials | 1Password | Manual | 1Password |

The S3 bucket is versioned, private and TLS-only. Two IAM identities: etcd's can list and prune its own prefix; the CronJobs' writer can only put objects. etcd snapshots contain Kubernetes Secrets in plaintext, so the bucket and the etcd credential matter. The whole thing costs a few cents a month.

## Drills

**Weekly PBS restore test (Semaphore, Wednesday morning).** Restores the newest backup of a canary every run and of one production LXC chosen by rotating through a list, onto a rotating Proxmox node. The restore goes into a scratch container with a reserved ID, a new MAC, **no network and no host-path mounts** (all removed before first start), so it cannot answer on a production address. It checks the filesystem mounts and holds an OS, that the host key matches, that a sentinel written by the previous run is present, and that the canary boots. It then destroys the scratch container. Every guest must also have a backup newer than 36 hours, which catches a missed nightly job.

Failure posts an `alert`. Success posts a single `info` line, which is the weekly heartbeat: **silence on a Wednesday means the test stopped, not that all is well.**

**S3 recovery drill.** Done once into a throwaway one-node cluster with production's CIDRs: etcd restored in seconds, the Calico export restored the IPAM objects, and a scratch Vault with the production KMS key unsealed a fresh store and took the Raft snapshot with production's data. A worker-VM restore from PBS is not yet covered.

**Postgres.** Point-in-time recovery (WAL archiving) is not built; a restore is "replay WAL from the last backup".

## What to do when restoring for real

- The whole-cluster path is in **Teardown & rebuild**. Restoring a Postgres node is a Patroni re-init from the leader.
- Never delete an old volume until the restored workload is confirmed healthy.
- After moving a Synology shared folder between volumes, its NFS export path changes; stale clients break. Restore-verify a moved datastore before relying on it.

---

## See also

- **Storage & data** (Components): the tiers and the backup table.
- **Teardown & rebuild** (this section).
- **Vault recovery** (this section).
