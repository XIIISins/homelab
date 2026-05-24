<!-- docs/services/observability.md -->

# Observability stack (Phase 7, prep'd 2026-05-24)

VictoriaMetrics + VictoriaLogs in **jotunheim K3s** (Phase 6 prerequisite). Replaces Prometheus + Loki per the CLAUDE.md architectural invariant. Zabbix LXC (Phase 5h) handles infra-level alerting independently; the Phase-7 stack is for K8s-native metrics + logs queries via the native vmui + VictoriaLogs UI.

**No Grafana.** Dropped from scope after the initial sketch — VM ships vmui (interactive PromQL + chart playground) and VictoriaLogs ships its own LogsQL UI; the dashboard layer Grafana adds isn't load-bearing for our homelab workflow. Revisit if/when we need cross-source dashboards, annotation timelines, or a UI-driven dashboards-as-code pipeline.

**Status:** Manifests staged in `k8s/jotunheim/apps/` ready for deployment when Phase 6 (Jotunheim K3s bootstrap) lands. Nothing applied yet — jotunheim doesn't exist.

## Architecture

| Layer | Stack | Notes |
|-------|-------|-------|
| Cluster | jotunheim K3s | Phase 6 prerequisite. NOT asgard — monitoring + production share no failure domain. |
| Namespace | `monitoring` | Shared by both apps |
| Metrics store + UI | `victoria-metrics-single` chart `0.38.0` | vmsingle (not vmcluster — homelab scale doesn't need sharding). 50 Gi iSCSI PV, 6mo retention. vmui (built into vmsingle) is the dashboard layer. |
| Log store + UI | `victoria-logs-single` chart `0.12.5` | vlsingle, 50 Gi iSCSI PV, 6mo retention. Native VL UI for LogsQL queries. |
| Edge | HTTPRoute on niflheim Gateway → ClusterIP Services | Internal-only, no public ingress, no Cloudflare tunnel. Hostnames: `victoriametrics`/`victorialogs`.niflheim.xiiisins.com |

## Why VM + VL instead of Prometheus + Loki

- **Single-binary deployments** (vmsingle, vlsingle) at homelab scale — no operator, no CRDs, no cluster-mode complexity. Migration to cluster-mode is a values-block change if/when ingest grows past ~100k samples/sec.
- **Built-in UIs** — vmsingle ships `vmui` (interactive PromQL + chart playground) and vlsingle ships its own LogsQL UI. No separate dashboard layer (Grafana) needed for homelab-scale ad-hoc queries.
- **PromQL + LogsQL via the same vendor** — VL's LogsQL syntax is PromQL-flavored, so the operator's PromQL muscle memory carries over.
- **Resource footprint**: VictoriaMetrics is ~10× more efficient than Prometheus for the same ingest rate at homelab scale. Less RAM, less CPU, less disk.
- **Tradeoff**: smaller community + ecosystem than Prometheus. Most Helm charts that ship Prometheus ServiceMonitors require the Prometheus-Operator CRDs to be installed. VM's vmoperator provides equivalent CRDs (VMServiceScrape) but with a name change — adapter manifests sometimes needed.

## Cross-cluster ingress (asgard → jotunheim)

The Phase-7 stack lives in jotunheim, but the bulk of the workload runs in asgard. Need shippers on asgard that send metrics + logs to jotunheim:

| Shipper | Source | Sink | Manifests live in (planned) |
|---------|--------|------|-----------------------------|
| `vmagent` | Pod ServiceMonitors + node-exporter + various Prometheus-format endpoints across asgard | jotunheim's `victoriametrics-victoria-metrics-single-server.monitoring.svc.cluster.local:8429` via the jotunheim MetalLB IP exposed cluster-externally | `k8s/asgard/infrastructure/vmagent/` (TODO — defer until jotunheim's MetalLB pool + a VictoriaMetrics ingest LoadBalancer Service are known) |
| `vector` or `vmlogs-agent` | Container logs (`/var/log/containers/*.log`) + journal | jotunheim's VictoriaLogs ingest endpoint | `k8s/asgard/infrastructure/log-shipper/` (TODO — same trigger as vmagent) |

**Network path:** asgard worker IPs (`10.0.21.21/22/23` on VLAN 21 + `10.0.20.201/202/203` on VLAN 20) ↔ jotunheim worker IPs (`10.0.31.21/22/23` on VLAN 31 + `10.0.30.x` MetalLB). The two K3s clusters are on different VLANs but both reachable through UCG-Ultra's `Internal → Any: Allow` rule. No special firewall config needed; cross-cluster traffic uses Service-IPs through the LoadBalancer published by jotunheim.

**Standing question:** should we use TLS for the cross-cluster shipping? Homelab-internal, all on the trusted UCG-Ultra L3, so plaintext is acceptable for now. Revisit if/when a workload that handles sensitive logs (e.g. Authentik) is added.

## Secrets

None required for the Phase-7 baseline. VictoriaMetrics + VictoriaLogs expose unauthenticated HTTP on internal-only HTTPRoutes (the niflheim Gateway is itself internal-only via Traefik + AGH split-DNS). If we ever expose these via the midgard Gateway, add HTTP basic-auth or OIDC via Traefik middleware — pairs naturally with the existing Authentik OIDC infrastructure.

## Routes + URLs

| Service | Internal URL | Notes |
|---------|--------------|-------|
| VictoriaMetrics UI / vmui | `https://victoriametrics.niflheim.xiiisins.com/` | Native vmui + Prometheus-compatible API at `/api/v1/query`, `/api/v1/query_range`. Operator-facing. |
| VictoriaLogs UI | `https://victorialogs.niflheim.xiiisins.com/` | Native VL UI for log queries; LogsQL syntax. |

## Deployment dependencies (Phase 6 must land first)

Before this stack can deploy, jotunheim must have:

1. **K3s cluster** with all CPs + workers Ready.
2. **Flux + GitRepository** pointing at this repo, watching `k8s/jotunheim/` (the bootstrap is the same `flux bootstrap` step as asgard — separate deploy key).
3. **Sealed Secrets** (for any bootstrap secrets needed before ESO is up).
4. **External Secrets Operator** + `ClusterSecretStore` named `vault` pointing at asgard's Vault (`http://10.0.20.11:8200`). Jotunheim consumes asgard's Vault — no separate Vault server.
5. **Synology CSI** with the `synology-csi-iscsi-retain` StorageClass. May share Munin with asgard or use a separate iSCSI target — operator choice.
6. **MetalLB** with a pool from `10.0.30.0/24` (per CLAUDE.md VLAN table).
7. **tigera-operator + Calico** with the same MTU 1450 fix as asgard.
8. **Gateway API + Traefik** with a `niflheim` Gateway in the `traefik` namespace + a `websecure` listener with the jotunheim-domain wildcard cert. AGH split-DNS already routes `*.niflheim.xiiisins.com` → AGH VIP → resolves to jotunheim's Traefik LoadBalancer for any HTTPRoute hosts that we configure here.
9. **cert-manager** with DNS-01 issuer + the wildcard cert for `niflheim.xiiisins.com` (or jotunheim could use the same wildcard cert as asgard via cert-manager federation — operator decides).

## Operational notes

- **VictoriaMetrics retention** is set to `6` (months — chart accepts integer months OR strings like `1y`). Bump if disk allows. The chart's PVC is iSCSI; growing the LUN on Synology side + restarting the pod picks up new size.
- **VictoriaLogs retention** similarly 6 months. VL compresses well (~10× vs raw); 50 Gi is plenty for log query volume of a homelab.
- **vmoperator** (the VM-side equivalent of prometheus-operator) is NOT installed by default. ServiceMonitor → VMServiceScrape CRD bridges exist but require vmoperator. Phase 7b: install vmoperator + migrate any ServiceMonitor-emitting charts (cert-manager, ESO, etc.).
- **Alerts** — VictoriaMetrics has `vmalert` (commented out in helmrelease.yaml). The split with Zabbix: Zabbix watches infra (host up/down, disk full, etc.), vmalert watches application metrics (request rate, error %, latency). Enable vmalert + alertmanager when you have specific alerts to ship.

## Pending follow-ups

- **Phase 6** — jotunheim K3s bootstrap (cluster + infra stack). Blocking everything in this doc.
- **Phase 7 actual deploy** — once Phase 6 lands, configure jotunheim's ESO ClusterSecretStore, `flux bootstrap` + reconcile. No Vault writes required for the baseline (VM + VL run unauthenticated on internal-only HTTPRoutes).
- **vmagent + log-shipper on asgard** — once jotunheim's MetalLB IPs are pinned, deploy the asgard-side shippers. Manifests TBD in `k8s/asgard/infrastructure/{vmagent,log-shipper}/`.
- **vmoperator + ServiceMonitor → VMServiceScrape** — Phase 7b.
- **Cross-source dashboards** — if vmui + LogsQL UI's ad-hoc queries stop scaling for the kinds of analyses we want, revisit Grafana (was dropped from initial Phase 7 scope). Trigger: needing canned dashboards with annotations spanning metrics + logs together, OR needing to share read-only dashboards with non-operator users.

## See also

- CLAUDE.md "Architectural invariants → Services / placement"
- [`docs/services/asgard-k3s.md`](asgard-k3s.md) — pattern reference for K8s deployments
- [`docs/services/jotunheim-k3s.md`](jotunheim-k3s.md) — Phase 6 plan
- Phase 5h Zabbix LXC ([`docs/operations/build-sequence.md`](../operations/build-sequence.md)) — infra-level monitoring, complementary to this stack
