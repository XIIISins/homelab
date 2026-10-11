<!-- docs/outline/components-and-interactions/gitops-and-automation.md -->

# GitOps & automation

How a change gets from an idea to a running system, which tool owns what, and how the system notices when reality drifts from the repository. The repository is public, and Git is the specification: nothing legitimate exists in production that is not described there.

---

## Who owns what

| Tool | Owns | Does not own |
|---|---|---|
| **Terraform** | Everything with an API: Proxmox guests, DNS rewrites, Cloudflare records, Vault config, Authentik apps, NetBox records, Tailscale ACLs, Garage buckets, Semaphore's own configuration, GitHub rulesets, AWS and DigitalOcean resources | The inside of a guest |
| **Ansible** | The operating system and services on LXCs and VMs: baseline, hardening, the K3s install and network plumbing, the Calico addon files, Postgres, Patroni, AdGuard and so on | Anything inside the cluster |
| **Flux CD** | Everything in `k8s/asgard/`: platform charts and applications. **Merging to `main` is the deploy.** | Calico, and the K3s binary |
| **Docs** | What has no useful API: the KPN box, DSM settings, the rebuild runbook | |

Never `kubectl apply` or `kubectl edit` against production: Flux reconciles, and `flux reconcile` is the only nudge. `terraform apply` runs only from the main checkout, and only one `ansible-playbook` runs at a time (Semaphore serialises its own).

---

## The change path

1. **Branch and pull request.** Work lands on a short-lived branch (`feat/`, `fix/`, `doc/`, `chore/`). Conventional-commit subjects, no attribution trailers.
2. **CI gate.** One required check, `CI gate`, aggregates path-aware static jobs: secret scanning over the whole history, YAML lint, Kubernetes manifests rendered and schema-validated, Terraform fmt and validate, ansible-lint, doc-link checks, and the AIOps schema suite. CI never touches a cluster, hypervisor, Vault or state.
3. **Review.** Docs and routine bumps may auto-merge; changes under `terraform/`, `ansible/` and `k8s/` get a diff review first. `main` rejects direct pushes.
4. **Merge = deploy for Kubernetes.** Flux pulls `main` and reconciles. For Terraform and Ansible, merge puts the change in the repository; applying is a deliberate step from the main checkout or through Semaphore.

Dependencies are bumped by Dependabot (GitHub Actions) and by a dedicated `chart-bump` agent for Helm charts, K3s, Calico, Flux and pinned images, which checks upstream, verifies that the target images exist, orders by blast radius and executes one item at a time.

---

## Flux layering

Flux watches `k8s/asgard/` with a small set of Kustomizations. The rule: **anything that needs a CRD installed by a chart lives in its own `<component>-config` Kustomization that depends on `infrastructure`.** A single Kustomization applies atomically, so a chart that fails to install would block the resources that depend on its CRDs, and one component's failure could block another's configuration (an External Secrets webhook failure once blocked MetalLB's config).

| Kustomization | Holds |
|---|---|
| `infrastructure` | Every platform HelmRelease and plain manifest: MetalLB, cert-manager, Traefik, Vault, ESO, Authentik, Cloudflared, observability agents, Garage and others |
| `infrastructure-config` | The External Secrets store |
| `metallb-config`, `vault-config`, `synology-csi-config`, `cert-manager-config`, `gateway-config` | CRD-dependent resources for each component (address pool, sealed secrets, issuers, Gateways and wildcard certificates) |
| `vpa-config` | One recommend-only VerticalPodAutoscaler per workload |
| `apps` | Application workloads: NetBox, Outline, Immich, Semaphore, Teamspeak, the media apps and the rest |

All chart and image versions are concrete pins in the HelmReleases; there are no floating tags. Calico and the K3s binary are deliberately outside Flux: the CNI is laid down as K3s addon files by Ansible and moved only by an upgrade playbook that exports the datastore first, because K3s prunes per addon file.

---

## Ansible and Semaphore

**One playbook per host group**, each listing every role that group needs: `asgard-k3s.yml`, `asgard-postgres.yml`, `asgard-adguard.yml` and so on. `site.yml` imports them all (after a preflight that refuses to run against a suspiciously small inventory) and is the single entry point for "converge everything". Where one playbook needs ordered rollout for some roles and parallel for others (Patroni replicas one at a time, agents everywhere at once), it contains multiple plays.

**Inventory comes from NetBox.** A dynamic inventory reads NetBox, groups hosts by `ansible:<group>` tags and caches the result for 24 hours (refreshed every 4). A static `hosts.yml` stays committed as the emergency fallback for a cold NetBox or a disaster rebuild. This is why every new guest needs its NetBox declarations in the same Terraform change.

**Semaphore** runs in asgard K3s (chosen over AWX for roughly 100 MB versus 3 GB), with its state in Postgres, sign-in through Authentik and a custom image that bakes the pinned Ansible collections (the stock image silently ignored the repo's pins). Its project, templates and schedules are themselves Terraform.

| Template | Cadence | Purpose |
|---|---|---|
| `asgard-drift-check` | every 6 h | `site.yml --check --diff`. Any change or failure posts an `alert` to Hermod; clean runs are silent. |
| `asgard-apply` | manual | Converge the fleet. Failure posts `critical`. |
| `asgard-os-updates` | manual | Fleet patching, one node per quorum group at a time. |
| `asgard-fleet-agents` | daily | Re-converge vlagent and the Zabbix agent everywhere. |
| `infra-health-check` | every 12 h | Active prober: Cloudflare token validity, certificate expiry for the three zones, Patroni and etcd quorum, PBS failures. |
| `pbs-restore-test` | weekly | Restore a backup into a scratch container and verify it. |
| `refresh-netbox-inventory` | every 4 h | Rebuild the inventory cache. |
| Canary variants | daily | The same loop against disposable test containers, alerts capped at `info`. |

The drift-check is the passive half of "self-healing": it confirms Git equals reality. The health-check prober is the active half: it checks live state that no converge would catch. Today the operator reviews a drift diff and either fixes the IaC or applies it; the direction is automatic convergence of small drift, with larger failures handled by rebuilding the guest from scratch.

---

## The AIOps layer

On top of detection sits a layered, approval-gated automation tier, built up by blast radius (T0 read-only through T3), with a kill switch before anything that can change state.

| Piece | Where | Role |
|---|---|---|
| **Toolbelt** | Frigg | A write-less API of read-only tools (metrics, logs, cluster state, Zabbix, NetBox) that grounds the agent's reasoning. |
| **Diagnosis agent** | Gná (n8n) | Receives context-rich Zabbix events beside Hermod, queries the Toolbelt, posts a structured diagnosis to a Discord thread. |
| **Action engine and bot** | Ratatoskr | The Discord bot is the only approver. Actions come from a registry and run as Semaphore tasks; approvals are bound to the exact parameters and expire. |
| **Autonomous healing** | Canaries only | A small policy set (such as restarting a failed unit) may act without approval on the disposable canary pool, behind a master switch, a circuit breaker and rate limits. |
| **Rebuild loop** | Canaries proven | An approval-gated rebuild of a canary from scratch works; replica and worker rebuilds are not built. |
| **PR author** | Frigg | Drafts reviewed pull requests for classes such as docs, drift notes, capacity and rightsizing. Infrastructure PRs are tested first on a canary or on an ephemeral burst cluster. It never merges. |
| **Rightsizing** | VPA + metrics | Pod resources are sized from 30 days of history; recommend-only VPA feeds findings and a periodic digest. |

Test substrates exist so that automation never practises on production: a disposable canary pool on Urd and an ephemeral DigitalOcean burst cluster. An outside watcher on the offsite node monitors the homelab from the internet.

---

## Failure surfaces

| What fails | Effect |
|---|---|
| Flux | No new deploys; running workloads are unaffected. Drift stops being corrected. |
| Semaphore | No scheduled drift-check or health-check; manual runs from the control node still work. |
| NetBox | Ansible uses the cached inventory, then the static fallback. |
| Frigg | No Toolbelt, no agent PRs, no non-interactive IaC runs. The operator's workstation remains an alternative. |
| Hermod | Alerts are not delivered; the underlying checks still log to VictoriaLogs. |
| A bad merge to `main` | CI is static, so cluster-level breakage is possible. Flux reports it, and reverting the commit is the rollback. |

---

## See also

- **Compute & hypervisors** (this section): how guests are created.
- **Identity & secrets** (this section): the credentials these tools use.
- **Observability** (this section): the alert path to Hermod and Discord.
- **Procedures**: whole-cluster rebuild, K3s upgrade, credential rotation.
- **Kubernetes & Flux** troubleshooting (Troubleshooting).
