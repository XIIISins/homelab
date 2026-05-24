<!-- docs/services/observability.md -->

# Observability stack (Phase 7, prep'd 2026-05-24)

VictoriaMetrics + VictoriaLogs in **asgard K3s**. Replaces Prometheus + Loki per the CLAUDE.md architectural invariant. Zabbix LXC (Phase 5h) handles infra-level alerting independently from any K3s state; the Phase-7 stack is for K8s-native metrics + logs queries via the native vmui + VictoriaLogs UI.

**No Grafana.** Dropped from scope after the initial sketch — VM ships vmui (interactive PromQL + chart playground) and VictoriaLogs ships its own LogsQL UI; the dashboard layer Grafana adds isn't load-bearing for our homelab workflow. Revisit if/when we need cross-source dashboards, annotation timelines, or a UI-driven dashboards-as-code pipeline.

**Asgard, not jotunheim.** Earlier draft placed VM/VL in jotunheim per the "monitoring outside production" instinct; reversed before any deploy. Three reasons: jotunheim's deploy timeline is uncertain; the bulk of log/metric producers live in asgard so in-cluster ingest is shorter; failure-domain separation is already preserved by **Zabbix LXC** (fully outside K3s). Full rationale: [`docs/operations/decisions.md`](../operations/decisions.md) row "VM/VL placement — asgard".

**Status:** Manifests staged in `k8s/asgard/apps/` ready for deployment alongside the existing apps. Nothing applied yet — pending the operator's deploy schedule.

## Architecture

| Layer | Stack | Notes |
|-------|-------|-------|
| Cluster | asgard K3s | Same cluster as the bulk of producers — shortest in-cluster ingest path. Failure-domain separation provided by Zabbix LXC (Phase 5h, fully outside K3s). |
| Namespace | `monitoring` | Shared by both apps |
| Metrics store + UI | `victoria-metrics-single` chart `0.38.0` → app v1.143.0 | vmsingle (not vmcluster — homelab scale doesn't need sharding). 100 Gi iSCSI PV, 6mo retention. vmui (built into vmsingle) is the dashboard layer. |
| Log store + UI | `victoria-logs-single` chart `0.12.5` → app v1.50.0 | vlsingle, 50 Gi iSCSI PV, **30d retention initially** (measure + resize before extending to the eventual 6mo target — VL pod-log ingest can spike disk use fast once the cluster-wide DaemonSet lights up). Native VL UI for LogsQL queries. |
| Edge | HTTPRoute on niflheim Gateway → ClusterIP Services | Internal-only, no public ingress, no Cloudflare tunnel. Hostnames: `victoriametrics`/`victorialogs`.niflheim.xiiisins.com |

## Why VM + VL instead of Prometheus + Loki

- **Single-binary deployments** (vmsingle, vlsingle) at homelab scale — no operator, no CRDs, no cluster-mode complexity. Migration to cluster-mode is a values-block change if/when ingest grows past ~100k samples/sec.
- **Built-in UIs** — vmsingle ships `vmui` (interactive PromQL + chart playground) and vlsingle ships its own LogsQL UI. No separate dashboard layer (Grafana) needed for homelab-scale ad-hoc queries.
- **PromQL + LogsQL via the same vendor** — VL's LogsQL syntax is PromQL-flavored, so the operator's PromQL muscle memory carries over.
- **Resource footprint**: VictoriaMetrics is ~10× more efficient than Prometheus for the same ingest rate at homelab scale. Less RAM, less CPU, less disk.
- **Tradeoff**: smaller community + ecosystem than Prometheus. Most Helm charts that ship Prometheus ServiceMonitors require the Prometheus-Operator CRDs to be installed. VM's vmoperator provides equivalent CRDs (VMServiceScrape) but with a name change — adapter manifests sometimes needed.

## Ingest paths

All shipping uses **vlagent** — the official VictoriaLogs project shipper. Picked over Vector and Fluent Bit per the 2026 upstream benchmark: vlagent at ~143k logs/s vs Fluent Bit at 31k vs Vector at 25k, 4-10× lower CPU, and Vector + Fluent Bit both have file-rotation correctness issues (incomplete records during log rotation; Vector has an FD leak under load). vlagent's data-loss surface is upstream-managed by the same team that runs VL.

Three classes of producers feed VL + VM:

| Producer | Path | Shipper |
|----------|------|---------|
| K3s pods (container logs, application metrics, ServiceMonitor-emitting charts) | vlagent DaemonSet on every asgard node uses `-kubernetesCollector` for native pod-log discovery; ships to VL via in-cluster Service DNS | `victoria-logs-collector` chart `0.3.4` — manifests at `k8s/asgard/infrastructure/victoria-logs-collector/` (Phase 7 prep, TBD) |
| Asgard LXCs (PG, HAProxy/etcd, Tailscale, AGH, Factorio, **Zabbix** — Phase 5h, etc.) | vlagent systemd binary reads journald + per-service log files → VL ingest via HTTPRoute on the niflheim Gateway | `ansible/roles/vlagent/` (Phase 7 prep, TBD) |
| Proxmox VMs (K3s nodes themselves — Göndul/Hlökk/Sigrún, Einherjar-urd/verd/skuld) | Same as LXCs — vlagent via Ansible reading journald + K3s-server-or-agent logs | Same `ansible/roles/vlagent/` |

**In-cluster vs off-cluster ingest endpoint.** Asgard pods send to VL via in-cluster Service DNS (`victorialogs-victoria-logs-single-server.monitoring.svc.cluster.local:9428`). LXCs + VMs that aren't K8s pods can't resolve cluster DNS, so they need an off-cluster endpoint. **Picking HTTPRoute on the niflheim Gateway** (e.g. `vl-ingest.niflheim.xiiisins.com`) — cleaner than a MetalLB LoadBalancer because it: (a) reuses the existing wildcard TLS cert + Gateway plumbing, (b) gives a stable FQDN target instead of a MetalLB IP, (c) matches the "K8s-fronted internal FQDN" pattern already in use (NetBox, Authentik). The vmui + VL UI HTTPRoutes stay for human use.

**Native journald** in vlagent is proposed-but-not-yet-merged ([VictoriaLogs issue #1274](https://github.com/VictoriaMetrics/VictoriaLogs/issues/1274)). Bridge for LXCs/VMs is either: (a) `systemd-journal-upload` → vlagent, or (b) vlagent's filelog input scraping `/var/log/journal/*.journal` directly. Pick whichever lands first when the role is implemented.

**Rollout priority** (per the operator's 5h follow-on direction):

1. **Zabbix LXC** — first non-system workload to plug in. Its logs flow to VL the same as any other LXC; sets the pattern + validates the agent role.
2. **VL + VM themselves** — VL's pod logs go via the DaemonSet, VM's similarly. (Recursive but useful: "what did VL just complain about?" answerable inside VL.)
3. **Other workloads** — cascade out: remaining LXCs, K3s pods, Proxmox VMs. Order doesn't matter much past #1.

**Standing question:** TLS for the in-homelab shipping? The HTTPRoute path is already TLS (Traefik wildcard cert). For in-cluster Service-DNS path, plaintext is fine. Add Basic Auth via Traefik middleware on the ingest HTTPRoute if we want auth on the off-cluster path.

## Secrets

None required for the Phase-7 baseline. VictoriaMetrics + VictoriaLogs expose unauthenticated HTTP on internal-only HTTPRoutes (the niflheim Gateway is itself internal-only via Traefik + AGH split-DNS). If we ever expose these via the midgard Gateway, add HTTP basic-auth or OIDC via Traefik middleware — pairs naturally with the existing Authentik OIDC infrastructure.

## Routes + URLs

| Service | Internal URL | Notes |
|---------|--------------|-------|
| VictoriaMetrics UI / vmui | `https://victoriametrics.niflheim.xiiisins.com/` | Native vmui + Prometheus-compatible API at `/api/v1/query`, `/api/v1/query_range`. Operator-facing. |
| VictoriaLogs UI | `https://victorialogs.niflheim.xiiisins.com/` | Native VL UI for log queries; LogsQL syntax. |

## Deployment dependencies

Asgard already has everything VM/VL need — the stack lands on existing infrastructure with no Phase-6 blocker. Specifically reused:

1. **Synology CSI** + `synology-csi-iscsi-retain` StorageClass for VM/VL PVs (50 Gi each).
2. **Gateway API + Traefik** + the `niflheim` Gateway already serving NetBox / authentik / etc. — VM/VL get HTTPRoutes attached to the same Gateway.
3. **cert-manager** + the wildcard `*.niflheim.xiiisins.com` cert (already mounted on the niflheim Gateway).
4. **AGH split-DNS** for `*.niflheim.xiiisins.com` — adds `victoriametrics.niflheim.xiiisins.com` + `victorialogs.niflheim.xiiisins.com` rewrites pointing at the Traefik VIP.
5. **MetalLB** — additional LoadBalancer Service for VL ingest (off-cluster shippers can't resolve in-cluster Service DNS). Pick an IP from the asgard MetalLB pool `10.0.20.11–.99`.

NO Vault writes required for the baseline. VM + VL ship with unauthenticated HTTP on internal-only HTTPRoutes. If/when we add TLS to the in-homelab shipping path or wire OIDC for the UIs, that adds Vault paths — defer until needed.

## Operational notes

- **VictoriaMetrics retention** is set to `6` (months — chart accepts integer months OR strings like `1y`). Bump if disk allows. The chart's PVC is iSCSI; growing the LUN on Synology side + restarting the pod picks up new size.
- **VictoriaLogs retention** similarly 6 months. VL compresses well (~10× vs raw); 50 Gi is plenty for log query volume of a homelab.
- **vm-operator** (the VM-side equivalent of prometheus-operator) is NOT installed by default. CRDs `VLSingle` / `VMSingle` / `VMServiceScrape` etc. exist but require vm-operator. The deprecated `VLogs` CRD has been replaced by `VLSingle` in current versions. Phase 7b: install vm-operator + migrate from the Helm charts to the CRD-based deploys + migrate any ServiceMonitor-emitting charts (cert-manager, ESO, etc.) to VMServiceScrape.
- **Alerts** — VictoriaMetrics has `vmalert` (commented out in helmrelease.yaml). The split with Zabbix: Zabbix watches infra (host up/down, disk full, etc.), vmalert watches application metrics (request rate, error %, latency). Enable vmalert + alertmanager when you have specific alerts to ship.

## Pending follow-ups

- **Phase 7 actual deploy** — `flux reconcile` on asgard once the operator schedules it. NO Vault writes required for the baseline.
- **Vector DaemonSet on asgard for K8s container logs** — manifests at `k8s/asgard/infrastructure/vector/` (Phase 7 prep, TBD).
- **Vector Ansible role for LXCs + VMs** — at `ansible/roles/vector/` (Phase 7 prep, TBD). Zabbix LXC first per the operator's priority.
- **vm-operator + Helm → CRD migration** — Phase 7b. Switches the VL/VM/vmagent lifecycle from Helm-managed Deployments to `VLSingle`/`VMSingle`/`VMAgent` CRDs. Also brings ServiceMonitor → VMServiceScrape conversion for cert-manager / ESO / other ServiceMonitor-emitting charts.
- **Cross-source dashboards** — if vmui + LogsQL UI's ad-hoc queries stop scaling for the kinds of analyses we want, revisit Grafana (was dropped from initial Phase 7 scope). Trigger: needing canned dashboards with annotations spanning metrics + logs together, OR needing to share read-only dashboards with non-operator users.

## See also

- CLAUDE.md "Architectural invariants → Services / placement"
- [`docs/services/asgard-k3s.md`](asgard-k3s.md) — pattern reference for K8s deployments
- [`docs/services/jotunheim-k3s.md`](jotunheim-k3s.md) — Phase 6 plan
- Phase 5h Zabbix LXC ([`docs/operations/build-sequence.md`](../operations/build-sequence.md)) — infra-level monitoring, complementary to this stack
