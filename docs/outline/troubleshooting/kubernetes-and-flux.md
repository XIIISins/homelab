<!-- docs/outline/troubleshooting/kubernetes-and-flux.md -->

# Kubernetes & Flux

Cluster-level symptoms: node lifecycle, stuck rollouts, GitOps reconciliation, and the secret-materialisation layer.

---

## "duplicate node name found" rebuilding a node

**Symptom:** K3s won't start on a rebuilt node, logging `duplicate node name found`.

**Cause:** the old node's object (and its etcd member) still exist.

**Fix:** delete the stale node from a surviving control plane *before* starting K3s on the replacement — this also evicts the stale etcd member:
```
kubectl delete node <name>
```
Full sequence in **K3s node rebuild** (Procedures).

---

## A StatefulSet rollout is stuck on a CrashLoopBackOff pod

**Symptom:** a StatefulSet update never progresses; `UpdateRevision != CurrentRevision` and the old (broken) pod is still running.

**Cause:** the StatefulSet controller waits for the existing pod to be `Ready` before replacing it. A pod stuck in CrashLoopBackOff (e.g. a failing init container) never reaches `Ready`, so the rollout deadlocks.

**Fix:** force-delete the stuck pod to let the new template spawn:
```
kubectl delete pod <name> --grace-period=0 --force
```

---

## A HelmRelease reconcile failed

**Symptom:** a HelmRelease is in a failed state and won't recover on retry.

**Fix:** suspend, roll back to the last good revision, fix the version in Git, then resume:
```
flux suspend hr <name> -n <ns>
helm rollback <name> <last-good-rev> -n <ns> --wait=false
# verify pods; kubectl delete pod to force template re-render if needed
# fix spec.chart.spec.version in Git
flux resume hr <name> -n <ns>
flux reconcile hr <name> -n <ns>
```
**Watch out:** Helm rollback uses the *old* release values verbatim, dropping any new-values fix — which can re-deadlock on retry. If that happens, keep the HR suspended, patch the live Deployment to match Git, orphan the old ReplicaSet, then resume.

> For first deploys, set `install.remediation.retries: -1` so a failure doesn't trigger `helm uninstall` (which obscures the original problem). Restore `3` after success.

---

## An ExternalSecret isn't materialising

**Symptom:** a consumer is stuck because its K8s Secret never appears; the ExternalSecret shows `SecretSyncedError`.

**Cause:** ExternalSecret `data` refs are all-or-nothing — a single missing Vault path blocks the *entire* target Secret. Or the value exists but hasn't synced yet (default `refreshInterval: 1h`).

**Diagnose:** `kubectl describe externalsecret <name> -n <ns>` — a `Secret does not exist` on one ref blocks all of them.

**Fix:** ensure every `remoteRef` resolves; if consumers have different readiness windows against the same Vault tree, split into multiple ExternalSecrets by resolve-time. To force an immediate sync of an existing value:
```
kubectl annotate -n <ns> externalsecret <name> force-sync=$(date +%s) --overwrite
```
Then `kubectl rollout restart` the consumer — Secret-as-env is snapshotted at pod start, so the pod needs to restart to see new values.

---

## A Job edit is rejected as immutable

**Symptom:** Flux fails a Kustomization with `Job.batch "X" is invalid: spec.template: ... field is immutable`.

**Cause:** Jobs are immutable — you changed the script, image, or env of an already-created Job.

**Fix:** delete the Job, then reconcile so Flux recreates it from Git:
```
kubectl delete job/<name> -n <ns>
flux reconcile kustomization <ks>
```

---

## An override "isn't taking effect"

**Symptom:** a values override (e.g. a rollout strategy) seems ignored.

**Cause:** YAML key casing is silently dropped when unknown to the schema — `rollingupdate` instead of `rollingUpdate`, for instance. Admission accepts-then-ignores it.

**Diagnose:** `kubectl get <resource> -o yaml` and grep for the actual key. If it's missing, it's a casing/spelling problem, not logic.

## See also

- **GitOps & automation** (Components) — how Flux reconciles and the per-component-config layering.
- **K3s node rebuild** / **Teardown & rebuild** (Procedures) — the deliberate node and cluster operations.
- **Vault & Postgres** (this section) — for ExternalSecret failures rooted in Vault itself.
