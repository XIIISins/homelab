<!-- docs/services/observability.md -->

# Observability stack (Phase 7a — deployed 2026-05-24/25)

VictoriaMetrics + VictoriaLogs in **asgard K3s**. Replaces Prometheus + Loki per the CLAUDE.md architectural invariant. Zabbix LXC ([Phase 7c](../operations/build-sequence.md), pending) handles infra-level alerting independently from any K3s state; the Phase-7 stack is for K8s-native metrics + logs queries via the native vmui + VictoriaLogs UI.

**No Grafana.** Dropped from scope after the initial sketch — VM ships vmui (interactive PromQL + chart playground) and VictoriaLogs ships its own LogsQL UI; the dashboard layer Grafana adds isn't load-bearing for our homelab workflow. Four custom dashboards shipped via `customDashboardsPath` ConfigMap. Revisit if/when we need cross-source dashboards, annotation timelines, or a UI-driven dashboards-as-code pipeline.

**Asgard, not jotunheim.** Earlier draft placed VM/VL in jotunheim per the "monitoring outside production" instinct; reversed before any deploy. Three reasons: jotunheim's deploy timeline is uncertain; the bulk of log/metric producers live in asgard so in-cluster ingest is shorter; failure-domain separation is already preserved by **Zabbix LXC** (fully outside K3s). Full rationale: [`docs/operations/decisions.md`](../operations/decisions.md) row "VM/VL placement — asgard".

**Status:** ✅ Phase 7a live. 23 off-cluster hosts shipping logs via vlagent systemd binary, K3s pod logs via DaemonSet, vmagent + KSM scraping K8s-layer metrics, vmui+VL UI behind Authentik ForwardAuth. Phase 7b (vm-operator migration) deferred.

## Architecture

| Layer | Stack | Notes |
|-------|-------|-------|
| Cluster | asgard K3s | Same cluster as the bulk of producers — shortest in-cluster ingest path. Failure-domain separation provided by Zabbix LXC (Phase 7c, fully outside K3s). |
| Namespace | `monitoring` | Shared by both apps + vmagent + KSM + log-collector DaemonSet |
| Metrics store + UI | `victoria-metrics-single` chart `0.38.0` → app v1.143.0 | vmsingle (not vmcluster — homelab scale doesn't need sharding). 100 Gi iSCSI PV, 6mo retention. vmui (built into vmsingle) is the dashboard layer. |
| Log store + UI | `victoria-logs-single` chart `0.12.5` → app v1.50.0 | vlsingle, 50 Gi iSCSI PV, **30d retention initially** (measure + resize before extending to the eventual 6mo target). |
| K8s metrics scraper | `victoria-metrics-agent` chart `0.39.0` | vmagent — kubelet `/metrics` + kubelet `/metrics/cadvisor` via apiserver-proxy + KSM scrape + pod-annotation scrape. Manifests at `k8s/asgard/infrastructure/vmagent/`. |
| K8s state metrics | `kube-state-metrics` chart `7.4.0` → app v2.19.0 | Cluster-state (pod phases, restarts, deploy replicas, etc.). Manifests at `k8s/asgard/infrastructure/kube-state-metrics/`. |
| K8s pod-log shipper | `victoria-logs-collector` chart `0.3.4` | vlagent DaemonSet (`-kubernetesCollector`, privileged + runAsUser=0 for RHEL SELinux). Manifests at `k8s/asgard/infrastructure/victoria-logs-collector/`. |
| Off-cluster log shipper | `ansible/roles/vlagent/` | vlagent systemd binary on LXCs + K3s VM hosts + Proxmox hosts + PBS. CLI-flag-only config; `/insert/native` protobuf endpoint. |
| Edge | HTTPRoute on niflheim Gateway → ClusterIP Services | Internal-only, no public ingress, no Cloudflare tunnel. Hostnames: `metric.niflheim.xiiisins.com` (vmui) + `logs.niflheim.xiiisins.com` (VL UI + ingest). |
| Auth | Authentik ForwardAuth via Traefik Middleware | Proxy providers (mode: forward_single) for vmui + VL UI. `/insert/*` PathPrefix on VL HTTPRoute bypasses auth so off-cluster shippers don't need OIDC. |
| Dashboards | 4 vmui custom dashboards | `customDashboardsPath` ConfigMap mounted into vmsingle. JSON files at `k8s/asgard/apps/victoriametrics/dashboards/`: 01-cluster-overview, 02-resource-usage, 03-flux-gitops, 04-victoria-self. |

## Why VM + VL instead of Prometheus + Loki

- **Single-binary deployments** (vmsingle, vlsingle) at homelab scale — no operator, no CRDs, no cluster-mode complexity. Migration to cluster-mode is a values-block change if/when ingest grows past ~100k samples/sec.
- **Built-in UIs** — vmsingle ships `vmui` (interactive PromQL + chart playground) and vlsingle ships its own LogsQL UI. No separate dashboard layer (Grafana) needed for homelab-scale ad-hoc queries.
- **PromQL + LogsQL via the same vendor** — VL's LogsQL syntax is PromQL-flavored, so the operator's PromQL muscle memory carries over.
- **Resource footprint**: VictoriaMetrics is ~10× more efficient than Prometheus for the same ingest rate at homelab scale.
- **Tradeoff**: smaller community + ecosystem than Prometheus. Most Helm charts that ship Prometheus ServiceMonitors require the Prometheus-Operator CRDs to be installed. VM's vmoperator provides equivalent CRDs (`VMServiceScrape`) but with a name change — adapter manifests sometimes needed. Phase 7b lifts this constraint.

## Ingest paths — actual deployed state

All shipping uses **vlagent** — the official VictoriaLogs project shipper. Picked over Vector and Fluent Bit per the 2026 upstream benchmark: vlagent at ~143k logs/s vs Fluent Bit at 31k vs Vector at 25k, 4-10× lower CPU, and Vector + Fluent Bit both have file-rotation correctness issues.

Three classes of producers feed VL + VM:

| Producer | Path | Shipper | Endpoint |
|----------|------|---------|----------|
| K3s pods (container logs) | vlagent DaemonSet on every asgard node, `-kubernetesCollector` for native pod-log discovery, ships to VL via in-cluster Service DNS | `victoria-logs-collector` chart `0.3.4` at `k8s/asgard/infrastructure/victoria-logs-collector/` | `http://victorialogs-victoria-logs-single-server.monitoring.svc.cluster.local:9428/insert/native` |
| Asgard LXCs (PG, HAProxy/etcd, Tailscale, AGH, Factorio, PBS — 14+ hosts) | vlagent systemd binary reads rsyslog'd `/var/log/syslog` (Debian) or `/var/log/messages` (RHEL) → VL ingest via HTTPRoute on the niflheim Gateway | `ansible/roles/vlagent/` | `https://logs.niflheim.xiiisins.com/insert/native` |
| Proxmox VMs (K3s nodes — Göndul/Hlökk/Sigrún, Einherjar-urd/verd/skuld) | Same as LXCs except endpoint is the in-cluster ClusterIP — K3s host network can't reach MetalLB VIPs | Same `ansible/roles/vlagent/` + `group_vars/asgard_k3s.yml` override | `http://10.43.14.105:9428/insert/native` (ClusterIP of `victorialogs-ingest` companion Service) |
| Proxmox hosts (Urd/Verd/Skuld) | Same vlagent role | `ansible/roles/vlagent/` with `-e ansible_user=root` | `https://logs.niflheim.xiiisins.com/insert/native` |

**vlagent is CLI-flag-only, not YAML-configured.** All config is passed as command-line flags. `-fileCollector.glob` + `-fileCollector.extraFields` are array flags — each appears multiple times, aligned by position to give per-file tags. The role builds the flag list via Jinja loop over `vlagent_log_inputs`.

**Debian 13 trixie ships journald-only.** The role installs rsyslog on Debian hosts so vlagent's filelog input has content to read. Native journald collection is upstream-pending ([VictoriaLogs issue #1274](https://github.com/VictoriaMetrics/VictoriaLogs/issues/1274)).

**`/insert/native` not `/insert/jsonline`.** vlagent's default protobuf protocol matches `/insert/native`. The HTTPRoute's `/insert/*` PathPrefix catches both endpoints; pick `native` for efficiency.

## K8s metrics path

- **vmagent** scrapes kubelet `/metrics` (kubelet self-metrics) + kubelet `/metrics/cadvisor` (per-container CPU/memory/network) via the apiserver-proxy pattern. Relabel sets `__address__: kubernetes.default.svc:443` + `__metrics_path__: /api/v1/nodes/<name>/proxy/metrics/cadvisor`.
- **kube-state-metrics** provides cluster-state metrics (`kube_pod_*`, `kube_node_*`, `kube_deployment_*`, etc.). vmagent scrapes it as a pod-annotation target.
- **vmagent's default ClusterRole is missing `nodes/proxy`** — added via `rbac.extraRules` in the HelmRelease. Without it, every cAdvisor scrape gets 403 and `container_*` metrics never reach VM.
- **cAdvisor metrics group by `instance`, not `node`.** The chart's apiserver-proxy relabel sets the node identifier in `instance` (and `kubernetes_io_hostname`). KSM metrics DO have `node` set; cAdvisor is the asymmetric one.

**Zabbix LXC** (Phase 7c, pending — see [`docs/services/zabbix.md`](zabbix.md)) covers host/LXC-level metrics — CPU/memory/disk/network at the OS layer + service-level Zabbix templates. Clean split: vmagent for the K8s layer, Zabbix for everything below the K8s layer. No duplication.

## vmui dashboards

Shipped via `customDashboardsPath`-mounted ConfigMap at `k8s/asgard/apps/victoriametrics/dashboards/`:

| File | Title | Notes |
|------|-------|-------|
| `01-cluster-overview.json` | Cluster overview | Node + pod counts, pod phases, restart rates, scrape target health |
| `02-resource-usage.json` | Resource usage (CPU / memory) | Per-node + per-namespace CPU/memory, top-10 pods, pending pods. Memory in GiB (PromQL-side conversion). |
| `03-flux-gitops.json` | Flux GitOps | Reconcile rates, suspended resources, last-attempt status |
| `04-victoria-self.json` | Victoria stack self-metrics | VL/VM ingest rates, disk usage, vmagent remote_write queue depth, scrape success % |

**vmui's `unit` field is a label suffix, not a formatter** — no auto B/KiB/MiB/GiB scaling. Byte panels use PromQL-side `/1024^n` with a fixed unit suffix per panel.

**vmui reads `customDashboardsPath` at startup only.** Updating the mounted ConfigMap doesn't hot-reload — bounce vmsingle (`kubectl rollout restart sts ...`) after dashboard JSON changes.

## Deployment dependencies — actual reused infrastructure

1. **Synology CSI** + `synology-csi-iscsi-retain` StorageClass — vmsingle 100Gi + vlsingle 50Gi PVs. VL needed a chown init container (fsGroup quirk; chart values key is `server.initContainers`).
2. **Gateway API + Traefik** + the `niflheim` Gateway. Two HTTPRoutes:
   - `metric.niflheim.xiiisins.com` → vmsingle (auth via Authentik ForwardAuth middleware)
   - `logs.niflheim.xiiisins.com` → VL with split rules: `/insert/*` no-auth (for shippers), `/` auth (for UI)
3. **cert-manager** + the wildcard `*.niflheim.xiiisins.com` cert (already mounted on the niflheim Gateway).
4. **AGH split-DNS** — `terraform/adguard/rewrites.tf` adds `metric.niflheim.xiiisins.com` + `logs.niflheim.xiiisins.com` → Traefik VIP `10.0.20.10`.
5. **K3s `coredns-custom`** — handles the in-cluster-pod → K8s-fronted-FQDN class for pods that need to reach `authentik.midgard.xiiisins.com` etc. for OIDC backchannel.
6. **Authentik** — two proxy providers (`mode: forward_single`) declared in `terraform/authentik/observability.tf`. `monitoring-admins` group in `terraform/authentik/groups.yaml` gates access.
7. **Companion ClusterIP Service** `victorialogs-ingest` (the chart's primary is headless) — K3s host network can't reach MetalLB VIPs, so the K3s VM hosts target the ClusterIP via `group_vars/asgard_k3s.yml` override.

## Operational notes

- **VictoriaMetrics retention** is set to `6` months. Bump if disk allows.
- **VictoriaLogs retention** is 30d initially. Measure week-of-data baseline, then resize the LUN + bump to 6mo target.
- **vm-operator** is NOT installed. Phase 7b refactors the Helm charts to CRD-based deploys (`VLSingle`, `VMSingle`, `VMAgent` CRDs + `VMServiceScrape` for ServiceMonitor-emitting charts). Deferred until ServiceMonitor-emitting charts land or alerting becomes a need.
- **Alerts** — vmalert NOT enabled. Zabbix watches infra (host up/down, disk full, etc.); vmalert would watch application metrics. Enable when a specific alert use case lands.
- **vmagent + kube-state-metrics** scrape every 30s. Tune via the HelmRelease `staticScrapeConfig` if cardinality grows past comfort.

## Pending follow-ups

- **Phase 7b — vm-operator migration**. Switches VL/VM/vmagent lifecycle from raw Helm to `VLSingle`/`VMSingle`/`VMAgent` CRDs. Also brings `ServiceMonitor` → `VMServiceScrape` conversion for cert-manager / ESO / other ServiceMonitor-emitting charts that we currently don't scrape.
- **VL retention bump** — 30d → 6mo once disk-use baseline is known.
- **VMAlert + alert routing** — when a paged-alert use case beyond Zabbix's coverage lands.
- **Cross-source dashboards** — Grafana revisit trigger: needing canned dashboards with annotations spanning metrics + logs, OR needing to share read-only dashboards with non-operator users.

## See also

- [`docs/incidents/2026-05-24-phase-7-observability.md`](../incidents/2026-05-24-phase-7-observability.md) — Phase 7a deploy retrospective (12 findings)
- CLAUDE.md "Architectural invariants → Services / placement" + "vmui / VictoriaMetrics dashboards" gotcha section
- [`docs/services/asgard-k3s.md`](asgard-k3s.md) — pattern reference for K8s deployments
- Phase 7c Zabbix LXC ([`docs/services/zabbix.md`](zabbix.md), [`docs/operations/build-sequence.md`](../operations/build-sequence.md)) — infra-level monitoring, complementary to this stack
