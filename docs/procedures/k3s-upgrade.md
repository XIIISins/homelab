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

## Calico (separate from K3s; git-driven, one minor per run)

Calico is **not** Flux-managed. It lives as K3s *addon files* on the init node and K3s **prunes per file** — never swap those files by hand (that deleted the Calico datastore on 2026-10-01: [incident](../incidents/2026-10-01-calico-datastore-prune.md)).

1. Bump `calico_version` in `ansible/roles/k3s/defaults/main.yml` (one minor above what runs) and commit.
2. `ansible-playbook playbooks/calico-upgrade.yml` (target defaults to `calico_version`; override with `-e calico_target_version=v3.NN.N`). It: gates on TigeraStatus + nodes → downloads the target manifests → **refuses if either addon file would lose a CRD** (`-e '{"calico_allow_crd_removal":["<crd>"]}'` for verified-deliberate removals; 3.31 → 3.32 drops the two k8s AdminNetworkPolicy CRDs) → **exports the whole Calico datastore** to `/var/lib/rancher/k3s/calico-datastore-backup-*.yaml` → applies `operator-crds.yaml` as its own addon → replaces the operator → waits for `status.calicoVersion` + all TigeraStatus Available + calico-node rolled → asserts IPPool/IPAMBlock/BlockAffinity counts did not shrink.
3. `ansible-playbook playbooks/platform-version-drift.yml` — K3s per node, Calico version and pool CIDR vs git (read-only; fails on drift).
4. If the datastore is ever wiped anyway: see the recovery in the incident doc (restore CRDs/Installation addon files, finalizers, finalize a deadlocked `calico-system`, then recreate all pods; Vault one at a time).
