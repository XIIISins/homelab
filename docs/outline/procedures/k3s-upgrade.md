<!-- docs/outline/procedures/k3s-upgrade.md -->

# K3s & Calico upgrade

Rolling, in-place upgrades of the asgard cluster. Kubernetes forbids skipping minor versions, so **one minor per run**. Calico is upgraded separately because it is not managed by Flux.

**Use when:** the owner has chosen a target K3s or Calico version (the `chart-bump` agent proposes them and orders the work).
**Operator-run.** Not for the on-call standby unless explicitly handed over. Run from Frigg.
**Duration:** roughly 30 to 60 minutes per K3s minor. Plan a window: drained workers leave some pods Pending (workers are heavily CPU-requested, and `local-path` or iSCSI pods are pinned to their node). That outage is accepted and temporary.

> Bumping `k3s_version` in `ansible/roles/k3s/defaults/main.yml` does **nothing** to a running cluster: the role only installs on nodes that are not yet healthy members. The upgrade playbook takes the target version explicitly. Update the role default only after the **final** hop, so a rebuild installs the right version.

---

## Part 1: K3s

### 1. Pre-flight (read-only)

```bash
# Current versions
kubectl get nodes -o custom-columns=NAME:.metadata.name,VERSION:.status.nodeInfo.kubeletVersion,READY:.status.conditions[-1].status
cd ~/homelab/ansible && ansible-playbook -i inventory/hosts.yml playbooks/platform-version-drift.yml     # reports K3s/Calico vs git

# Find the newest stable patch of the NEXT minor
curl -s https://update.k3s.io/v1-release/channels | jq -r '.data[]|select(.name|test("^v1\\.[0-9]+$"))|"\(.name) \(.latest)"'
# Confirm the binary and checksum assets exist for the exact version
V='v1.NN.N+k3sN'; curl -sI "https://github.com/k3s-io/k3s/releases/download/${V}/k3s" | head -1; curl -sI "https://github.com/k3s-io/k3s/releases/download/${V}/sha256sum-amd64.txt" | head -1

# Health gates: all green before you start
kubectl get nodes | grep -vc " Ready"                          # 1 (the header) means all Ready
kubectl get --raw /readyz/etcd; echo                            # ok
flux get kustomizations | grep -v True; flux get hr -A | grep -v True      # nothing
kubectl get tigerastatus                                        # all Available
kubectl -n vault exec vault-0 -c vault -- vault status | grep -E "Sealed|HA Mode"
curl -s https://smoketest.niflheim.xiiisins.com/anything         # smoketest ok

# Disk space: OS disks (CP 20 GB, worker 30 GB) must have headroom; images land on them
for ip in 10.0.21.11 10.0.21.12 10.0.21.13 10.0.21.21 10.0.21.22 10.0.21.23; do
  echo -n "$ip "; ssh -o IdentitiesOnly=true -i "$ANSIBLE_PRIVATE_KEY_FILE" ansible@$ip "df -h / | tail -1 | awk '{print \$5\" used\"}'"
done
```

Also check what rides on the new Kubernetes minor: Flux's minimum Kubernetes version (its release notes), chart support for cert-manager, External Secrets, Vault, Traefik, MetalLB, deprecated APIs in use (`kubectl get --raw /metrics | grep apiserver_requested_deprecated_apis`), cgroup v2 (`stat -fc %T /sys/fs/cgroup` must be `cgroup2fs`; required from Kubernetes 1.35), and the `k3s-selinux` package in the stable repo. **Upgrade K3s before Flux** when Flux's minimum rises.

### 2. Run

```bash
cd ~/homelab/ansible
ansible-playbook -i inventory/hosts.yml playbooks/k3s-upgrade.yml -e k3s_target_version=v1.NN.N+k3sN
```

What it does, in order: pre-flight (all nodes Ready, on-demand `etcd-snapshot save`) → control planes **one at a time, no drain** (tainted, no workloads): checksum-verified binary → `restorecon` → restart → wait Ready and the target version → wait `/readyz/etcd` before the next → workers **one at a time**: drain → swap → restart → wait → uncordon. Already-upgraded nodes are skipped, so a failed run is simply re-run. Restarting the service does not stop running pods (`KillMode=process`). `FAILED - RETRYING … Wait until this node is Ready` once or twice per node is normal (30 to 60 seconds to re-register).

Watch in another terminal:

```bash
watch -n10 'kubectl get nodes -o wide; kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded | head -20'
```

### 3. After each hop

```bash
kubectl get nodes -o custom-columns=NAME:.metadata.name,VERSION:.status.nodeInfo.kubeletVersion   # all at the target
flux get hr -A | grep -v True ; flux get kustomizations | grep -v True                              # empty
kubectl get tigerastatus                                                                             # all Available
kubectl get externalsecrets -A | grep -v SecretSynced                                                # empty
kubectl -n vault exec vault-0 -c vault -- vault operator raft list-peers                             # 3 voters
curl -s https://smoketest.niflheim.xiiisins.com/anything                                             # smoketest ok
```

Workers may sit under `DiskPressure` for about five minutes right after a drain (drained pods pull images onto small OS disks); evicted pods and `node.kubernetes.io/disk-pressure` clear on their own. Clean up leftovers: `kubectl get pods -A | grep -E "Evicted|Error|ContainerStatusUnknown"` → `kubectl delete pod …`.

If Flux HelmReleases show `does not have an artifact` right after a Flux upgrade, wait a few minutes. If a HelmRelease is stuck `RetriesExceeded`: `flux reconcile helmrelease <name> -n <ns> --reset`.

### 4. After the final hop only

Set `k3s_version` in `ansible/roles/k3s/defaults/main.yml` to the new version, PR it (**Landing a change**), and run the drift playbook (`platform-version-drift.yml`): it should pass.

### Rollback

Re-run the playbook with the previous `k3s_target_version`. The binary swap is reversible until the control plane has migrated stored objects, so **treat a completed control-plane upgrade as one-way**. Cluster-level recovery: restore the `pre-upgrade-*` etcd snapshot taken at the start of the run: on one control plane `k3s server --cluster-reset --cluster-reset-restore-path=<snapshot>`, then rejoin the others after `kubectl delete node <name>`. Local snapshots: `/var/lib/rancher/k3s/server/db/snapshots/` on each control plane. **Escalate before restoring.**

---

## Part 2: Calico (one minor per run, git-driven)

Calico is installed as **K3s addon files** on the init node, and **K3s prunes per file**: swapping an addon file for a newer manifest that lacks a CRD deletes every stored object of that kind. This is exactly how the Calico datastore was once wiped. **Never replace those files by hand.**

```bash
# 1. Bump calico_version by ONE minor in ansible/roles/k3s/defaults/main.yml; PR and merge it (Landing a change)

# 2. Run the upgrade playbook (target defaults to calico_version)
cd ~/homelab/ansible
ansible-playbook -i inventory/hosts.yml playbooks/calico-upgrade.yml
#   Override only deliberately:  -e calico_target_version=v3.NN.N
#   It: gates on TigeraStatus + nodes -> downloads target manifests -> REFUSES if either addon file would lose a CRD
#   (-e '{"calico_allow_crd_removal":["<crd>"]}' only for a verified, deliberate removal) -> exports the whole
#   Calico datastore to /var/lib/rancher/k3s/calico-datastore-backup-*.yaml -> applies

# 3. Confirm
ansible-playbook -i inventory/hosts.yml playbooks/platform-version-drift.yml     # K3s per node, Calico version and pool CIDR vs git; fails on drift
kubectl get tigerastatus                                                         # all Available
kubectl get ippools.crd.projectcalico.org                                        # 10.42.0.0/16 present
kubectl get installation default -o jsonpath='{.spec.calicoNetwork.nodeAddressAutodetectionV4}{"\n"}'   # cidrs=[10.0.21.0/24]
curl -s https://smoketest.niflheim.xiiisins.com/anything                         # smoketest ok = traffic crosses nodes
kubectl get pods -A -o wide | grep -E "external-secrets|metallb-speaker|calico-node" | grep -v Running   # empty
```

The `Installation` object is merged by the addon controller, so fields you removed can persist; after changes, read the live object and `kubectl patch --type=json` stale fields. The MTU is pinned to 1450 (a workaround for an operator/SELinux bug); do not remove it.

**If the Calico datastore is ever wiped** (IP pools, IPAM blocks and block affinities gone; operator logs `Installation object is terminating` or `waiting for enabled IP pools`; cross-node pod traffic fails while nodes stay Ready): **escalate immediately.** The recovery is: restore the CRD and Installation addon files, finalise a `calico-system` namespace stuck `Terminating` (`kubectl get ns calico-system -o json | jq '.spec.finalizers = []' | kubectl replace --raw /api/v1/namespaces/calico-system/finalize -f -`), restore the datastore export from S3 or the node (`kubectl apply --validate=false -f <export>.yaml` after stripping `resourceVersion`, `uid`, `generation`), and then **recreate every pod** (their IPs are no longer in IPAM): namespace by namespace, Vault one pod at a time.

---

## See also

- **K3s node rebuild** (this section).
- **Backups & restore drills** (this section): the etcd and Calico backups this relies on.
- **Kubernetes & Flux**, **DNS & networking** (Troubleshooting).
