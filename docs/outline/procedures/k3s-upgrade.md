<!-- docs/outline/procedures/k3s-upgrade.md -->

# K3s & Calico upgrade

Rolling, in-place upgrades of the asgard cluster. Kubernetes forbids skipping minor versions, so each run moves **one minor**. Calico is upgraded separately, because it is deliberately not managed by Flux.

---

## Why bumping a version does nothing

The `k3s` Ansible role only installs on nodes that are not already healthy members. Changing `k3s_version` in the role defaults therefore has no effect on a running cluster; it only matters for a future rebuild. Upgrades go through a dedicated playbook that takes a target version.

## K3s

**Pre-flight (read-only).**

- Pick the newest stable patch of the next minor and confirm the binary and checksum assets exist.
- Check what rides on the new minor: Flux's compatibility table, the chart support for cert-manager, ESO, Vault, Traefik and MetalLB, deprecated APIs in use, cgroup v2, and the `k3s-selinux` package.
- All six nodes Ready. Workers are heavily CPU-requested, so a drain will leave some pods Pending (and pinned local-path pods unavailable) until the node returns. That is an accepted, temporary outage.

**What the playbook does.** Pre-flight and an on-demand etcd snapshot, then control planes one at a time with no drain (they hold no workloads): checksum-verified binary, SELinux relabel, restart, wait for Ready and for etcd health before the next. Then workers one at a time: drain, swap, restart, wait, uncordon. Already-upgraded nodes are skipped, so a failed run is simply re-run. Restarting the service does not stop running pods.

**After each hop.** All nodes at the target version; Flux releases Ready; Calico status Available; ExternalSecrets synced; Vault 3 of 3 unsealed; the smoketest endpoint returns 200. Only after the final hop is the version in the role defaults updated.

**Watch for.** Workers can hit DiskPressure right after a hop, because drained pods pull images onto small OS disks; it self-heals within minutes as image garbage collection runs. Check control-plane disk space beforehand, since an etcd peer being down floods logs.

**Rollback.** Re-run with the previous version (reversible until the control plane has migrated stored objects, so treat a completed control-plane upgrade as one-way), or restore the pre-upgrade etcd snapshot.

## Calico

Calico is installed as K3s addon files on the init node, and **K3s prunes per file**: replacing a file with a manifest that lacks a CRD deletes every stored object of that kind. That is exactly how the Calico datastore was once wiped. So:

1. Bump `calico_version` one minor and commit.
2. Run the Calico upgrade playbook. It gates on cluster health, downloads the target manifests, **refuses if either addon file would lose a CRD**, and exports the whole Calico datastore first.
3. Run the version-drift playbook, which reports K3s and Calico versions against Git.

If the datastore is ever wiped anyway, recovery means restoring the addon files, finalising a deadlocked `calico-system` namespace if needed, and recreating every pod one namespace at a time (Vault one pod at a time).

---

## See also

- **Compute & hypervisors** (Components): how K3s is installed.
- **Backups & restore drills** (this section): the etcd and Calico backups this relies on.
- **Kubernetes & Flux** troubleshooting (Troubleshooting).
