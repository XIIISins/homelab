<!-- docs/known-issues/k3s-lifecycle.md -->

# Known gotchas — K3s lifecycle / rebuilds

*Migrated from `CLAUDE.md`. Recovery commands + rules. Incident retros in [`../incidents/`](../incidents/).*

## K3s lifecycle / rebuilds

- **CP rebuild → "duplicate node name found"**: `kubectl delete node <name>` from a surviving CP *before* starting K3s on the new VM. The K3s node-delete handler also evicts the stale etcd member.
- **CP rebuild of the default init node**: override with `ansible-playbook playbooks/asgard-k3s.yml --limit <name> -e 'k3s_init_node=hlokk'` (any healthy CP). Otherwise the role `--cluster-init`s a fresh cluster.
- **K3s role install-skip on healthy nodes.** `detect-state.yml` sets `k3s_already_healthy` if `is-active k3s == active` AND node `Ready` → `install.yml` + `calico.yml` skipped. `config.yml` is separate and ALWAYS runs (split out 2026-05-21 — config-template was previously bundled with install.yml and never rendered on healthy nodes).
- **K3s `node-taint:` config is registration-time only.** Restarting K3s on an existing cluster member does NOT re-apply taints. For existing nodes: `kubectl taint node <name> ...`. Config-template change still matters for fresh bootstraps.
- **K3s restart on existing CP**: empirically does NOT trigger duplicate-node-name in steady state (caveat, not blocker — only fresh-bootstrap is at risk).
- **`NoSchedule` taint does NOT evict existing workload pods** — only blocks new scheduling. DaemonSets respect taints automatically; Deployments/StatefulSets stay until natural churn or explicit `kubectl delete pod`. Don't taint a CP with stateful pods expecting them to move.
- **Vault Helm chart uses required (not preferred) pod anti-affinity.** 3 replicas × 3 workers = exactly 3 slots. Cordoning a worker leaves the displaced Vault pod Pending. Accept 2/3 voters for ~20-30 min during single-worker rebuilds (Vault stays fully read+write).
- **Step Raft leadership BEFORE drain/delete** on the doomed worker. `vault operator step-down` first (while pod healthy) lets the cluster elect cleanly; pod-delete after is a clean follower departure. Cost ~10s. Applies to any Raft-quorum workload.
- **Default Ansible playbook execution is parallel** — multi-node-outage footgun for K3s. A config change triggering `restart-k3s` fires across all 6 nodes at once. Pending: `serial: 1` default in `asgard-k3s.yml`. Until then: stage config changes with `--limit <one-node>` first.

## Minor-version upgrade (k3s-upgrade.yml)

- **Workers can hit DiskPressure right after a hop.** Draining moves every pod onto the remaining workers, which pull images they didn't have; the 30 GB OS disks (containerd lives on `/`) cross the kubelet threshold (nodefs < 10 % / imagefs < 15 % free). Symptoms seen 2026-09-30 after 1.33 → 1.34: `node.kubernetes.io/disk-pressure:NoSchedule` taint on `einherjar-verd`, DaemonSet pods `Evicted` (`Pod admission denied … DiskPressure`), `vault-2` Pending (local-path PV pinned to that node), then everything self-healed ~5 min after kubelet image-GC freed space (the condition lingers for `evictionPressureTransitionPeriod`, default 5 m). Leftover `Evicted`/`Error`/`ContainerStatusUnknown` pod records are harmless once replacements are Ready. **Before the next hop:** `df -h /` on every worker (`ansible k3s_worker -m shell -a 'df -h /; du -sh /var/lib/rancher/k3s/agent/containerd'`) — skuld was 77 % (19 GB containerd), urd 56 %; prune unused images (`k3s crictl rmi --prune`) or grow `scsi0`.
- **The restart-driven Ready wait always retries once or twice** (`FAILED - RETRYING … Wait until this node is Ready`) — normal, the node takes ~30–60 s to re-register.
