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

## /var/log/messages floods while a CP peer is down (etcd Raft-drop spam)

- **Symptom:** a surviving control plane's OS disk fills (hlokk hit 89 % of its then-10 GB disk on 2026-09-30; CP disks are now 20 GB): `/var/log/messages` (+ weekly rotations) is hundreds of MB to 1 GB. Cause: embedded etcd logs `dropped internal Raft message since sending buffer is full (overloaded network)` for every message it can't send to an unreachable peer — ~115k lines/hour, ~2M lines per outage day (peer `10.0.21.13` = sigrun on Skuld during the 2026-09-21 and 2026-09-30 Skuld freezes). Not an ongoing bug; it scales with how long a peer is down.
- **Fix (in IaC):** `roles/k3s/tasks/logging.yml` (`--tags k3s_logging`): rsyslog rule `/etc/rsyslog.d/05-drop-etcd-raft-spam.conf` drops ONLY that line from `/var/log/messages` (the journal keeps it; `failed to reach the peer URL` / `prober detected unhealthy status` still log), plus `maxsize 200M` / `rotate 2` / `compress` on `/etc/logrotate.d/rsyslog`.
- **Reclaim by hand if a disk is already full:** delete old `/var/log/messages-YYYYMMDD` files, then `logrotate -f /etc/logrotate.d/rsyslog` (751 MB → 15 MB compressed). Check disks before any K3s hop: CP OS disks were grown 10 → 20 GB on 2026-09-30 (now ~31-34 % used).

## K3s addon files prune on removal — Calico CRDs (2026-10-01 incident)

- **K3s's addon controller applies each file in `/var/lib/rancher/k3s/server/manifests/` as its own objectset and DELETES, per file, any object that is no longer in the file.** For a CRD that means all its stored objects go too. Replacing an addon file with a newer upstream manifest is therefore only safe if the new file still carries every CRD the old one did. Calico 3.29.3's `tigera-operator.yaml` carried 24 CRDs (datastore + operator); 3.30's carries none → K3s deleted all 24, wiping IPPools / IPAMBlocks / BlockAffinities and putting the `Installation` into uninstall. Post-mortem: [2026-10-01-calico-datastore-prune.md](../incidents/2026-10-01-calico-datastore-prune.md).
- **Symptom of a Calico datastore wipe:** `kubectl get ippools.crd.projectcalico.org` → 0 (and ipamblocks/blockaffinities/ipamhandles 0), `installations.operator.tigera.io` missing, operator logs `Installation object is terminating` / `waiting for enabled IP pools`, cross-node pod traffic and anything behind it fails while nodes stay Ready and same-node traffic works.
- **A terminating namespace can deadlock the CNI rebuild:** `calico-system` stuck `Terminating` with `NamespaceDeletionDiscoveryFailure: metrics.k8s.io/v1beta1` (the metrics API can't be reached while the pod network is down). The namespace is empty; finalize it via `/api/v1/namespaces/<ns>/finalize` (`kubectl get ns X -o json | yq -p json -o json '.spec.finalizers = []' | kubectl replace --raw /api/v1/namespaces/X/finalize -f -`) and the operator recreates it.
- **After the Calico rebuild every pod must be recreated** (their IPs are no longer in IPAM): staged restart of non-host-network pods, namespace by namespace, Vault one pod at a time.
- **Calico on this cluster is operator-managed via addon files on the init node** (`tigera-operator.yaml`, `tigera-operator-crds.yaml` from 3.30+, `calico-installation.yaml`), laid down at install by `tasks/calico.yml` (first install only) and moved only by `playbooks/calico-upgrade.yml` (datastore export + per-file CRD-prune guard). Do NOT replace those files by hand. `playbooks/platform-version-drift.yml` reports drift between `calico_version`/`k3s_version` in git and what runs.
- **K3s only re-applies an addon when the file CHECKSUM changes** — to force a re-apply of an unchanged manifest, append a comment line.
- **External Secrets 2.x validating webhook is `failurePolicy: Fail`:** while its pod is being recreated (worker drains, pod restarts) Flux dry-runs of ExternalSecret/ClusterSecretStore fail (`failed calling webhook "validate.externalsecret…"`), turning `infrastructure`/`apps` Kustomizations `False` until the pod is back. Self-heals; `flux reconcile kustomization infrastructure --with-source` after.

