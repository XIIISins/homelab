<!-- docs/operations/10i-rightsizing.md -->

# Phase 10i — Pod rightsizing with VPA (recommend-only) and Gná suggestions: plan

*Drafted 2026-10-04, restarted 2026-10-05 on the operator's brief: "use VPA to make the worker VMs reduce their memory usage; the VMs stay at their allocated resources in Proxmox; optimise the pods over time so Gná gives weekly, bi-weekly or monthly suggestions and optimisations". Status: 🟡 10i0, 10i0b and 10i1 live 2026-10-05; 10i2 onward not built (10i2 waits for about 7 days of VPA history). Parent: [`aiops-roadmap.md`](aiops-roadmap.md) §10i. Builds on the 10d Toolbelt (read-only tools, audit log), the 10h1 forecast cards and the 10h2 PR author (change requests, dispatcher, the `k8s` class and its burst-cluster test, [`10h-k8s-burst-test.md`](10h-k8s-burst-test.md)). Motivating gotcha: [`known-issues/k8s-scheduling.md`](../known-issues/k8s-scheduling.md).*

---

## Goal and boundary

**Goal: the pods on the asgard workers (Einherjar-urd/verd/skuld) use and reserve less memory, improved steadily over time.** The worker VMs keep their 16 GiB / 2 vCPU in Proxmox; nothing in this plan resizes a VM or LXC. CPU requests (84–90 % requested, ~5–10 % used; NetBox requests 500m and uses about 1m) are trimmed by the same loop as a side benefit, because the data is the same.

What "less memory" means, and what VPA can and cannot do about it:

| Lever | What it changes on the worker | Who proposes it |
|---|---|---|
| **Memory requests** down to what the pod really needs | memory *reserved* by the scheduler; frees room for surges and rescheduling after a node loss | VPA recommendation + Toolbelt check |
| **Memory limits** down to a safe margin over the real peak | the most a pod is *allowed* to grow to; stops slow leaks and unbounded caches from eating the VM | Toolbelt (VPA upperBound + 30-day peak) |
| **App tuning** (worker/process counts, cache sizes, `GOMEMLIMIT`, JVM heap, replica counts on a 3-node cluster) | memory actually *used* | Gná, from usage trends and the chart's values; a suggestion, not an automatic PR |

VPA only sees containers; it lowers the first two. Actual usage only falls with the third, which is why Gná's suggestions cover tuning, not just numbers.

What stays true:

- **VPA never touches a pod.** Recommender only: no updater, no admission webhook, every VPA object `updateMode: "Off"`. Kubernetes 1.36 in-place resize and VPA's `InPlaceOrRecreate` are not used.
- **Every change is a reviewed PR that a human merges.** Merging `k8s/` is the deploy (Flux). Gná suggests; the operator decides which suggestions become PRs.
- **No new authority for agents.** The Toolbelt gains read verbs on two VPA resources; the registry, autonomy, rebuild scope and executor are untouched.
- **Memory is handled with care.** CPU is compressible (too low means slower); memory is not (too low means OOMKilled). Memory cuts use the 30-day **max** and VPA's **upperBound**, keep a margin, and never apply to a workload that was OOMKilled in the window.

## Pre-flight: what this depends on and touches

| Finding | Effect on 10i |
|---|---|
| Workers are at 84–90 % CPU requested ([`k8s-scheduling.md`](../known-issues/k8s-scheduling.md)) | The recommender pod needs a little room. **10i0** trims NetBox and `authentik-server` CPU by hand first. |
| K3s ships `metrics-server` (the role disables only `traefik`, `servicelb`, `local-storage`) | VPA's live source exists. Confirm `kubectl top pods -A` before 10i1. |
| vmagent scrapes cAdvisor and kube-state-metrics; VictoriaMetrics keeps 1 month | 30 days of per-container usage is available today for the Toolbelt's cross-check and the trend lines in each digest. |
| kube-state-metrics is v2.20; KSM dropped its built-in VPA collector in 2.9 | Recommendation history in VictoriaMetrics needs a KSM `customResourceState` config + RBAC rule (10i1). |
| CRD-dependent resources need their own Kustomization ([`flux-helm-kustomize.md`](../known-issues/flux-helm-kustomize.md)) | VPA objects go in a new **`vpa-config/`** Kustomization, `dependsOn: infrastructure`; CLAUDE.md's `<component>-config` list gets the entry at build time. |
| `aiops-readonly` ClusterRole has no `autoscaling.k8s.io` rule | Add get/list/watch on `verticalpodautoscalers` and `verticalpodautoscalercheckpoints` (no secrets, no configmaps). |
| Some charts set requests through presets (NetBox worker: `resourcesPreset: medium`) | A PR may replace a preset with an explicit block (`resourcesPreset: "none"` + `resources:`). |
| The 10h2 `k8s` author class exists and is burst-tested (one app under `k8s/asgard/apps/`) | Rightsizing PRs use a narrower `rightsizing` class that inherits the burst test: it proves the pod still **starts and becomes Ready inside the new limit**, which is the failure a too-low memory limit causes. |

## Sequence

| Step | What | Depends on |
|---|---|---|
| **10i0** | Hand-trim the obvious over-requests (NetBox, `authentik-server`) in a normal PR. Done with live numbers from 2026-10-05: NetBox web CPU request 500m → 100m and memory raised (it ran at 1521Mi of a 1536Mi limit: request 1792Mi, limit 2560Mi); NetBox worker 500m/1Gi → 50m/384Mi requests, limits unchanged; `authentik-server` 200m → 50m CPU per replica. Frees about 1.3 CPU of requests across the workers | nothing |
| **10i0b** | Initial tuning from 30 days of VictoriaMetrics history, by hand in a normal PR (2026-10-05, [below](#10i0b--initial-tuning-from-victoriametrics-history)) | 10i0 |
| **10i1** | VPA recommender + `vpa-config/` + KSM VPA metrics | 10i0 |
| **10i2** | Toolbelt `kube.rightsizing` tool + the findings pass (shadow for 7 days) | 10i1 + 7 days of samples |
| **10i3** | Gná's periodic rightsizing digest (weekly / bi-weekly / monthly) | 10i2 |
| **10i4** | `rightsizing` author class + resources-only check, from a digest button | 10i3, 10i5 |
| **10i5** | Post-merge watch (72 h) | 10i2 |

10i5 is built before 10i4 is enabled: no agent PR merges without a watch behind it.

---

## 10i0b — Initial tuning from VictoriaMetrics history

Done by hand on 2026-10-05, the day VPA started, because VictoriaMetrics already held 30 days of cAdvisor data. The rules are the 10i2 table's, with one change for CPU: short-lived pods (rollouts, restarts) inflate a raw 30-day p99, so the CPU basis is the **median of the daily p95** (the worst day's p95 is kept as a floor).

- **CPU request** = `ceil10m(max(1.5 × median daily p95, worst-day p95))`, floor 10m; only applied where the request was ≥ 3 × that.
- **Memory under-request** (30-day max > request) = `ceil16Mi(1.2 × max)`; the limit goes to ≥ 1.5 × the new request where max was > 80 % of it.
- **Memory over-request** = `ceil16Mi(1.3 × max)` where the request was ≥ 1.5 × max, floor 32Mi; limits are not cut.

What it found: CPU is over-requested almost everywhere (cloudflared, Vault, VictoriaLogs/Metrics, vlagent, Garage, the Redis pods), but **memory was under-requested for most workloads**, and three ran close to their limit (apex-static and startpage Caddy at 63 of 64 Mi, `authentik-server` at 984Mi of 1Gi, Vault at 429 of 512Mi). Those three got limit raises; the rest are request changes only. Net: about 1.6 CPU of requests freed across the workers, about 6 GiB more memory requested (requests now match real use, so the scheduler stops overbooking).

Left out on purpose: the NetBox pods (trimmed in 10i0 the same day); the VPA recommender (no history yet); Flux controllers (`gotk-components.yaml`, needs a patch in `flux-system/kustomization.yaml`); K3s addons (CoreDNS, metrics-server); Calico/Tigera (not Flux-managed); `csi-driver-nfs` (small numbers). **Workloads with no requests at all** (Immich server, up to 5.2Gi, and machine-learning, up to 1.5Gi; External Secrets, MetalLB, Sealed Secrets, Synology CSI, local-path) are a decision, not a trim: adding a request makes the scheduler count them.

**Follow-up (same day, operator's call): requests for the workloads that had none.** Same CPU rule; memory = 1.2 × the 30-day max, except the two Immich pods whose peaks sit far above their usual level (server p95 3970Mi / max 5167Mi, machine-learning p95 608Mi / max 1533Mi on model loads): those get 1.2 × p95, so the peaks run above the request instead of booking a third of a worker. No limits added. Covered: Immich (server, machine-learning, valkey), External Secrets (3), MetalLB (controller, speaker), Sealed Secrets, local-path-provisioner, the cert-manager webhook and cainjector, and Synology CSI (controller and node, through a Flux `postRenderers` patch because the chart has no resources values). About +8 GiB of worker memory requests (to about 57 % of allocatable; about 85 % on two workers after a node loss). Still without requests: Calico and the Tigera operator, which are K3s addon files moved only by `calico-upgrade.yml`. **Operator's decision (2026-10-05): leave Calico as is**; it is the cluster's core network and has had no resource problems.

**Worker memory reservation (#193, same day).** Honest requests exposed a gap: allocatable equalled capacity on every worker, so the scheduler could book the 1.5–2 GiB the OS, K3s and Calico use. The workers now set `system-reserved=memory=1Gi`, `kube-reserved=memory=512Mi` and `eviction-hard=memory.available<500Mi` (disk thresholds restated) through `k3s_worker_kubelet_args` in `group_vars/k3s_worker.yml`; allocatable dropped from 16111084Ki to 14026220Ki per worker. The same PR fixed the worker config template, which notified a CP-only handler and so never restarted the agent ([`known-issues/k3s-lifecycle.md`](../known-issues/k3s-lifecycle.md)).

**Rolled out and checked 2026-10-05:**

- **Per worker, against the new allocatable (before #194):** urd 37 % CPU / 32 % memory, verd 42 % / 43 %, skuld 44 % / 30 %. Before 10i0 the workers sat at 73–83 % CPU requested.
- **After #194:** about 57 % of worker memory is requested, and about 85 % on two workers after a node loss.
- **Vault** (StatefulSet `updateStrategy: OnDelete`) was rolled by hand, one pod at a time with the leader last. All three came back unsealed and in the Raft on the new 528Mi request / 800Mi limit, with no ExternalSecret errors. Vault cannot step down from Frigg (the AppRole policy denies `step-down`); deleting the leader hands leadership over cleanly.
- **No fallout:** no OOM kills, evictions or FailedScheduling events.

---

## 10i1 — VPA in recommend-only mode

- **`k8s/asgard/infrastructure/vpa/`**: HelmRelease for the Fairwinds `vpa` chart (`https://charts.fairwinds.com/stable`), which installs the CRDs and lets each component be switched off:
  - `recommender.enabled: true`, `updater.enabled: false`, `admissionController.enabled: false`;
  - recommender requests about 20m CPU / 128Mi, no CPU limit;
  - flags `--pod-recommendation-min-cpu-millicores=10` and `--pod-recommendation-min-memory-mb=32`. **The defaults (25m / 250 MiB) would floor every small pod at 250 MiB**, which is the opposite of the goal;
  - history from VPA's own checkpoints (`VerticalPodAutoscalerCheckpoint`, survives a restart). VPA's default memory histogram half-life is 24 h with an 8-day window, so its target follows recent use; the Toolbelt adds the 30-day peak on top (10i2).
  - versions pinned at build time by the [`chart-bump`](../../.claude/agents/chart-bump.md) agent (images verified, VPA's supported Kubernetes range checked against K3s 1.36).
- **`k8s/asgard/vpa-config/`**: one hand-written `VerticalPodAutoscaler` per controller, `updateMode: "Off"`:

  ```yaml
  apiVersion: autoscaling.k8s.io/v1
  kind: VerticalPodAutoscaler
  metadata: {name: netbox, namespace: netbox}
  spec:
    targetRef: {apiVersion: apps/v1, kind: Deployment, name: netbox}
    updatePolicy: {updateMode: "Off"}
  ```

  Every Deployment, StatefulSet and DaemonSet in Flux-managed namespaces, T3 ones included (a recommendation costs nothing). Not Jobs/CronJobs, not Calico/Tigera (not Flux-managed). The digest lists controllers without a VPA, so a new app does not drop out silently.
- **kube-state-metrics** `customResourceState` exposing `lowerBound/target/upperBound/uncappedTarget` per container for CPU and memory; vmagent already scrapes KSM.

**Acceptance:** every covered controller shows `RecommendationProvided=True` within 24 h; no updater/admission pods and no VPA `MutatingWebhookConfiguration` exist; VPA series queryable in vmui; a recommender restart keeps recommendations.

---

## 10i2 — Toolbelt data and findings (T0)

A new read tool, **`kube.rightsizing`** (namespace, kind, name; or a whole-cluster summary), returns per container:

| Field | Source |
|---|---|
| requests and limits | controller pod template (existing RBAC) |
| VPA lowerBound / target / upperBound, sample age | VPA status + checkpoint (new RBAC rule) |
| recommendation stability | KSM VPA series, last 7 days |
| memory working set p50 / p95 / max and CPU p95 / p99 over 30 days | cAdvisor in VictoriaMetrics |
| OOMKilled and restarts, 30 days | pod `lastState` + events |
| per worker: memory requested, memory limits, memory used | KSM + node metrics |

Gná gets the tool too, so a chat question ("why is authentik using 1 GiB?") or a diagnosis of `Insufficient cpu`/`memory` can cite requests vs use vs VPA.

**The findings pass** runs daily inside the existing forecast job on Frigg and stores rows in the `forecasts` table (`kind = rightsizing`, fingerprint `rightsizing:<ns>/<kind>/<name>/<container>/<metric>`). It posts nothing on its own: findings are delivered through the digest (10i3). Shadow for the first 7 days after 10i1.

A recommendation counts only when VPA reports `RecommendationProvided=True`, the first sample is ≥ 7 days old, the target moved < ±30 % over the last 7 days, and the pod template has not changed in the last 24 h.

| Finding | Fires when | Proposed value |
|---|---|---|
| **Memory under-request** (fix first) | 30-day max working set > request, or any OOMKilled | request `ceil16Mi(max(upperBound, max) × 1.2)`; limit raised to ≥ 1.5 × that if max > 80 % of it |
| **Memory over-request** | request ≥ 1.5 × max(upperBound, 30-day max) and frees ≥ 64Mi, no OOMKilled | `ceil16Mi(max(upperBound, max) × 1.3)`, floor 32Mi |
| **Memory over-limit** | limit ≥ 3 × 30-day max and frees ≥ 256Mi, no OOMKilled; only after this workload's request cut held through its watch | `ceil16Mi(max(2 × max, upperBound × 1.5, request))` |
| **Memory creep** | 30-day working-set trend rising (Theil-Sen, the 10h1 detector) with no matching rise in traffic/restarts | no number: a tuning suggestion (leak, cache without bound, missing `GOMEMLIMIT`) |
| **CPU over-request** | request ≥ 3 × max(target, p99) and frees ≥ 50m | `ceil10m(max(target, p99) × 1.5)`, floor 10m |
| **CPU under-request** | p95 > request | `ceil10m(max(target, p95) × 1.5)` |

Never proposed: a CPU limit (throttling on 2-vCPU workers hurts more than it protects). Workloads with rare peaks (monthly imports, Immich jobs) can be marked in `aiops/rightsizing.yml` to skip limit cuts. That file holds the thresholds, the allow-list and the digest cadence; agent PRs may not edit it.

---

## 10i3 — Gná's rightsizing digest

**Cadence is the operator's choice: weekly (default), bi-weekly or monthly**, set in `aiops/rightsizing.yml` (`digest.cadence`) and switchable from Discord with `/aiops rightsizing cadence weekly|biweekly|monthly` (the bot records the override in the Toolbelt; the repo value is the default after a restart). `/aiops rightsizing now` posts one on demand.

One post per period in the forecasts channel, threaded:

1. **Scoreboard:** per worker, memory requested / limits / used now vs the last digest and vs the baseline (first digest), plus CPU requested. Sparklines link to vmui.
2. **Top suggestions** (at most 5, ranked by MiB freed, under-requests always first): workload, old → proposed values, the evidence (VPA bounds, 30-day max, samples age), and a **Draft PR** button for allow-listed workloads (10i4).
3. **Tuning suggestions:** Gná's read of memory-creep and high-baseline workloads against the chart's values (e.g. a worker count, a cache size, a heap). Written under the 10d grounding gate (every claim cites a tool call); a **Draft PR** for these goes through the normal `/aiops draft` `k8s` class, not the resources-only class.
4. **Results of earlier PRs:** each merged rightsizing PR's 10i5 verdict (held / regressed) and what it freed.
5. **Coverage:** controllers without a VPA, and findings suppressed (OOM in window, unstable target).

Each suggestion carries **Useful / Noise** labels like the 10h1 cards; a suggestion marked Noise is not repeated until its numbers move by more than 30 %. Nothing in the digest pages; it is a quiet post.

---

## 10i4 — Rightsizing PRs (author class `rightsizing`)

A digest **Draft PR** files a change request (one per workload; operator Approve; Discord user id), and the 10h2 dispatcher drafts it as for any class. All open findings for that HelmRelease go into one PR; at most 3 rightsizing PRs open at a time.

New class in `aiops/author-classes.yml`, shipped `enabled: false`:

```yaml
  rightsizing:
    enabled: false
    summary: Change only the resources (requests, memory limits) of ONE HelmRelease from a 10i finding.
    allow:
      - "k8s/asgard/apps/*/helmrelease.yaml"
      - "k8s/asgard/infrastructure/authentik/helmrelease.yaml"
    tested_on: a burst cluster (the k8s class's test; proves the pod starts and becomes Ready within the new limits); the 72 h post-merge watch proves it under real load
    burst_test: true
    checks:
      - {name: resources only, script: .github/scripts/ci-resources-only.py}
```

**`ci-resources-only.py`** (under `.github/`, so no agent can edit it) runs in the dispatcher and in `agent-scope`: it parses base and head YAML and fails unless every difference is under a `resources` key (`requests`/`limits` × `cpu`/`memory`) or a `resourcesPreset` becoming `"none"` beside a new `resources:` block. It also fails on any added CPU limit, a memory limit below its request, or values below the floors in `aiops/rightsizing.yml`. A probe PR that also edits an image tag must fail.

The allow-list is the T1/T2 apps (NetBox, Outline, Immich, Semaphore, MicroBin, Startpage, Teamspeak, VictoriaMetrics/Logs, Authentik). Vault, Traefik, cert-manager, MetalLB, ESO, Sealed Secrets, the CSI drivers, Garage, vmagent and Flux show up in the digest but get no button.

The PR carries a per-container old → new table with the evidence, the per-worker memory-requested totals before and after, the burst-test summary, and a rollback line (`git revert`; Flux rolls back).

---

## 10i5 — Post-merge watch

After a rightsizing PR merges and the HelmRelease is Ready at the new revision, the Toolbelt watches the workload for **72 h**: OOMKilled, CrashLoopBackOff or a restart rise, readiness failures, the controller not available, working set above 90 % of the new limit, and any alert correlated to the workload. The verdict (held / regressed) goes into the next digest and onto the PR's change request; a regression offers a **Draft revert PR** (the same class, restoring the old values). Nothing reverts on its own.

---

## Decisions

| Question | Choice | Why |
|---|---|---|
| Tool | upstream VPA, recommender only, `updateMode: "Off"` | recommendations without power to evict or mutate; changes only through reviewed PRs |
| Goldilocks / KRR | neither | Goldilocks' dashboard duplicates vmui + the digest ("no Grafana" reasoning) and its auto-created VPA objects are outside Git; KRR's Prometheus analysis is what the Toolbelt does against VictoriaMetrics, joined with VPA |
| Chart | Fairwinds `vpa`, recommender only | maintained, installs CRDs, components switch off individually |
| VPA objects | hand-written in `vpa-config/`, coverage reported in the digest | GitOps; CRD-dependent resources need their own Kustomization |
| Min floors | 10m / 32 MiB | the 25m / 250 MiB defaults keep small pods over-provisioned |
| VM sizes | unchanged (operator, 2026-10-05) | the goal is pod efficiency inside the VMs, not reclaiming host RAM |
| Delivery | a periodic Gná digest (weekly default; bi-weekly or monthly by choice), not a card per finding | rightsizing is slow-moving; one batched read per period beats a stream of cards |
| Memory | requests and limits both, from 30-day max + VPA upperBound with margin; limits only after the request cut held; never after an OOM | OOM is the one failure rightsizing can cause |
| CPU limits | never added | throttling on 2-vCPU workers |
| Testing | burst cluster (pod starts inside the new limits) + 72 h watch | the burst cluster has no real load, so the watch is the real proof |
| In-place resize | not used | that would be VPA acting on its own |
| Jotunheim | same pattern when Phase 7 deploys it | |

## Not in 10i

VPA updater or admission controller; autonomous apply or revert; HPA; resizing any VM or LXC; Calico/Tigera; Jobs/CronJobs; agent edits to `aiops/rightsizing.yml`, the CI check or the allow-list.

## Exit criteria

- **10i1:** every covered controller has a recommendation; no updater, admission controller or webhook exists.
- **10i3:** four consecutive digests delivered at the chosen cadence; ≤ 1 Noise label per digest after tuning.
- **10i4:** ≥ 3 rightsizing PRs merged with evidence and a burst-test summary; the resources-only probe fails CI.
- **10i5:** every merged rightsizing PR has a verdict; no regression reached a user before the watch caught it.
- **Outcome:** per-worker memory requested down ≥ 25 % from the first digest's baseline, memory used trending flat or down on the scoreboard, and worker CPU requested below 60 %.

## Operator steps

- Review and merge the 10i0 trim, the 10i1 Flux PR and the RBAC change.
- Pick the digest cadence (weekly is the default) and confirm the allow-list in `aiops/rightsizing.yml`.
- Enable the `rightsizing` class after the 10i5 watch is live (a reviewed PR).
- At build time: add `vpa-config` to CLAUDE.md's `<component>-config` list and a decisions row ("Workload rightsizing").
