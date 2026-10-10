<!-- docs/procedures/pbs-restore-test.md -->

# Scheduled PBS restore test

*Phase 10g prerequisite (the undo path of stage B, [`10g-rebuild-loop.md`](../plans/active/10g-rebuild-loop.md)). Code: [`ansible/playbooks/pbs-restore-test.yml`](../../ansible/playbooks/pbs-restore-test.yml) + [`tasks/pbs-restore-test-one.yml`](../../ansible/playbooks/tasks/pbs-restore-test-one.yml); Semaphore template `pbs-restore-test` (`terraform/semaphore/templates.tf`). Decision row: [`decisions.md`](../operations/decisions.md) ("PBS restore test...").*

A backup nobody restores is a hope. Every Wednesday 07:22 UTC (hours after the 01:00 UTC nightly job) Semaphore restores the newest PBS backup of two guests into a scratch container, checks it, and destroys it.

## What it restores

| Guest | When | Checks |
|---|---|---|
| `canary-1` (1190) | every run | rootfs mounts and holds an OS; **boots with no network**; the **sentinel** the previous run wrote (`/var/lib/restore-test/sentinel`, `<epoch> <run id>`) is in the restored copy; host key matches the live guest |
| one production LXC | rotates by ISO week through `restore_test_guests` (17 LXCs: hugin, hermod, saga, mimir, kvasir, bifrost, heimdall, gjallarbru, factorio, gna, ratatoskr, the three Patroni nodes, the three HAProxy/etcd nodes) | rootfs mounts, `os-release`/`hostname`/`dpkg/status` present, at least 100 MB used, host key matches the live guest (a mismatch only warns: the guest may have been rebuilt since the backup) |

The **restore node** also rotates (urd, verd, skuld by ISO week), so each node's PBS client path is exercised. Excluded on purpose: PBS 1101 (restoring the backup server onto itself) and Jellyfin 1123 (device passthrough needs root@pam ticket auth; its restore is part of the 5h J5 acceptance). VMs (the K3s workers' `/data`) are not covered yet: that is the 10g3 gate.

Every guest also needs a backup **newer than 36 h**: a missed nightly job fails the test with "the nightly job missed it".

## Safety rules (why a restore can't hurt production)

- The scratch CT is always **id 1199** (reserved for this; canaries are 1190-1192), hostname `restore-test-<name>`. A leftover from a crashed run is destroyed on the next run, but only if it carries that hostname prefix; anything else on 1199 aborts the run.
- Restored with `--unique` (new MAC) and `--onboot 0`. Every `net*`, `dev*` and bind `mp*` entry is **deleted before the CT can start**, so a restored copy can never answer on the production IP or touch a host path.
- Destroyed in an `always` block, then confirmed gone; a failed cleanup fails the run.
- Not Terraform- or NetBox-managed: it lives for minutes. The Proxmox Zabbix template may discover it during that window; if a stopped-CT problem ever opens for `restore-test-*`, that is this test, not an incident.

## Results

- Failure: Hermod `alert` (`#infra-alerts`) with one line per failed guest, and the Semaphore task fails.
- Success: one `info` line (the FYI channel). It is the **weekly heartbeat**: silence on a Wednesday means the schedule or Semaphore stopped, not that all is well.

## Manual run

From Frigg (disposable worktree, one playbook at a time; the Mac works too, see [`frigg-control-node.md`](../known-issues/frigg-control-node.md)):

```bash
ansible-playbook playbooks/pbs-restore-test.yml
ansible-playbook playbooks/pbs-restore-test.yml -e restore_test_node=verd -e restore_test_vmid=1131
```

Without `HERMOD_URL` (a manual run) the notify tasks skip and the summary is printed. Overrides: `restore_test_node`, `restore_test_vmid`, `restore_test_max_age_h`.

To prove the sentinel path by hand: run once (writes the sentinel), `vzdump 1190 --storage pbs-backup --mode snapshot` on Urd, run again: the canary line must say "sentinel from the previous run found in the restored copy". Any run between the backup and the check rewrites the sentinel and makes the comparison skip ("live sentinel is newer than the backup").

## Proven 2026-10-10

Manual runs from Frigg: canary-1 + ratatoskr on Urd (43 s + 33 s), canary-1 + `vor` (27 GiB) on Verd (346 s for the pair), the sentinel comparison, and a negative run (`restore_test_max_age_h=1`) that failed the right guest with the right message, cleaned up, and left no scratch CT or LV on any node.
