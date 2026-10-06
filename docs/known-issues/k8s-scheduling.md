<!-- docs/known-issues/k8s-scheduling.md -->

# Known gotchas — K8s scheduling

*Migrated from `CLAUDE.md`. Recovery commands + rules. Incident retros in [`../incidents/`](../incidents/).*

## K8s scheduling

- **Required pod anti-affinity + RollingUpdate without `maxSurge: 0` deadlocks on N replicas across N nodes.** Default `maxSurge: 25%` rounds up to 1; cluster has no 4th slot → rollout deadlocks. Fix: explicit `strategy.rollingUpdate.maxSurge: 0, maxUnavailable: 1`. Briefly runs at N-1/N during rolls.
- **YAML key casing is silently dropped** when unknown to the schema (e.g. `rollingupdate` vs `rollingUpdate`). Default permissive admission accepts-then-ignores. Debug "my override isn't taking" with `kubectl get ... -o yaml` and grep for the actual key — if missing, it's a casing/spelling issue, not logic.
- **StatefulSet `RollingUpdate` won't replace a CrashLoopBackOff pod.** The controller waits for the existing pod to be `Ready` before deleting it for the new revision (avoids cascade-deletion thrashing); a permanently-failing init container never reaches Ready, so the rollout stalls indefinitely with `UpdateRevision != CurrentRevision` and the old (broken) pod still running. **Fix:** `kubectl delete pod <name> --grace-period=0 --force` to break the deadlock; the new pod spawns with the updated template. Affects StatefulSets specifically — Deployment's RollingUpdate has different semantics (new RS creates fresh pods independently). Common after first-deploy fixes where the broken pod is on the old template. Surfaced 2026-05-25 Phase 5g.
- **`runAsNonRoot: true` fails admission against images whose USER directive is a name (not a numeric UID).** `Error: container has runAsNonRoot and image has non-numeric user (nodejs), cannot verify user is non-root`. K8s can't introspect the image's user db to confirm the named user is non-root, so it refuses to start. **Fix**: set `runAsUser: <numeric>` explicitly alongside `runAsNonRoot: true` — admission then verifies the UID directly. Common offenders: `outlinewiki/outline` (USER nodejs = 1001), `library/postgres` (USER postgres), `bitnami/*` (USER 1001 by name in older images). For ANY new image where the upstream USER might be name-based, set numeric UID even if you don't need it — costs nothing, future-proofs against the admission failure. Surfaced 2026-05-26 Phase 5j (Outline first-deploy).


## Worker CPU *requests* are nearly full — surge rollouts of ≥ ~300m pods deadlock

- **Workers have 2 vCPU each and sit at 84–90 % CPU requests (measured 2026-09-30) while actual usage is ~5–10 %.** A Deployment rolling update that surges a new pod (NetBox web 500m, worker, etc.) hits `0/6 nodes are available: 3 Insufficient cpu` (CPs are tainted) and the Helm upgrade waits until its timeout.
- **Recovery (outage accepted):** delete the OLD pod (`kubectl -n <ns> delete pod <old> --wait=false`) — frees its request so the pending surge pod schedules. Do it only after confirming the new pod is the one that's Pending.
- **Check before any bump that surges:** `kubectl describe node <worker> | grep -A5 "Allocated resources"`. Long-term fix is trimming over-requested `resources.requests.cpu` (real usage is far lower) or adding worker CPU. Measured, reviewed trimming is Phase 10i ([`10i-rightsizing.md`](../operations/10i-rightsizing.md): VPA recommend-only + rightsizing PRs). **Since 2026-10-05 (10i0b) the workers sit at 37–44 % CPU requested**, and requests match 30 days of real use.

## Memory requests are the scheduler's only view of memory — keep them honest

- **Until 2026-10-05 most workloads requested less memory than they used, and 25 containers requested none** (Immich up to 5.2 GiB). The scheduler places pods by requests only, so after a node loss it could pack the survivors past their real memory and the kernel would evict or OOM-kill, BestEffort pods first. Phase 10i set requests from 30 days of VictoriaMetrics history and reserved 2 GiB per worker for the OS and K3s ([`10i-rightsizing.md`](../operations/10i-rightsizing.md), [`k3s-lifecycle.md`](k3s-lifecycle.md)). Calico and the Tigera operator still have none, by choice.
- **Rule:** a new workload ships with CPU and memory requests (and a VPA object in `vpa-config/`). Check the cluster-wide picture with `kubectl describe node <worker> | sed -n '/Allocated resources/,/Events/p'` against `kubectl top nodes`.

