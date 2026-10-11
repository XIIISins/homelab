<!-- docs/outline/troubleshooting/kubernetes-and-flux.md -->

# Kubernetes & Flux

Symptoms with nodes, pods, rollouts, Helm releases, Secrets and Jobs. Cluster: asgard, three control planes (Göndul, Hlökk, Sigrún; tainted, no workloads) and three workers (Einherjar-urd/-verd/-skuld). Everything in the cluster is applied by Flux from Git: **fix Git, not the cluster.**

```bash
SSH="ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE"
```

---

## 1. First diagnostics (read-only)

```bash
kubectl get nodes -o wide
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded | head -30
kubectl get events -A --sort-by=.lastTimestamp | tail -30
flux get sources git; flux get kustomizations; flux get helmreleases -A | grep -v " True "
kubectl get externalsecrets -A | grep -v SecretSynced
kubectl describe node <node> | sed -n '/Conditions:/,/Addresses:/p'
kubectl describe node <node> | grep -A8 "Allocated resources"           # CPU/memory requested vs allocatable
```

Check `kubectl get nodes` **before** blaming a chart: a node outage makes Helm waits on DaemonSets and StatefulSets time out, and it shows up as a "failed" release.

---

## 2. A node is NotReady

```bash
kubectl describe node <node> | grep -E "Ready|KubeletNotReady|DiskPressure|MemoryPressure|NetworkUnavailable"
$SSH ansible@<node-ip> 'sudo systemctl is-active k3s k3s-agent 2>&1; sudo journalctl -u k3s -u k3s-agent -n 60 --no-pager | tail -40'
$SSH ansible@<node-ip> 'df -h / /var; free -m; uptime'
```

- **VM unreachable:** the guest or its Proxmox host is down → **Hosts & Proxmox**. A frozen Skuld takes Sigrún and Einherjar-skuld with it (and one member of every trio); that is tolerated, not an emergency, so long as the other two nodes of each quorum are healthy.
- **DiskPressure:** drained pods pulled images onto the small OS disk. Wait about 5 minutes after the drain, then clear (see **Storage & iSCSI** §11).
- **Control plane disk filling with `dropped internal Raft message`:** a peer is down; an rsyslog rule drops that line; reclaim old `/var/log/messages-*`.
- **Duplicate node name** when a rebuilt control plane starts: `kubectl delete node <name>` from a surviving control plane **first** (**K3s node rebuild**).

## 3. Pods Pending

```bash
kubectl -n <ns> describe pod <pod> | sed -n '/Events:/,$p'
```

| Event | Cause | Action |
|---|---|---|
| `0/6 nodes available: 3 Insufficient cpu` | Workers' CPU requests are nearly full (control planes are tainted) | If a surge pod is Pending during a rollout and the old pod is the one blocking it: confirm the new pod is the pending one, then `kubectl -n <ns> delete pod <old-pod> --wait=false` to free its request. Outage accepted. Check headroom before bumps: `kubectl describe node <w> \| grep -A5 "Allocated resources"` |
| `didn't match pod anti-affinity rules` on a rollout | Required anti-affinity with N replicas on N nodes plus a surge pod | The Deployment needs `strategy.rollingUpdate.maxSurge: 0, maxUnavailable: 1`. For Traefik it is the chart's **root** `updateStrategy`; a `deployment.strategy` key is silently ignored. Verify with `kubectl get deploy traefik -n traefik -o jsonpath='{.spec.strategy}'` |
| `volume node affinity conflict` | A `local-path` volume pinned to a worker that is down/cordoned | **Storage & iSCSI** §9 |
| `persistentvolumeclaim … not found` / Pending claim | StorageClass missing: there is no default class | The PVC must name its tier |
| Vault pod Pending after a drain | Required anti-affinity, 3 replicas, 3 workers | Expected while a worker is out (2 of 3 voters) |

A StatefulSet will not replace a CrashLoopBackOff pod on a new template (it waits for Ready): `kubectl -n <ns> delete pod <pod> --grace-period=0 --force`. A YAML key with the wrong casing (`rollingupdate`) is silently dropped by the API: if "my override isn't taking", `kubectl get … -o yaml | grep -i <key>`.

## 4. Pods CrashLoopBackOff / ImagePullBackOff / CreateContainerConfigError

```bash
kubectl -n <ns> logs <pod> --previous --tail=60
kubectl -n <ns> describe pod <pod> | sed -n '/State:/,/Ready:/p;/Events:/,$p'
```

- `CreateContainerConfigError`: a referenced Secret/ConfigMap is missing (check `kubectl -n <ns> get externalsecret`; an ExternalSecret that has not synced leaves its Secret absent; see §6).
- `runAsNonRoot … non-numeric user`: the image's `USER` is a name; set a numeric `runAsUser` in the manifest.
- `ImagePullBackOff`: the tag does not exist or the registry rate-limits/blocks. Verify the tag before pinning; revert the bump in Git.
- OOMKilled (`Last State: Terminated, Reason: OOMKilled`): the limit is too low (SABnzbd 1 GiB, NetBox below 1.5 GiB, and others have hit it). Raise the limit in Git (rightsizing PRs exist for this); a restart only delays it.

## 5. A rollout or HelmRelease is stuck

```bash
flux get helmrelease <name> -n <ns>
kubectl -n <ns> describe helmrelease <name> | tail -25
kubectl -n <ns> rollout status deploy/<name> --timeout=60s
```

| State | Meaning | Action |
|---|---|---|
| `Stalled / RetriesExceeded` after the cause is fixed | A plain reconcile ignores an exhausted release | `flux reconcile helmrelease <name> -n <ns> --reset` (or `--force`) |
| `install retries exhausted` on first deploy | Helm uninstalled on failure and the state no longer shows the original problem | Set `install.remediation.retries: -1` for the first deploy via Git; restore `3` afterwards. Extend `spec.timeout` for slow first runs (Authentik ~15 min) |
| Release waits on a DaemonSet/StatefulSet and times out | A node is down (`csi-nfs-node` "InProgress" with pods Terminating on a NotReady node) | Fix the node; do not touch the chart |
| After a rollback, an app that migrates its own database crash-loops (`previously executed migration … is missing`) | The rollback restored an older image that refuses the newer schema | Roll **forward**: `flux reconcile helmrelease <name> -n <ns> --reset` so the desired version re-applies |
| Rollback keeps re-deadlocking | A rollback reuses the old release values and drops your new fix | `flux suspend hr <name> -n <ns>`; `kubectl rollout pause deploy/<name>`; patch the live Deployment to match Git; delete orphan ReplicaSets; `kubectl rollout resume`; `flux resume hr <name> -n <ns>` — only once live matches Git |
| Many releases `Ready=False` "does not have an artifact" right after a Flux upgrade | Source-controller rebuilt its artifact store | Wait a few minutes |
| `Job.batch … field is immutable` | A Job's pod template cannot change | `kubectl -n <ns> delete job <name>` then `flux reconcile kustomization <ks>` |
| A ConfigMap changed in Git but the pod behaves the same | A subPath mount does not update in a running pod; some apps read config only at start | `kubectl -n <ns> rollout restart deploy/<name>` |
| Kustomization fails dry-run on a CRD-dependent resource | The CRD comes from a chart in the same Kustomization | Move it into the component's `<component>-config` Kustomization (`dependsOn: infrastructure`) |
| `failed calling webhook "validate.externalsecret…"` | The External Secrets webhook fails closed while its pod restarts | Self-heals; then `flux reconcile kustomization infrastructure --with-source` |
| HelmRepository `Ready=False 404` for sealed-secrets | The upstream index moved | Update the repo URL in Git; the cached release keeps running |

Last-resort recovery of a failed Helm release: `flux suspend hr <name> -n <ns>` → `helm rollback <name> <last-good-rev> -n <ns> --wait=false` → verify pods → fix `spec.chart.spec.version` in Git → `flux resume hr <name> -n <ns>` → `flux reconcile hr <name> -n <ns>`.

## 6. A Secret is missing or has the old value (External Secrets)

```bash
kubectl -n <ns> get externalsecret
kubectl -n <ns> describe externalsecret <name> | sed -n '/Status:/,$p' | head -30     # names the missing Vault path
kubectl get clustersecretstore vault -o jsonpath='{.status.conditions[0].status}{" "}{.status.conditions[0].message}{"\n"}'     # True?
```

- **`SecretSyncedError … Secret does not exist` (one path):** one missing Vault path blocks the whole target Secret (all-or-nothing). Mint/seed the missing path (Terraform module, or `vault kv put` from a variable), then force-sync.
- **Stale value after you changed Vault:** refresh interval is one hour. Force it: `kubectl annotate -n <ns> externalsecret <name> force-sync=$(date +%s) --overwrite`, then `kubectl -n <ns> rollout restart deploy/<consumer>` (pods read env at start).
- **ClusterSecretStore not Ready:** Vault is sealed or unreachable, or the internal CA bundle is missing. Go to **Vault & Postgres**.
- A Secret that is split into two ExternalSecrets exists on purpose so one app dependency (S3, OIDC) not yet provisioned does not block the other (Outline).

## 7. Flux itself

```bash
flux check
flux get sources git                                                # flux-system: Ready, latest main revision?
flux logs --level=error --since=15m | tail -30
kubectl -n flux-system get pods
flux reconcile source git flux-system && flux reconcile kustomization flux-system --with-source
```

- `GitRepository` not Ready: the deploy key or GitHub is unreachable. The key is `[Bootstrap] - Manual - Flux - Deploy key`; reuse it, never create a new one (`flux bootstrap` otherwise orphans the old key).
- After a Flux upgrade (regenerated `gotk-components.yaml`), `kubectl get hr -A` column order is not stable across versions; read `.status.conditions[?(@.type=="Ready")]` with jsonpath instead of `awk '$3'`.
- Flux has **no** per-object metrics from its controllers in this stack; the cluster dashboard builds them from kube-state-metrics.

## 8. Quick facts that explain a lot

- Workers are close to full on CPU/memory **requests**; a surge or a drain leaves pods Pending until capacity returns. Real usage is low.
- Memory reservations: 2 GiB per worker is held for the OS and K3s. `eviction-hard` replaces the kubelet defaults, so all thresholds must always be restated together.
- A worker config change restarts the agent (`restart k3s-agent`); `KillMode=process` keeps the pods up.
- Calico and the K3s version are **not** Flux-managed: `playbooks/platform-version-drift.yml` reports drift.

---

## See also

- **GitOps & automation** (Components): the Flux layering.
- **K3s node rebuild**, **K3s & Calico upgrade**, **Everyday operations** (Procedures).
- **Vault & Postgres**, **Storage & iSCSI** (this section).
