<!-- docs/procedures/k3s-upgrade.md -->

# K3s rolling upgrade (asgard)

In-place, one **minor** version per run (Kubernetes forbids skipping minors), via
[`ansible/playbooks/k3s-upgrade.yml`](../../ansible/playbooks/k3s-upgrade.yml).
`roles/k3s` only installs on nodes that are not yet healthy members
(`detect-state.yml`), so bumping `k3s_version` alone does **nothing** on a running cluster.

## Pre-flight (read-only)

1. Pick the next minor's newest stable patch (`curl -s https://update.k3s.io/v1-release/channels`, or GitHub releases of `k3s-io/k3s`). Confirm the binary + `sha256sum-amd64.txt` assets exist for `v1.NN.N+k3sN`.
2. Check what rides on the new Kubernetes minor: Flux (its minimum K8s is in the release notes' compatibility table), cert-manager/ESO/Vault/Traefik/MetalLB chart support, Calico (see below), deprecated APIs in use (`kubectl get --raw /metrics | grep apiserver_requested_deprecated_apis`), cgroup v2 (`stat -fc %T /sys/fs/cgroup` = `cgroup2fs`; required from K8s 1.35), `k3s-selinux` from the stable repo.
3. All 6 nodes Ready. Workers are ~85-90 % CPU-*requested*: a drain moves that worker's pods onto the other two, so expect Pending pods (and local-path/iSCSI-pinned pods) until the node is uncordoned — accepted outage, not permanent. See [k8s-scheduling.md](../known-issues/k8s-scheduling.md).

## Run

```fish
source (git rev-parse --show-toplevel)/.config/scripts/homelab.sh; vault-homelab-env >/dev/null   # or: . ~/.cache/homelab/env.sh
cd ansible
ansible-playbook playbooks/k3s-upgrade.yml -e k3s_target_version=v1.34.11+k3s1
```

What it does: pre-flight (all nodes Ready, on-demand `etcd-snapshot save`) → control planes one at a time, **no drain** (tainted, no workloads): download checksum-verified binary → `restorecon` → restart → wait Ready + version → wait `/readyz/etcd` before the next CP → workers one at a time: drain → swap → restart → wait → uncordon. Already-upgraded nodes are skipped, so a failed run is simply re-run.

Restarting the service does not stop running pods (`KillMode=process`).

## After each hop

- `kubectl get nodes` all at the target, `flux get hr -A` all Ready, `kubectl get tigerastatus` all Available, ExternalSecrets synced, Vault 3/3 unsealed, `curl https://smoketest.niflheim.xiiisins.com/anything` = 200.
- Only after the **final** hop: set `k3s_version` in `ansible/roles/k3s/defaults/main.yml` so a teardown/rebuild installs it.

## Rollback

Workers/CPs: re-run the playbook with the previous `k3s_target_version` (binary swap is reversible until the control plane has migrated stored objects — treat a completed CP upgrade as one-way). Cluster-level recovery: restore the `pre-upgrade-*` etcd snapshot taken at the start of the run (`k3s server --cluster-reset --cluster-reset-restore-path=...` on one CP, then rejoin the others). Snapshots live in `/var/lib/rancher/k3s/server/db/snapshots/` on each CP.

## Calico

Calico is **not** Flux-managed: `calico_version` in `roles/k3s/defaults/main.yml` is downloaded by `tasks/calico.yml` as an addon manifest on the init node (and only when the node is not already healthy). To move Calico on a live cluster, replace `/var/lib/rancher/k3s/server/manifests/tigera-operator.yaml` on the init node with the new version's `manifests/tigera-operator.yaml`, then watch `kubectl get tigerastatus`.
