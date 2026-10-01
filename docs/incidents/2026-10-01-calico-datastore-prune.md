<!-- docs/incidents/2026-10-01-calico-datastore-prune.md -->

# 2026-10-01 — Calico upgrade wiped the CNI datastore (cluster pod networking down ~35 min)

**Impact:** during the Calico 3.29.3 → 3.30.7 hop, K3s pruned Calico's CRDs and with them every IPPool / IPAMBlock / BlockAffinity / IPAMHandle. Cross-node pod networking broke cluster-wide: wiki, `metric.`, NetBox, Authentik (503/500/timeouts) were down for roughly 35 minutes; the smoketest backend (same node as Traefik) and the Vault UI kept answering. Nodes stayed Ready. No data loss in applications; Vault stayed unsealed throughout. Recovery needed a Calico rebuild plus a restart of every pod.

**Caused by me** (the upgrade was run by the assistant): the method was wrong, not the target version.

## What happened

1. `calico-upgrade.yml` (new that day) replaced `/var/lib/rancher/k3s/server/manifests/tigera-operator.yaml` on the init node with the 3.30.7 manifest — the procedure written into `docs/procedures/k3s-upgrade.md` and the role comments.
2. **K3s's addon controller prunes, per addon file, every object that disappears from the file it was applied from.** The 3.29.3 `tigera-operator.yaml` carried **24 CRDs** (incl. `installations.operator.tigera.io` and the datastore CRDs); the 3.30 one carries **none** (from 3.30 the operator manages its own CRDs, shipped separately as `operator-crds.yaml`). K3s deleted all 24 CRDs → every stored object went with them.
3. The `Installation` CR (finalizers held by the operator) went to `Terminating`; the operator entered its uninstall flow and the `calico-system` namespace started terminating; IPAM state was gone, so cross-node routing for existing pod IPs broke.

## Recovery (all via the repo's mechanisms except the two steps that needed explicit approval)

1. Restored `operator-crds.yaml` (as its own addon file `tigera-operator-crds.yaml`) and re-rendered `calico-installation.yaml` into the addon dir on gondul.
2. **Approved by the owner:** removed the stuck `Installation`'s finalizers (`kubectl patch … finalizers: []`); touched `calico-installation.yaml` (new checksum) so K3s re-applied the Installation + APIServer CRs.
3. The operator could not recreate objects because `calico-system` was **Terminating** — stuck on `NamespaceDeletionDiscoveryFailure` (`metrics.k8s.io/v1beta1` unreachable because the pod network was down): a deadlock. **Approved by the owner:** finalized the empty namespace via `/api/v1/namespaces/calico-system/finalize`. The operator immediately recreated everything; Calico came up 3.30.7 with a fresh pool.
4. Existing pod IPs were no longer in IPAM → recreated every non-host-network pod, 21 namespaces in dependency order (kube-system → flux → cert-manager → ESO → metallb → traefik → … ), each waited Ready; Vault last, one pod at a time (standbys first), 3/3 unsealed throughout.
5. Flux Kustomizations that failed on the External Secrets webhook (`failurePolicy: Fail`, pod being recreated) recovered on a nudge.

## Findings

1. **K3s addon files are prune-on-removal, per file.** Replacing an addon file with one that no longer contains a CRD deletes the CRD and all its objects. See [k3s-lifecycle.md](../known-issues/k3s-lifecycle.md).
2. **The docs and the cluster disagreed:** CLAUDE.md said Calico is "applied by `calico.yml`… `kubectl edit` gets reverted by the addon controller", but no Calico manifest existed on any CP (only the K3s `Addon` records remained). I should have stopped at that discrepancy instead of treating the file-swap as equivalent to the install.
3. **My first guard idea (compare the union of CRDs) would not have caught this** — K3s prunes per file. The per-file comparison does (verified offline against the real manifests: 3.29.3→3.30.7 drops 24 from `tigera-operator.yaml`).
4. **A datastore backup would have turned this into a restore:** nothing exported the `crd.projectcalico.org` objects first.
5. The permission classifier blocked three recovery steps (finalizer patch, namespace finalize, earlier pod/cert/K3s actions) until the owner approved each — correct behaviour, but it lengthened the outage; incident runbooks should list these steps up front.

## Changes

- `ansible/playbooks/calico-upgrade.yml` rewritten: git-driven target (`calico_version`), one-minor guard, **datastore export first**, **per-addon-file CRD-prune guard** (`calico_allow_crd_removal` for deliberate removals), `operator-crds.yaml` applied as its own addon *before* the operator, post-checks that CRD and IPPool/IPAMBlock/BlockAffinity counts did not shrink.
- `ansible/roles/k3s/tasks/calico.yml`: a fresh install of ≥ 3.30 also lays down `operator-crds.yaml`.
- `ansible/playbooks/platform-version-drift.yml` (read-only): K3s per node + Calico version + live pool CIDR vs git.
- Role defaults updated to what runs: `k3s_version v1.36.4+k3s1`, `calico_version v3.30.7`.
- Docs: [k3s-lifecycle.md](../known-issues/k3s-lifecycle.md), [k3s-upgrade.md](../procedures/k3s-upgrade.md), CLAUDE.md invariant, the `chart-bump` agent guardrails.

## Follow-ups

- 3.31.7 passes the guard; **3.32.2 needs `calico_allow_crd_removal`** for `adminnetworkpolicies.policy.networking.k8s.io` and `baselineadminnetworkpolicies.policy.networking.k8s.io` (no such objects exist in the cluster today — re-verify before allowing).
- Wire `platform-version-drift.yml` into the Semaphore drift job.
- Consider an etcd/PBS-independent periodic export of the Calico datastore.
