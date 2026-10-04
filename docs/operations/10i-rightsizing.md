<!-- docs/operations/10i-rightsizing.md -->

# Phase 10i — Workload rightsizing (VPA recommend-only + AIOps PRs): plan

*Drafted 2026-10-04. Status: 🔲 planned, nothing built. Parent: [`aiops-roadmap.md`](aiops-roadmap.md) §10i. Structure mirrors [`10h-predictive-change.md`](10h-predictive-change.md). Builds on the 10d Toolbelt (read-only tools, audit log), the 10h1 forecast cards and the 10h2 PR author (change requests, the dispatcher, `aiops/author-classes.yml`, the `agent-scope` CI job). Motivating gotcha: [`known-issues/k8s-scheduling.md`](../known-issues/k8s-scheduling.md) ("Worker CPU requests are nearly full").*

---

## Goal and boundary

The asgard workers (Einherjar-urd/verd/skuld, 2 vCPU each) sit at **84–90 % CPU requested while real usage is ~5–10 %** (measured 2026-09-30). That is why surge rollouts deadlock with `Insufficient cpu`. On 2026-10-04 another thread found concrete cases: the NetBox pods request 500m and use about 1m, and `authentik-server` is over-provisioned. Requests were set by chart presets and guesses, never by measurement.

The second pressure is **memory on the Proxmox hosts**. Each host has 32 GiB, and the asgard K3s VMs alone claim 20 GiB of it on every host (worker 16 GiB + CP 4 GiB, `terraform/proxmox/asgard-k3s/main.tf`), before any LXC. Claimed guest memory is getting close to the physical RAM (operator, 2026-10-04), and the roadmap already lists "RAM-tight Proxmox hosts" as a weak spot.

So 10i has **two targets**:

1. **CPU:** worker CPU requested below **60 %** on every worker (scheduling headroom).
2. **Memory:** claimed guest memory per Proxmox host at or below **75 % of physical RAM** (24 of 32 GiB). Pod memory requests and limits are trimmed first, which lets the worker VMs shrink. LXC and VM sizes that are far above their real use shrink too. The baseline is measured by the allocation ledger (10i2) before any change.

10i makes requests a measured, reviewed value:

- **10i1 — VPA in recommend-only mode.** The Vertical Pod Autoscaler **recommender only** (no updater, no admission controller) on asgard, with a `VerticalPodAutoscaler` object per workload in `updateMode: "Off"`. It writes recommendations into the VPA object's status and changes nothing in the cluster.
- **10i2 — Rightsizing findings (T0).** The Toolbelt reads VPA recommendations plus 14 days of real usage from VictoriaMetrics, decides which workloads are worth changing, and posts quiet cards next to the 10h1 forecasts. Gná gets the same data as a read-only tool for diagnosis.
- **10i3 — Rightsizing PRs.** A new 10h2 author class, `rightsizing`, turns an approved card into a draft PR that changes **only the `resources` of one HelmRelease**, with the evidence in the description. A human merges; merging is the deploy (Flux).
- **10i4 — Post-merge watch.** After the merge the Toolbelt watches the workload for 72 h (restarts, OOMKilled, throttling, new alerts) and posts the result on the card; on a regression it offers a revert PR.
- **10i5 — Host memory reclaim.** This step turns pod savings into freed host RAM. It shrinks the worker VMs once their pods need less (a human Terraform change, one worker at a time), and shrinks over-sized LXCs/VMs through a shrink direction on the existing `capacity` class.

What stays true after 10i:

- **Nothing changes a running pod except a merged PR.** VPA never evicts or mutates (no updater, no admission webhook, `updateMode: "Off"`). Kubernetes 1.36 in-place resize and VPA's `InPlaceOrRecreate` mode are explicitly not used.
- **No new authority for agents.** The Toolbelt gains read verbs on two VPA resources, nothing else. The registry, autonomy scope, rebuild scope and executor are untouched; 10i has no executor action at all.
- **Memory is a target, but it is handled with more care than CPU.** CPU is compressible (a too-low request means slower, not dead); memory is not (too low means OOMKilled). So memory cuts use the 14-day **max** and VPA's **upperBound**, never the target, keep a wider margin, and a cut is never made on a workload that was OOMKilled in the window.
- **T3 workloads are recommended on, never PR'd.** Vault, the control-plane add-ons, Calico, Flux and friends get VPA objects (the numbers are useful to the operator) but are not in the author's allow-list.

## Pre-flight: what this depends on and touches

Checked: `docs/` (no VPA / rightsizing mention before this plan), `open-questions.md`, `known-issues/k8s-scheduling.md`, `flux-helm-kustomize.md`, `observability.md`, the K3s role config, the vmagent scrape config, `aiops-readonly` RBAC, `aiops/author-classes.yml`, `aiops/toolbelt/capacity_draft.py`.

| Finding | Effect on 10i |
|---|---|
| Workers are at 84–90 % CPU requested | The recommender pod itself needs room. **10i0 (below) trims the worst offenders by hand first**, so the VPA deploy does not deadlock on the very problem it is meant to fix. |
| K3s ships `metrics-server` (only `traefik`, `servicelb`, `local-storage` are disabled in `roles/k3s/templates/config-*.yaml.j2`) | The recommender's default live source exists; no new metrics plumbing for VPA. Confirm `kubectl top pods -A` works before 10i1. |
| vmagent already scrapes cAdvisor and kube-state-metrics; VictoriaMetrics keeps 1 month | 14 days of real usage per container is available today for the Toolbelt's cross-check. |
| kube-state-metrics is v2.20 (chart 8.6.0); KSM dropped its built-in VPA collector in 2.9 | Recommendation **history** in VictoriaMetrics needs a KSM `customResourceState` config for `verticalpodautoscalers` plus a KSM RBAC rule (10i1). Without it the Toolbelt only sees the current recommendation. |
| CRD-dependent resources cannot share a Kustomization with the chart that installs the CRD (`flux-helm-kustomize.md`, decisions "Flux structure") | VPA objects go in a new **`vpa-config/`** Kustomization with `dependsOn: infrastructure`. CLAUDE.md's list of `<component>-config` dirs gets the new entry at build time. |
| CPs are tainted `NoSchedule` | The recommender schedules on workers; that is fine, but see the first row. |
| `aiops-readonly` ClusterRole has no `autoscaling.k8s.io` rule | Add get/list/watch on `verticalpodautoscalers` and `verticalpodautoscalercheckpoints` (no secrets, no configmaps; the negative test is unchanged). |
| Several charts set requests through presets (NetBox worker uses `resourcesPreset: medium`) | The PR must be able to replace a preset with an explicit block (`resourcesPreset: "none"` + `resources:`); the resources-only check allows exactly that pair. |
| The existing `capacity` class only covers Terraform guest sizes and refuses K3s nodes | 10i3 is a separate class with `k8s/` paths; `capacity` is unchanged. Worker vCPU growth stays a human Terraform decision. |
| 10h2 burst-cluster tests for `k8s/` are not built | Rightsizing does not wait for them: a burst cluster has no real load, so it cannot prove a request is right. The proof is the post-merge watch (10i4), which must exist before the class is enabled. |

## Sequence

| Step | What | Depends on | Why in this order |
|---|---|---|---|
| **10i0** | Hand-trim the obvious over-requests (NetBox web/worker, `authentik-server`) via a normal reviewed PR, using `kubectl top` and 14-day VictoriaMetrics p95 as evidence | nothing | frees worker requests so the recommender and the next surge rollouts fit; it is the quick win the other thread found and should not wait 8 days for VPA |
| **10i1** | Deploy VPA recommender + `vpa-config/` objects + KSM VPA metrics | 10i0 | needs a few hundred millicores of headroom; starts the ~8-day learning clock |
| **10i2** | Toolbelt read tool + daily rightsizing pass, shadow first, then cards | 10i1 + 7 days of data | VPA recommendations are not trustworthy before about a week of samples |
| **10i4** | Post-merge watch | 10i2 | must exist before any agent PR merges |
| **10i3** | `rightsizing` author class + resources-only CI check, enabled last | 10i2, 10i4 | the class is the only piece that changes the cluster (through a human merge) |
| **10i5** | Worker VM memory shrink (human, one worker at a time) and the `capacity` shrink direction for LXCs/VMs | the ledger (10i2); worker shrink also needs the pod memory trims from 10i3 merged and watched | host RAM is only freed when a guest's assigned memory goes down; pod requests alone free nothing on the host |

10i4 is built before 10i3 on purpose; the numbering follows the pipeline, not the build order. The LXC/VM half of 10i5 does not depend on VPA at all and can start as soon as the ledger exists.

---

## 10i1 — VPA in recommend-only mode

### What gets deployed

- **`k8s/asgard/infrastructure/vpa/`**: HelmRelease for the Fairwinds `vpa` chart (HelmRepository `https://charts.fairwinds.com/stable`), which installs the VPA CRDs and lets each component be switched off. Values:
  - `recommender.enabled: true`, `updater.enabled: false`, `admissionController.enabled: false`.
  - recommender `resources.requests` small (about 20m CPU / 128Mi), no CPU limit; one replica, no leader-election concerns.
  - recommender flags: `--pod-recommendation-min-cpu-millicores=10` and `--pod-recommendation-min-memory-mb=32`. **The defaults (25m and 250 MiB) would floor every tiny pod at 250 MiB**, which is exactly the over-provisioning we are trying to remove. `--recommendation-margin-fraction` stays at the 0.15 default.
  - history from VPA's own checkpoints (the default `VerticalPodAutoscalerCheckpoint` objects survive a recommender restart). The Prometheus history provider pointed at VictoriaMetrics is **not** used in v1 (decision below).
  - The chart and app versions are pinned at build time by the [`chart-bump`](../../.claude/agents/chart-bump.md) agent (target images verified to exist, VPA's supported Kubernetes range checked against K3s 1.36).
- **`k8s/asgard/vpa-config/`**: a Flux Kustomization (`dependsOn: infrastructure`) holding one `VerticalPodAutoscaler` per controller, hand-written in Git:

  ```yaml
  apiVersion: autoscaling.k8s.io/v1
  kind: VerticalPodAutoscaler
  metadata:
    name: netbox
    namespace: netbox
  spec:
    targetRef: {apiVersion: apps/v1, kind: Deployment, name: netbox}
    updatePolicy:
      updateMode: "Off"
  ```

  Coverage: every Deployment, StatefulSet and DaemonSet in Flux-managed namespaces, including T3 ones (recommendations cost nothing). Excluded: Jobs and CronJobs (VPA fits short-lived pods poorly), Calico/Tigera (not Flux-managed; its resources change only through `calico-upgrade.yml`). The Toolbelt pass reports controllers without a VPA as a coverage finding, so a new app does not silently drop out.
- **kube-state-metrics**: `customResourceState` config exposing `status.recommendation.containerRecommendations[*].{lowerBound,target,upperBound,uncappedTarget}` for CPU and memory, plus the matching `rbac.extraRules`. vmagent already scrapes KSM, so the series land in VictoriaMetrics with no scrape change.

### What it costs

One small pod on a worker, the CRDs, ~30 small objects, and a few hundred KSM series. No webhook (so no new failure mode on pod admission), no evictions.

### Acceptance

- `kubectl get vpa -A` shows every covered controller with `RecommendationProvided=True` within 24 h.
- `updater` and `admission-controller` pods do not exist; no `MutatingWebhookConfiguration` for VPA exists.
- The VPA target series are queryable in vmui.
- A recommender pod restart keeps recommendations (checkpoints reloaded).

---

## 10i2 — Rightsizing findings (T0)

### Data

A new Toolbelt read tool, **`kube.rightsizing`** (args: namespace, controller kind, name), returns per container:

| Field | Source |
|---|---|
| current requests and limits | the controller's pod template (`kube.get`, existing RBAC) |
| VPA lowerBound / target / upperBound / uncappedTarget | VPA status (new RBAC rule) |
| VPA sample age and count | the checkpoint object (`firstSampleStart`, `totalSamplesCount`) |
| recommendation stability | KSM VPA series over 7 days in VictoriaMetrics (min/max of `target`) |
| real usage p50 / p95 / p99 / max over 14 days | cAdvisor `container_cpu_usage_seconds_total` (rate) and `container_memory_working_set_bytes` in VictoriaMetrics |
| OOMKilled and restarts in 14 days | pod `lastState` + events |
| CPU throttling ratio (only if a CPU limit is set) | `container_cpu_cfs_throttled_periods_total` / `container_cpu_cfs_periods_total` |
| node pressure | sum of CPU and memory requests vs allocatable per worker, plus the worker's 14-day max used memory (the same numbers the 10h1 memory-headroom signal uses) |

The same pass builds the **memory allocation ledger** that 10h1 planned and never built: per Proxmox host, the sum of assigned guest memory (Proxmox read tool, already in the Toolbelt) vs physical RAM, plus each guest's 14-day max used memory from Zabbix. It is posted as one quiet card per host, refreshed weekly, and it is the baseline and the scoreboard for the memory target.

It is added to Gná's toolset, so a diagnosis of `FailedScheduling: Insufficient cpu` or a HelmRelease upgrade timeout can say "requests are the constraint: NetBox requests 500m, p99 use is a few millicores, VPA target is 15m" instead of only "the cluster is full". A runbook id for "worker CPU requests exhausted" is added to `aiops/runbooks.yml` by a normal human PR.

### The daily pass

Runs inside the existing forecast job on Frigg (no new service). Each finding is a row in the `forecasts` table with `kind = rightsizing`, `metric = cpu-request | memory-request | memory-limit`, fingerprint `rightsizing:<ns>/<kind>/<name>/<container>/<metric>`, posted by the bot as a quiet card in the forecasts channel with **Useful / Noise** and, for allow-listed workloads, **Draft fix PR**. Shadow mode (stored, not posted) for the first 7 days after 10i1, the same pattern 10h1 used. Thresholds live in a repo file `aiops/rightsizing.yml` (workload allow-list, floors, ratios), which agent PRs may not edit.

### Rules (initial; tuned from the Useful/Noise labels through reviewed PRs)

A recommendation is only considered when VPA reports `RecommendationProvided=True`, the first sample is at least **7 days** old, the target moved less than **±30 %** over the last 7 days, and the controller has not changed its pod template in the last 24 h.

| Finding | Fires when | Proposed value | Priority |
|---|---|---|---|
| **CPU under-request** (reliability) | 14-day p95 use > current request | `ceil10m(max(VPA target, p95) × 1.5)` | high: posted first |
| **Memory under-request** (reliability) | 14-day max working set > current request, or any OOMKilled | request `ceil16Mi(max(VPA upperBound, max working set) × 1.2)`; limit raised only if max working set > 80 % of it, to `max(old limit, new request × 1.5)` | high |
| **CPU over-request** (capacity) | current request ≥ 3 × max(VPA target, p99) **and** the reduction frees ≥ 50m | `ceil10m(max(VPA target, p99) × 1.5)`, floor **10m** | ordered by millicores freed on workers |
| **Memory over-request** (capacity) | current request ≥ 1.5 × max(VPA upperBound, 14-day max working set) **and** frees ≥ 64Mi, **and** no OOMKilled in 14 days | `ceil16Mi(max(VPA upperBound, max working set) × 1.3)`, floor **32Mi** | same rank as CPU: ordered by MiB freed |
| **Memory over-limit** (capacity) | current limit ≥ 3 × the 14-day max working set **and** the cut frees ≥ 256Mi, no OOMKilled in 14 days | `ceil16Mi(max(2 × max working set, VPA upperBound × 1.5, new request))` | after the request cut for the same workload has held through its 10i4 watch |

Never added by a finding: a CPU limit (throttling on a 2-vCPU node does more harm than a noisy neighbour). An existing CPU limit is left alone unless it would end up below 4 × the new request, in which case the finding says so and the PR leaves the limit untouched (a human decides).

Startup spikes are the honest gap: p95/p99 over 14 days hide a JVM or Python start. That is acceptable for CPU (a slower start, not a failure; readiness probes still gate traffic) and is why memory uses the **max** working set and VPA's **upperBound**, not the target.

Limits matter for the memory target even though the scheduler only counts requests: a worker VM can only shrink safely if the pods on it cannot together grow past its RAM. The worker shrink rule in 10i5 therefore looks at the sum of limits too, which is why limits are cut (with a 2× margin), not just requests. A workload that runs monthly jobs or imports (Immich machine learning, NetBox imports) can have a 14-day max that misses its real peak; such workloads are flagged in `aiops/rightsizing.yml` with a longer window (30 days, the VictoriaMetrics retention) or excluded from limit cuts.

---

## 10i3 — Rightsizing PRs (author class `rightsizing`)

### Shape

The same 10h2 flow: a card's **Draft fix PR** files a change request (one per finding fingerprint, operator Approve, Discord user id), the dispatcher on Frigg starts an unprivileged drafting session that returns a patch, checks it in its own clean clone, pushes `agent/rightsizing/<id>-<slug>` and opens a draft PR. The request text is generated from the finding (like `capacity_draft.py` does for `capacity`): which HelmRelease, which container, the old and proposed values, and the rule that produced them. All findings for one HelmRelease that are open at the time go into **one PR** (a workload's containers are reviewed together); at most 3 rightsizing PRs open at once.

### New class in `aiops/author-classes.yml` (shipped `enabled: false`)

```yaml
  rightsizing:
    enabled: false
    summary: Change the resources (requests and memory limits) of ONE HelmRelease from a 10i finding.
    allow:
      - "k8s/asgard/apps/*/helmrelease.yaml"
      - "k8s/asgard/infrastructure/authentik/helmrelease.yaml"
    tested_on: no substrate (a burst cluster has no real load); offline render-diff, then the 72 h post-merge watch
    checks:
      - {name: resources only, script: .github/scripts/ci-resources-only.py}
      - {name: doc links, script: .github/scripts/ci-doc-links.py}
```

The allow-list is the T1/T2 application set (NetBox, Outline, Immich, Semaphore, MicroBin, Startpage, Teamspeak, VictoriaMetrics/Logs, Authentik). Everything else under `k8s/asgard/infrastructure/` (Vault, Traefik, cert-manager, MetalLB, ESO, Sealed Secrets, CSI drivers, Garage, vmagent, Flux) stays human-only even when its card shows a finding.

### The resources-only check

`.github/scripts/ci-resources-only.py` (under `.github/`, so no agent PR can edit it) runs in the dispatcher and in the `agent-scope` CI job for this class. It parses the base and head YAML of each changed HelmRelease and fails unless every difference sits under a key named `resources` (with only `requests` / `limits` / `cpu` / `memory` beneath it), or is a `resourcesPreset` turning into `"none"` in the same block that gains a `resources:`. It also fails if any CPU limit is added, if a memory limit ends up below 2 × its new request, or if a request lands below the floors in `aiops/rightsizing.yml`. (Whether a cut is justified by usage is the Toolbelt's evidence in the PR, which the reviewer reads; CI checks the shape.) A probe PR that also edits an image tag must fail it.

### What the PR carries

- A per-container table: old → new request/limit, VPA lower/target/upper, 14-day p95/p99/max, samples age, and the rule that fired.
- The worker CPU-requested and memory-requested/limit totals before and after (the point of the exercise), and for a **raise** whether the surge pod fits on any worker (a raise that does not fit says so: the merge would reproduce the surge deadlock).
- An offline render-diff where the chart can be fetched (the `chart-bump` helper `render-diff.sh`), showing that only `resources` change in the rendered manifests.
- `Not tested on a substrate:` with the reason, and the rollback line (`git revert` of the merge commit; Flux rolls it back).

Lowering requests eases its own rollout (the new pod asks for less than the old one holds), which is why CPU over-request PRs are safe to merge first on a tight cluster.

---

## 10i4 — Post-merge watch

When a rightsizing PR merges, the Toolbelt (which already reads the repo through `aiops-toolbelt-ghread`) records the merge, waits for the HelmRelease to report Ready at the new revision, then watches the workload for **72 h**:

- any OOMKilled, CrashLoopBackOff or restart count rise;
- readiness failures or the Deployment/StatefulSet not available;
- CPU throttling ratio above 10 % where a CPU limit exists;
- any Zabbix or VictoriaMetrics alert whose target is the workload, via the existing incident correlation.

The card is updated with **held** or **regressed (what, when)**. A regression offers **Draft revert PR**, which files a `rightsizing` request restoring the old values (it passes the same resources-only check). Nothing is reverted automatically.

---

## 10i5 — Host memory reclaim

Pod requests and limits are bookkeeping inside the worker VM; the Proxmox host only gets RAM back when a guest's **assigned** memory goes down. 10i5 has two halves.

### Worker VMs (human-applied, never an agent class)

The workers are 16 GiB each (`locals.workers` in `terraform/proxmox/asgard-k3s/main.tf`). Once the pod memory cuts from 10i3 have merged and held through their 10i4 watches, the Toolbelt computes per worker:

- the sum of memory **requests** of the pods it runs, and the same sum if the busiest worker's pods had to move to the other two (one worker down is the design case: drains, rebuilds, Skuld freezes);
- the sum of memory **limits**, and the worker's 14-day max used memory.

It proposes a new worker size, rounded up to 1 GiB, of `max(1.3 × requests in the one-worker-down case, 1.2 × the 14-day max used memory)` plus the system reserve (kubelet, K3s agent, vlagent). The finding is a card on the ledger, not an agent PR: the K3s nodes are outside the `capacity` class by design, and a worker resize reboots the VM. The operator (or a Claude session they start) makes the one-line Terraform change, then applies it **one worker at a time**: cordon and drain, `terraform apply` (the provider reboots the VM on a memory change), uncordon, wait for every HelmRelease Ready, then the next worker. The three workers stay the same size, because failover symmetry is the rule the CPs already follow.

Memory ballooning is not used for this: it hides the claim without freeing a reliable amount, and it does not help K3s, which sizes allocatable from boot-time RAM.

### LXCs and other VMs (`capacity` class, new shrink direction)

`capacity` today only grows a guest's `memory { dedicated }`. 10i5 adds a **shrink** remedy, fed by a new `memory-oversized` finding from the ledger pass:

- fires when assigned memory ≥ 2 × the guest's 30-day max used memory (Zabbix, `vm.memory.size[available]` turned into used) and the cut frees ≥ 512 MiB;
- proposes `ceil256Mi(max(1.5 × 30-day max used, 512 MiB))`, never more than halving in one PR;
- excluded: T3 guests (Patroni Fulla/Vör/Idunn, the HAProxy/etcd trio, PBS, Frigg, the AdGuard primary Saga) and anything with an OOM-kill in Zabbix or the kernel log within 30 days. Those get the card but no button.

The shrink direction ships disabled in `capacity_draft.py` (a reviewed PR enables it), the PR carries the 30-day max used and the headroom left, and `terraform apply` stays the operator's. An LXC memory change applies without a restart; a VM change reboots it (checked per guest at build time). The 10i4 watch covers these too: 72 h of Zabbix memory and OOM checks on the guest.


| Question | Choice | Why |
|---|---|---|
| Tool | upstream VPA **recommender only**, `updateMode: "Off"` | the recommendations without any power to evict or mutate; the PR flow is the only path to change |
| Goldilocks / KRR | neither in v1 | Goldilocks' value is its dashboard and auto-created VPA objects; the dashboard duplicates vmui + cards (same reasoning as "no Grafana"), and objects created outside Git break the GitOps rule. KRR is a CLI over Prometheus history; the Toolbelt pass does the same against VictoriaMetrics and can join it with VPA |
| Chart | Fairwinds `vpa` chart, recommender only | maintained, installs CRDs, components switch off individually; revisit the upstream `kubernetes/autoscaler` chart if it becomes the maintained default |
| VPA objects | hand-written in `vpa-config/`, coverage reported by the Toolbelt | GitOps; CRD-dependent resources need their own Kustomization |
| Recommender history | its own checkpoints; no Prometheus provider | no coupling between the recommender and VictoriaMetrics' label layout; the Toolbelt cross-checks against VM history anyway. Cost: about a week of learning after deploy |
| Min recommendation floors | 10m CPU / 32 MiB memory | the 25m / 250 MiB defaults would keep tiny pods over-provisioned |
| CPU limits | never added by an agent PR | throttling on 2-vCPU workers hurts more than it protects |
| Memory | a target alongside CPU (hosts are close to fully claimed); cut from the max working set and VPA upperBound with margin, limits only after the request cut held, never after an OOM | host RAM is the scarcer resource now, but OOM is the one failure rightsizing can cause |
| Host memory | freed by shrinking guests: worker VMs by hand one at a time, LXCs/VMs via the `capacity` shrink direction; no ballooning | pod-level cuts free nothing on the host until the guest's assigned memory goes down |
| Test substrate | none; the post-merge watch is the proof | a burst cluster has no real load, so it cannot validate a request |
| Scope | T1/T2 apps allow-listed; T3 recommended on but human-only | the same tiers as the rest of Phase 10 |
| In-place resize / `InPlaceOrRecreate` | not used | that is VPA acting on its own; revisit only after 10i has a clean record and as a separate decision |
| Jotunheim | same pattern when Phase 7 deploys it | recommendations are per cluster; the class gains `k8s/jotunheim/apps/*/helmrelease.yaml` then |

## Not in 10i

VPA updater or admission controller; any autonomous apply or revert; HPA; changing worker vCPU or node counts; an agent PR that resizes a K3s node (worker memory shrinks in 10i5 are a human Terraform change; the `capacity` class still refuses K3s nodes); shrinking T3 guests; Calico/Tigera resources; Jobs/CronJobs; agent edits to `aiops/rightsizing.yml`, the CI check, or the allow-list.

## Exit criteria

- **10i1:** every covered controller has a recommendation; no VPA updater, admission controller or webhook exists; recommendation history queryable in vmui.
- **10i2:** 7 days of shadow, then cards; at most 2 Noise labels per week after tuning; Gná cites requests vs use in a real `Insufficient cpu` diagnosis or a replay.
- **10i3:** at least 3 rightsizing PRs merged by the operator with evidence; a probe PR touching a non-`resources` key fails `agent-scope`; worker CPU requested drops from 84–90 % to **below 60 %** on every worker.
- **10i4:** every merged rightsizing PR has a held/regressed verdict; any regression was caught by the watch, not by a user.
- **10i5:** claimed guest memory at or below **75 %** of physical RAM (24 of 32 GiB) on every Proxmox host, measured by the ledger; the three workers resized together and still schedule the one-worker-down case; no OOM-kill caused by a shrink.

## Operator steps (the ones Claude cannot do)

- Review and merge the 10i0 trim PR, the 10i1 Flux PR and the RBAC change (merging `k8s/` is the deploy).
- Decide the workload allow-list and floors in `aiops/rightsizing.yml` (the plan's list is the default).
- Enable the `rightsizing` class in `aiops/author-classes.yml` after the 10i4 watch is live (a reviewed PR, as for every class).
- Apply the worker VM resize one worker at a time (drain, `terraform apply`, uncordon), and `terraform apply` each LXC/VM shrink PR.
- Confirm the 75 % memory target once the ledger shows the baseline.
- At build time: add `vpa-config` to CLAUDE.md's list of `<component>-config` Kustomizations and a decisions row ("Workload rightsizing").
