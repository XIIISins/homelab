<!-- docs/known-issues/k8s-scheduling.md -->

# Known gotchas — K8s scheduling

*Migrated from `CLAUDE.md`. Recovery commands + rules. Incident retros in [`../incidents/`](../incidents/).*

## K8s scheduling

- **Required pod anti-affinity + RollingUpdate without `maxSurge: 0` deadlocks on N replicas across N nodes.** Default `maxSurge: 25%` rounds up to 1; cluster has no 4th slot → rollout deadlocks. Fix: explicit `strategy.rollingUpdate.maxSurge: 0, maxUnavailable: 1`. Briefly runs at N-1/N during rolls.
- **YAML key casing is silently dropped** when unknown to the schema (e.g. `rollingupdate` vs `rollingUpdate`). Default permissive admission accepts-then-ignores. Debug "my override isn't taking" with `kubectl get ... -o yaml` and grep for the actual key — if missing, it's a casing/spelling issue, not logic.
- **StatefulSet `RollingUpdate` won't replace a CrashLoopBackOff pod.** The controller waits for the existing pod to be `Ready` before deleting it for the new revision (avoids cascade-deletion thrashing); a permanently-failing init container never reaches Ready, so the rollout stalls indefinitely with `UpdateRevision != CurrentRevision` and the old (broken) pod still running. **Fix:** `kubectl delete pod <name> --grace-period=0 --force` to break the deadlock; the new pod spawns with the updated template. Affects StatefulSets specifically — Deployment's RollingUpdate has different semantics (new RS creates fresh pods independently). Common after first-deploy fixes where the broken pod is on the old template. Surfaced 2026-05-25 Phase 5g.
- **`runAsNonRoot: true` fails admission against images whose USER directive is a name (not a numeric UID).** `Error: container has runAsNonRoot and image has non-numeric user (nodejs), cannot verify user is non-root`. K8s can't introspect the image's user db to confirm the named user is non-root, so it refuses to start. **Fix**: set `runAsUser: <numeric>` explicitly alongside `runAsNonRoot: true` — admission then verifies the UID directly. Common offenders: `outlinewiki/outline` (USER nodejs = 1001), `library/postgres` (USER postgres), `bitnami/*` (USER 1001 by name in older images). For ANY new image where the upstream USER might be name-based, set numeric UID even if you don't need it — costs nothing, future-proofs against the admission failure. Surfaced 2026-05-26 Phase 5j (Outline first-deploy).

