<!-- docs/outline/troubleshooting/kubernetes-and-flux.md -->

# Kubernetes & Flux

Symptoms with nodes, rollouts, Helm releases, Secrets and Jobs.

---

## First diagnostics

```
kubectl get nodes ; kubectl get pods -A | grep -v Running
flux get kustomizations ; flux get helmreleases -A
kubectl get externalsecrets -A
```

Check `kubectl get nodes` before blaming a chart: a node outage makes Helm waits on DaemonSets and StatefulSets time out.

## Nodes and rollouts

| Symptom | Likely cause | What to do |
|---|---|---|
| "Duplicate node name" when a control plane rebuilds | The old node object is still registered | `kubectl delete node <name>` from a surviving control plane first. |
| Rebuilding the default init node starts a new cluster | The role defaults to `--cluster-init` | Override the init node with a healthy control plane. |
| A taint did not appear or pods did not move | `node-taint` applies at registration only, and `NoSchedule` never evicts running pods | Taint existing nodes by hand; delete pods that must move. |
| A node has `disk-pressure` after an upgrade hop | Drained pods pulled images onto the 30 GB OS disk | Self-heals in about five minutes; clean up evicted pods afterwards. |
| A Traefik rollout is stuck Pending | The default update strategy surges a fourth pod that cannot schedule (anti-affinity) | The strategy must be set at the chart's root `updateStrategy` with `maxSurge: 0`. |
| A Vault pod is Pending after a drain | Required anti-affinity: three replicas, three workers | Expected while a worker is out; Vault runs on 2 of 3. |
| Pods Pending after a drain, CPU requests too high | Workers are heavily requested | A drain is an accepted temporary outage; uncordon when done. |
| A control plane's disk fills with logs while a peer is down | etcd logs every dropped Raft message | An rsyslog rule drops that line; reclaim old logs by hand. |
| A K3s config change restarted every node at once | Ansible runs in parallel by default | Stage with `--limit` on one node first. |

## Helm and Flux

| Symptom | Likely cause | What to do |
|---|---|---|
| A HelmRelease stays failed after the cause was fixed | Retries are exhausted and a plain reconcile does nothing | `flux reconcile helmrelease <name> -n <ns> --reset` (or `--force`). |
| An app that migrates its own database cannot start after a rollback | The rollback restored an older image that refuses the newer schema | Roll **forward**: reset so the desired chart version re-applies. |
| Helm rolled back and re-deadlocked | A rollback uses the old release values, dropping the new fix | Suspend the release, patch the live Deployment to match Git, then resume. |
| After a Flux upgrade most releases show "does not have an artifact" | Source-controller rebuilt its artifact store | Wait a few minutes. |
| A Kustomization fails dry-run on a CRD resource | The CRD is installed by a chart in the same Kustomization | The resource belongs in the component's own `-config` Kustomization. |
| A Job fails with "field is immutable" | Job templates cannot change | `kubectl delete job/<name>` and reconcile. |
| A ConfigMap changed but the app did not | A subPath mount does not auto-update | `rollout restart`. |
| A SealedSecret will not decrypt after a rebuild | The controller generated a new keypair | Restore the master key from 1Password before the controller starts, or re-seal. |
| The sealed-secrets HelmRepository is `Ready=False` | The upstream index URL moved | Update the URL; the cached release keeps working meanwhile. |

## Secrets from Vault

| Symptom | Likely cause | What to do |
|---|---|---|
| An ExternalSecret is stuck `SecretSyncedError` | A single missing Vault path blocks the whole target Secret | Split into separate ExternalSecrets by when each path resolves, or mint the missing path. |
| A new Vault value has not reached the pod | Refresh interval is an hour | Annotate the ExternalSecret with `force-sync=<timestamp>`, then restart the consumer. |
| Flux dry-runs of ExternalSecrets fail during a restart | The ESO validating webhook fails closed while its pod is recreated | Self-heals; reconcile with source afterwards. |

## Hard rules

- No manual `kubectl apply`; change Git.
- Concrete version pins everywhere; floating ranges have caused breaking patch releases.
- Verify the rendered object after a Helm change, not just the release status.

---

## See also

- **GitOps & automation** (Components): the Flux layering.
- **K3s node rebuild** and **K3s & Calico upgrade** (Procedures).
- **Vault & Postgres** (this section).
