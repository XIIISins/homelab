<!-- docs/outline/components-and-interactions/gitops-and-automation.md -->

# GitOps & automation

This subpage covers how changes flow from a Git commit into running infrastructure. Three IaC tools split the work along clean boundaries — Terraform for things with APIs, Ansible for OS-level state on nodes, Flux for everything inside the K3s clusters. A fourth piece (Semaphore) schedules Ansible and watches for drift between Git and reality.

The principle that shapes every choice on this page: each tool owns the layer where it has the most leverage, and the boundaries are chosen so the same change never has two owners. Terraform doesn't reach into running pods; Flux doesn't provision VMs; Ansible doesn't deploy K8s workloads. When the boundary is fuzzy, the choice is whichever tool gives a cleaner failure mode.

---

## The three tools and what they own

| Tool | Owns | Lives in |
|---|---|---|
| **Terraform** | Anything with a provider/API: Proxmox VMs+LXCs, Cloudflare DNS+tunnel, AWS KMS, Vault config (auth methods, policies, roles, KV), Authentik (users, groups, OIDC/SAML providers), NetBox writes, AdGuard rewrites, Tailscale tailnet, Garage buckets, Semaphore projects+templates | `terraform/<provider>/` |
| **Ansible** | OS-level state on every node: baseline (packages, sysctls, resolv.conf), hardening (SSH, SELinux, firewall), service installs that aren't containerized (Postgres + Patroni + HAProxy + etcd on LXCs, AdGuard, Tailscale, Zabbix server, vlagent, Zabbix agent). Also K3s install + Calico addon manifest. | `ansible/` |
| **Flux** | Every in-cluster workload: HelmReleases, Kustomizations, Secrets via ESO, app manifests | `k8s/<cluster>/` |
| **Docs** | KPN router config + any system without a useful API. If it can't be code, it's documented as a procedure. | `docs/procedures/` |

The boundary is intentional. Terraform talks to APIs and stops at the OS. Ansible talks to the OS and stops at the K3s boundary (apart from installing K3s itself). Flux talks to the K3s API server and stops at the node boundary (apart from CSI driver pods, which by definition reach down). No tool spans more than one layer.

### Why K3s install is Ansible, not Flux

K3s itself is the substrate Flux runs on, so Flux can't install it. The Calico addon manifest sits at the same level — it's templated by the Ansible `k3s` role and dropped into K3s's `/var/lib/rancher/k3s/server/manifests/` directory on the init node. K3s's built-in addon controller applies it. Changing Calico config via `kubectl edit` gets reverted by that controller; the template is the source of truth.

---

## Flux — in-cluster reconciliation

Flux CD runs in every K3s cluster. One `flux-system` namespace per cluster; one set of `Kustomization` objects watching the cluster's slice of the repo.

- **Read path:** Flux's source controller polls the GitHub repo. On every commit, it fetches and updates the in-cluster source-of-truth artifact.
- **Reconcile path:** the Kustomize controller applies manifests to the cluster, the Helm controller drives chart releases. The current state of every object is reconciled against Git on a continuous loop.
- **No manual `kubectl apply` for production.** Production workloads always flow through Git. Manual apply is a debugging escape hatch only — and any state it creates gets overwritten on the next Flux reconcile if it diverges from Git.

### The per-component-config layering

The repo layout splits each cluster into three Kustomization tiers:

| Tier | Contents | Depends on |
|---|---|---|
| `infrastructure` | Charts that install operators + CRDs (cert-manager, ESO, MetalLB, Vault, Synology CSI, Traefik, Gateway API) | — |
| `<component>-config/` | CRD-dependent resources that the operator above owns (cert-manager `Certificate`s, ESO `ClusterSecretStore`, MetalLB `IPAddressPool`, Vault auth roles' K8s objects, Gateway API `Gateway` + `HTTPRoute`) | `infrastructure` |
| `apps/` | Leaf consumer workloads (Authentik, NetBox, Outline, Teamspeak, etc.) | `infrastructure` + relevant `<component>-config/` |

The split exists because Flux's Kustomization dry-run runs *before* the chart installs. A `Certificate` resource in the same Kustomization as cert-manager's chart fails dry-run — the CRD isn't there yet. Per-component-config Kustomizations with `dependsOn: infrastructure` solve the timing without coupling the cert-manager chart to the certificates it issues.

### What a change looks like

1. Operator edits a manifest in `k8s/asgard/...` and pushes to `main`.
2. Flux source controller pulls; the GitRepository's `revision` advances.
3. Affected Kustomizations reconcile. Flux applies via server-side apply.
4. Pods restart or new RS/StatefulSet revisions roll out under whatever update strategy the workload declares.

Push *is* the deploy. There's no CI gate, no merge queue, no separate release step. Single-operator repo + private; reverting is a commit, not a rollback.

---

## Ansible — playbooks per host-group

Ansible's organising rule is: **one playbook per inventory host-group**. The playbook lists every role that group needs — baseline, hardening, service-specific roles, agents.

| Pattern | Use |
|---|---|
| `asgard-<service>.yml` | Per-host-group entry point. Contains every role that group needs. |
| `vlagent.yml`, `zabbix-agent.yml` | Cluster-wide agent rollouts. Cross-group. Deliberate one-shots, not implicit side-effects of every playbook run. |
| `site.yml` | Top-level orchestrator. `import_playbook:` each `asgard-*.yml` plus the cluster-wide agents. Single entry point for "converge everything." |

A single playbook file can hold multiple plays with different host selectors and `serial:` settings. Patroni replicas roll one-at-a-time via `serial: 1` for safety; the agent install on the same hosts runs in parallel in a second play. The playbook stays the contract for "what state these hosts hold."

### Inventory — NetBox dynamic + static fallback

Two inventory sources merge transparently:

- **NetBox dynamic** (primary). The `netbox.netbox.nb_inventory` plugin queries NetBox at runtime, projects hosts and groups from NetBox tags (`ansible:postgres`, `ansible:k3s-cp`, etc.). A jsonfile cache (24h TTL) sits in front of the live query; refresh cadence (4h, Semaphore template) is deliberately faster than TTL, so consumer playbooks always hit a warm cache between refreshes. The cache itself lives in an `emptyDir` mounted at the Semaphore container's `~/.cache/ansible/netbox-inventory` — pod restart loses the cache, and the next inventory build lands a cold-fetch from NetBox. `site.yml` asserts a minimum host count up front so a cold-fetch that returns zero hosts (NetBox blip during pod restart) fails loud instead of silently no-op'ing.
- **`hosts.yml`** (fallback). Hand-maintained, committed. Used when NetBox is down, on day-1 bootstrap before NetBox exists, and in disaster recovery.

NetBox supplies "what is this host?" — hostnames, IPs, group membership, primary interface metadata. File-based `group_vars/` and `host_vars/` supply "how should Ansible configure it?" — passwords (via Vault lookup), tunable knobs, per-environment overrides. The split keeps each store authoritative for its domain.

---

## Semaphore — scheduling Ansible

Semaphore runs in asgard K3s as a single-replica StatefulSet, Postgres-backed (on the Patroni VIP), Authentik-gated. Its job is to schedule and run Ansible playbooks against the homelab on a recurring cadence, post-deploy notifications to **Hermod**, and record an audit trail.

| Template | Cadence | What it runs | Notify on |
|---|---|---|---|
| `refresh-netbox-inventory` | Cron, every 4h | Wipes + rebuilds the NetBox inventory cache | Failure only |
| `asgard-drift-check` | Cron, every 6h | `ansible-playbook -i inventory/ site.yml --check --diff` | Changes detected or hard failure → `alert` tag. Clean → silent (logs to VictoriaLogs) |
| `asgard-apply` | Manual | `ansible-playbook -i inventory/ site.yml` | Failure → `critical` tag. Success → silent |
| `asgard-fleet-agents` | Cron, daily | Cluster-wide agent rollout (vlagent + zabbix-agent) | Failure → `alert` |

Semaphore authenticates to Vault via the `ansible-awx` AppRole — credentials live at Vault KV `secret/k8s/semaphore/vault-approle`, not in 1Password. The MacBook control node uses a separate `ansible-local` AppRole with its own storage convention. Both are described in the **Identity & secrets** subpage.

### The drift loop

Today the loop is detect → notify → operator decides → manually converge.

1. Every 6h, Semaphore runs `site.yml --check --diff` against the fleet.
2. If any host shows `changed=N`, Semaphore posts to Hermod with `tag: alert` and a summary of which tasks would change on which hosts.
3. Operator reads the alert in Discord, investigates the drift, and decides whether to converge.
4. To converge: trigger the `asgard-apply` template manually. Apply success is silent; apply failure pages with `tag: critical`.

The directional goal is auto-remediation — drift detected by the check loop converges automatically without operator involvement, and misbehaving VMs/LXCs get torn down and rebuilt from scratch rather than nursed back. Reaching that state needs every role to be reliably check-mode-faithful (so the alerts that auto-converge are real, not false positives) and enough test coverage that an unintended role change can't silently rewrite the fleet. Until those are in place, the human in the loop is the safety net.

### Drift-check caveats

`--check` mode is not as thorough as a real apply. It validates file existence but not ownership/mode, and some modules (`uri:` with POST, `command:`, `shell:`) are no-ops in check mode. Tasks downstream of a no-op `command:` can report "would change" on a converged host — false positives.

Each role owner is responsible for making the role check-mode-faithful enough that drift-check doesn't cry wolf. When false positives surface in `alert` notifications, the fix lives in the role, not the drift-check template.

---

## The change flow, end to end

A typical change touches more than one tool. The boundary keeps each step clean.

1. **Define the resource in Terraform** if there's an API to talk to (new LXC, new DNS record, new Vault path, new NetBox VM record, new Authentik group). `terraform apply` from the main checkout.
2. **Provision the OS via Ansible** if the resource is a node (`ansible-playbook playbooks/asgard-<service>.yml`). On a fresh LXC: `--tags baseline` as root first, then a full play as the `ansible` user.
3. **Deploy in-cluster workload via Flux** if the resource is a pod (commit to `k8s/...` + push). Flux reconciles on its own cadence — `flux reconcile kustomization <name>` if you want it now.
4. **Verify** via the per-tool check: `terraform plan` shows no drift, `ansible-playbook --check` shows no drift, `kubectl get ...` shows the workload Ready. Semaphore's 6h drift-check catches anything that drifted later.

The order matters. Trying to apply Flux changes before the underlying Terraform-provisioned secret exists fails predictably; same for Ansible against a host Terraform hasn't created yet.

---

## Failure surfaces worth knowing

- **Flux source controller can't reach GitHub.** Reconciliation stops at the last fetched commit. Running workloads continue. New commits queue until connectivity returns.
- **Flux reconcile of a chart fails.** The HelmRelease enters a degraded state; the prior revision continues running. Flux retries on the chart's `interval` cadence. Manual recovery is documented per chart class.
- **Semaphore down.** Drift-check stops running; nothing else breaks. The next scheduled apply also doesn't fire — but apply is manual anyway, so an operator trying to converge would notice immediately.
- **NetBox down.** Dynamic inventory falls through to the jsonfile cache (24h TTL) and then to `hosts.yml`. Drift-check + apply continue against the cached or static inventory. New hosts added in NetBox during the outage don't get picked up until NetBox returns or the cache is manually refreshed.
- **Terraform state lock orphaned.** S3 native locking (`use_lockfile = true`) — operator unlocks via `terraform force-unlock <lock-id>`. The S3 lock object is named per module, so an orphan only blocks the affected module.
- **Drift-check false positive storm.** A new role with check-mode-unfriendly tasks fires `alert` on every cron run. The fix is in the role (mark tasks `check_mode: false` for known-safe POSTs, or restructure to be idempotent in check mode), not the drift-check template. Until then, the alert is informational noise.

---

## See also

- **Identity & secrets** (this section) — Vault auth methods for Flux/ESO and Ansible/AppRole, Semaphore's Vault KV credential storage, secret rotation rules.
- **Compute & hypervisors** (this section) — Terraform Proxmox modules (`asgard-lxcs/`, `asgard-lxcs-root/`, `asgard-k3s/`) that this layer drives.
- **Observability** (this section) — Semaphore's stdout/stderr ships to VictoriaLogs via vlagent; the audit trail for every run lives there.
- **Procedures** — per-component apply and rollback runbooks, drift-investigation playbook, Flux disaster recovery.
- **Troubleshooting** — Flux Kustomization stuck in dry-run failure, immutable Job spec edits, ExternalSecret all-or-nothing resolve, Ansible `include_tasks` vs `import_tasks` tag propagation.
