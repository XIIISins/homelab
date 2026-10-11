<!-- docs/outline/procedures/backups-and-restore.md -->

# Backups & restore drills

A backup nobody restores is a hope. This runbook shows what is backed up, how to prove each backup exists and is recent, how to rehearse a restore safely, and how to do a real one.

**Use when:** a backup alert fired, you must confirm backups are healthy, or you must restore something.
**Prerequisites:** Session setup. For restores: the owner's go-ahead (restores can overwrite live data).

---

## 1. What is backed up (and where)

| State | Mechanism | Cadence | Where | Restore needs |
|---|---|---|---|---|
| LXCs and VMs (incl. worker `/data` disks) | PBS (LXC 1101 on Urd) | Nightly 01:00 UTC | Munin, `Volume1` NFS datastore | PVE host + PBS up |
| Postgres | PBS of each node + daily logical dump on the leader (`/var/backups/postgresql`) | Daily | Munin (via PBS) | PBS file-restore |
| K3s etcd (contains Secrets in plaintext) | K3s `etcd-snapshot` to S3 from all 3 control planes | Every 12 h each | S3 `xiiisins-homelab-backups/etcd/` | the restore IAM key (1Password only) |
| Vault | Raft snapshot CronJob (`backups` ns), Kubernetes auth | Daily 02:45 UTC | S3 `vault-raft/` (30 d) | root token + KMS key |
| Calico datastore | Export CronJob | Daily 02:30 UTC | S3 `calico/` (30 d) | the restore IAM key |
| Sealed-secrets keypair, Vault root + unseal credentials, AppRole creds, SSH keys | Manual | After change | 1Password `[Bootstrap] - …` | `op` |
| Media library | None (replaceable) | | | |
| Immich photos | None; a secondary copy by design | | | |

The restore credentials live **only in 1Password** (so a restore works when Vault is gone): `[Bootstrap] - Manual - AWS - Backup restore access key` (read-only on the three prefixes) and `[Bootstrap] - Manual - AWS - KMS unseal access key` (Vault leg only).

---

## 2. Prove the backups are healthy (read-only; do this daily or when alerted)

### PBS / Proxmox backups

```bash
# Backup job definition and schedule
ssh root@urd.niflheim.xiiisins.com 'pvesh get /cluster/backup --output-format json | jq'

# Newest backup per guest (look for anything older than ~36 h)
ssh root@urd.niflheim.xiiisins.com 'pvesm list pbs-backup --content backup | sort -k2 | tail -40'
ssh root@urd.niflheim.xiiisins.com 'pvesm list pbs-backup --vmid 1130'           # one guest's history

# PBS itself: recent task failures, datastore health, is the NFS datastore mounted?
ssh root@pbs.niflheim.xiiisins.com 'proxmox-backup-manager task list --limit 20 | grep -v OK'
ssh root@pbs.niflheim.xiiisins.com 'proxmox-backup-manager datastore list; df -h | grep -iE "nfs|proxmox-backup"'
```

If the datastore directory exists but is **not mounted**, PBS services may still report active: mount it, then `systemctl restart proxmox-backup proxmox-backup-proxy`.

### S3 recovery state

```bash
# Is the daily Vault + Calico upload happening? (CronJobs in the backups namespace)
kubectl -n backups get cronjob,job
kubectl -n backups logs job/$(kubectl -n backups get jobs -o name | grep vault-raft | tail -1 | cut -d/ -f2) -c upload | tail -5

# etcd snapshots: list from a control plane (full path: sudo's PATH lacks /usr/local/bin)
ssh -o IdentitiesOnly=true -i "$ANSIBLE_PRIVATE_KEY_FILE" ansible@10.0.21.11 \
  'sudo /usr/local/bin/k3s etcd-snapshot ls | grep s3:// | tail -6'

# Force a fresh etcd snapshot now (any control plane)
ssh ... ansible@10.0.21.11 'sudo /usr/local/bin/k3s etcd-snapshot save --name manual-$(date +%Y%m%d%H%M)'

# Trigger the CronJobs on demand, read their log, clean up
kubectl -n backups create job --from=cronjob/calico-datastore-export calico-now
kubectl -n backups create job --from=cronjob/vault-raft-snapshot vault-now
kubectl -n backups wait --for=condition=complete job/calico-now job/vault-now --timeout=180s
kubectl -n backups logs job/calico-now | tail -3         # expect "uploaded s3://xiiisins-homelab-backups/calico/..." and ippools>=1
kubectl -n backups logs job/vault-now -c upload | tail -3
kubectl -n backups delete job calico-now vault-now
```

A failed CronJob shows only in `kubectl get jobs -n backups`; there is no alert for it yet, so look here when you suspect a gap.

### Vault, by hand (before any storage-touching maintenance)

```bash
kubectl -n vault exec vault-0 -c vault -- env VAULT_TOKEN="$(op read 'op://Homelab 2.0/7g4grolyien2yqkm7me2jficmy/password')" \
  vault operator raft snapshot save /tmp/pre-maint.snap
kubectl -n vault cp vault/vault-0:/tmp/pre-maint.snap ./pre-maint.snap -c vault
kubectl -n vault exec vault-0 -c vault -- rm /tmp/pre-maint.snap
ls -l pre-maint.snap                       # keep it safe; it is the whole of Vault
```

---

## 3. Rehearse a restore (the weekly drill)

The Semaphore `pbs-restore-test` template does this every Wednesday 07:22 UTC. To run it now, start the template (**Everyday operations** §8). It restores the newest backup of `canary-1` and one rotating production LXC into a scratch container, checks it, and destroys it. Results: a failure posts a Hermod `alert`; success posts one `info` line. **Silence on a Wednesday means the test stopped.**

The same by hand on a Proxmox node, for one guest (safe: no network, scratch ID, destroyed after):

```bash
ssh root@urd.niflheim.xiiisins.com
# 1. Find the newest backup of the guest (example: canary-1, CT 1190)
pvesm list pbs-backup --vmid 1190 | tail -1                         # note the volid: pbs-backup:backup/ct/1190/<timestamp>
# 2. Restore into the reserved scratch ID 1199, never onto the real ID
pct restore 1199 pbs-backup:backup/ct/1190/<timestamp> --storage local-lvm --unique --onboot 0 --start 0
pct set 1199 --hostname restore-test-canary-1 --delete net0          # remove the NIC BEFORE first start (no IP clash)
# 3. Start it and check it came up
pct start 1199 && sleep 10
pct exec 1199 -- bash -c 'cat /etc/os-release | head -2; systemctl is-system-running; ls /etc/ssh/ssh_host_*'
# 4. Destroy it
pct stop 1199; pct destroy 1199 --purge
```

ID `1199` is reserved for this; do not use it for anything else, and do not restore over a live ID. Expect harmless Zabbix "restarted (uptime < 10m)" problems for `CT1199` while it exists.

**S3 legs** (etcd, Calico, Vault) are rehearsed on the ephemeral DigitalOcean burst cluster, not on asgard. The procedure and acceptance criteria are in the repository (`docs/procedures/burst-substrate.md`, "Restore drill"). Re-run it after any change to the producers, the bucket or the restore credentials. Proven results: etcd restored in ~7 s, Calico IPAM in ~4 s, a scratch Vault with the production KMS key unsealed and held production's data ~6 s after the snapshot restore.

---

## 4. A real restore

### 4a. One LXC or VM (data loss or corruption in one guest)

```bash
# On the PVE host that should own the guest. Stop it first.
pct stop <ctid>                                                     # (VMs: qm stop <vmid>)
pvesm list pbs-backup --vmid <ctid> | tail -5                      # choose the timestamp
# Restore OVER the original ID (this replaces the guest's disk)
pct restore <ctid> pbs-backup:backup/ct/<ctid>/<timestamp> --storage local-lvm --force
pct start <ctid>
```

Restoring over an ID that Terraform already created keeps the original ID. For a **Patroni, etcd or Vault member**, do not restore blind: follow *Postgres operations* (re-seed from the leader is usually better than restoring a stale data directory) or *Vault recovery*. For a guest with passthrough (Jellyfin), the restored copy has a fresh root-owned cache volume (the service reapplies ownership on start) and needs `/media` mounted.

### 4b. A single file from a backup

In the PBS web UI (`https://pbs.niflheim.xiiisins.com:8007`): Datastore → Content → select the snapshot → **File Restore**, or from a node: `proxmox-backup-client restore` / the Proxmox UI's backup browser. Use this to recover a Postgres dump file from `/var/backups/postgresql`.

### 4c. etcd (the cluster state)

Last resort; the cluster is down or its state is corrupt. **Escalate first.** The shape: stop K3s on the other two control planes, run on one control plane `k3s server --cluster-reset --cluster-reset-restore-path=<snapshot-name>` (add the S3 flags to restore from S3), restart, then rejoin the others after `kubectl delete node <name>` for each. Full command list with the S3 flag set is in the repository (`docs/procedures/offsite-backups.md`, "Restore"). Local snapshots live in `/var/lib/rancher/k3s/server/db/snapshots/` on each control plane.

### 4d. Vault

See **Vault recovery**: `vault operator raft snapshot restore -force <file>` against the active node, needing a root token.

### 4e. Calico

Order matters: tigera operator addon → Installation → wait for the datastore CRDs → `kubectl apply --validate=false -f calico-datastore-<ts>.yaml` (strip `resourceVersion`, `uid`, `generation`), then restart pods so their IPs are in IPAM. **Escalate before starting.**

---

## 5. Verify after any restore

```bash
kubectl get nodes; flux get kustomizations; kubectl get externalsecrets -A | grep -v SecretSynced
kubectl exec -n vault vault-0 -c vault -- vault status | grep -E "Sealed|HA Mode"
ssh -o IdentitiesOnly=true -i "$ANSIBLE_PRIVATE_KEY_FILE" ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml list'
curl -s https://smoketest.niflheim.xiiisins.com/anything
```

And ask the service owner to confirm the data they care about is there.

---

## See also

- **Storage & data** (Components): the backup design.
- **Vault recovery**, **Postgres operations**, **Teardown & rebuild** (this section).
- **Monitoring & automation** (Troubleshooting): backup alerts.
