<!-- docs/outline/components-and-interactions/observability.md -->

# Observability

This subpage covers how the homelab sees itself — metrics, logs, and the alert pipeline that wakes the operator. Two stacks split the work along a clean failure-domain boundary: **VictoriaMetrics + VictoriaLogs** live inside asgard K3s and watch K8s-native concerns, while **Zabbix** runs on its own LXC outside K3s and watches everything below the cluster. The alert layer (**Hermod**) is a third LXC, deliberately outside K3s for the same reason: if K3s is what's on fire, the thing that pages about it must not depend on K3s being healthy.

The principle that shapes every choice on this page: a monitoring component cannot share a failure domain with what it monitors. K8s-native scrapes can live in K3s — if K3s is down, those metrics aren't meaningful anyway. Host-level monitoring and the notification pipe live outside K3s, so they survive the cluster's worst day and report on it.

---

## The split: in-cluster vs out-of-cluster

| Layer | Stack | Where it runs | Watches |
|---|---|---|---|
| K8s metrics + logs | VictoriaMetrics + VictoriaLogs (single-binary) | asgard K3s, `monitoring` namespace | Pods, kubelet/cAdvisor, cluster state, Flux reconciliation, the VM/VL stack itself |
| Host + LXC metrics + service templates | Zabbix 7.0 LTS | LXC 1102 (Hugin) on Urd, outside K3s | Every node, every LXC, every VM. PG/HAProxy/etcd/Proxmox/nginx/PHP-FPM templates. Patroni + etcd cluster health as defence-in-depth against PG-callback-driven Hermod notifications. |
| Notification fan-out | Hermod (AppriseAPI behind Caddy IP-allowlist) | LXC 1103 on Verd, outside K3s | Every alert producer above POSTs here; Hermod dispatches to Discord channels by tag. |

vmagent collects K8s-layer metrics; Zabbix collects host-layer metrics. No overlap — `container_*` and `kube_*` series stay in VictoriaMetrics; `system.cpu.util`, `vfs.fs.size`, `net.if.in` stay in Zabbix. Each store is authoritative for its layer.

---

## VictoriaMetrics + VictoriaLogs — K8s-native store

Two single-binary deployments in the `monitoring` namespace. No vmoperator CRDs yet — straight Helm charts.

- **vmsingle** (chart `victoria-metrics-single`) — 100 GiB iSCSI PV, 6-month retention. PromQL queries land here. The built-in **vmui** is the dashboard layer — interactive PromQL playground, chart panels, and a small set of canned dashboards mounted via `customDashboardsPath` ConfigMap.
- **vlsingle** (chart `victoria-logs-single`) — 50 GiB iSCSI PV, 30-day retention. LogsQL queries (PromQL-flavored) via the native VictoriaLogs UI.

There is **no Grafana**. vmui + the VictoriaLogs UI handle every dashboard and ad-hoc query the homelab needs at this scale. The revisit trigger is genuine cross-source dashboards (metrics + logs in a single panel) or wanting to share read-only dashboards with non-operator users.

### Ingress + auth

Both UIs sit behind Traefik on the niflheim Gateway, internal-only. AdGuard rewrites point `metric.niflheim.xiiisins.com` and `logs.niflheim.xiiisins.com` at the Traefik VIP. A Traefik ForwardAuth middleware against an Authentik proxy provider gates browser access; the VL HTTPRoute splits the `/insert/*` path prefix off as no-auth so off-cluster log shippers can POST without OIDC.

---

## vmagent — K8s metrics scraper

vmagent runs in the same namespace as vmsingle, scrapes the cluster, and remote-writes to vmsingle.

- **Kubelet self-metrics + cAdvisor** via the apiserver-proxy pattern. The scrape target is `kubernetes.default.svc:443` with a relabel that rewrites `__metrics_path__` to `/api/v1/nodes/<name>/proxy/metrics[/cadvisor]`. vmagent's default ClusterRole is missing `nodes/proxy`, so the HelmRelease grants it via `rbac.extraRules`.
- **kube-state-metrics** runs as its own Deployment in `monitoring` and exposes cluster-state metrics (`kube_pod_*`, `kube_node_*`, `kube_deployment_*`, etc.). vmagent scrapes it as a standard pod-annotation target.
- **30s scrape interval** across the board. Cardinality is tuned by what KSM emits + the relabel rules — no per-pod scrape annotations needed for the homelab-shape workloads.

**Label asymmetry.** cAdvisor's `container_*` series group by `instance` (the node), not `node` — the apiserver-proxy relabel sets `instance` and `kubernetes_io_hostname` but not `node`. KSM's `kube_*` series do have a `node` label set normally. Dashboards that join across both sources have to be aware of which key holds the node identifier.

---

## vlagent — log shipper, two deploy modes

All log shipping uses **vlagent**, the official VictoriaLogs project shipper. CLI-flag-only configuration (no YAML). Two deploy shapes:

| Mode | Where | How |
|---|---|---|
| K3s DaemonSet | One pod per asgard worker | `victoria-logs-collector` Helm chart, runs vlagent with `-kubernetesCollector` for native pod-log discovery. SELinux on RHEL workers requires `privileged: true` + `runAsUser: 0`. |
| systemd binary | Every off-cluster host — LXCs, K3s VMs, Proxmox hosts, PBS | Ansible `vlagent` role drops the binary + a systemd unit that reads from `/var/log/syslog` (Debian) or `/var/log/messages` (RHEL). |

### Ingress endpoints

The on-cluster DaemonSet writes directly to vlsingle via in-cluster Service DNS. Off-cluster shippers POST to vlsingle via an HTTPRoute on the niflheim Gateway — `https://logs.niflheim.xiiisins.com/insert/native`. K3s VM hosts are the awkward middle: their host network can't reach MetalLB VIPs from the same cluster, so a companion ClusterIP Service (`victorialogs-ingest`) is what they target, overridden in `group_vars/asgard_k3s.yml`.

### Debian rsyslog

Debian 13 ships journald-only; `/var/log/syslog` doesn't get written by default. The `vlagent` role installs and enables rsyslog on Debian hosts so the filelog input has content to read. Native journald collection in vlagent is upstream-pending.

---

## Zabbix — host, LXC, and service templates

Zabbix runs on a single LXC (Hugin, `10.0.11.21`) — Zabbix server, frontend, and a local agent2 instance for sanity. The database lives on the Patroni VIP like every other K3s consumer, so Postgres HA is shared with the rest of the homelab.

### Agent rollout

Every inventory host runs `zabbix-agent2`, deployed via an OS-aware Ansible role split (apt on Debian, dnf + SELinux boolean on RHEL). Hosts register declaratively — the `community.zabbix.zabbix_host` module creates host records via Zabbix's API with `delegate_to: hugin` and an `httpapi` connection. Inventory groups map to a hierarchical host-group taxonomy in Zabbix (`Asgard/K3s/CP`, `Asgard/LXCs/Postgres`, `Hypervisors/Proxmox`, etc.).

### Service templates

Round 2 of the Phase 8c rollout layered domain-specific monitoring on top of the agent baseline:

- **Postgres**: `PostgreSQL by Zabbix agent 2` template, agent2's PG plugin package, a `zbx_monitor` PG role with `pg_monitor` membership.
- **HAProxy + etcd**: HAProxy stats page on localhost:9000, etcd's `:2379/metrics` scraped server-side via the `Etcd by HTTP` template.
- **Proxmox**: PVE API user + token minted via `terraform/proxmox/zabbix-access/`, scraped via the `Proxmox VE by HTTP` template.
- **Nginx + PHP-FPM**: Hugin's own front-end via localhost stub_status + status/ping endpoints.

### Auth — SAML, not OIDC

Zabbix 7.0 has built-in SAML 2.0 support and uses it to federate with Authentik. The choice over OIDC + Traefik ForwardAuth is failure-domain-driven: Zabbix is the K3s-down emergency observability path, and its auth cannot itself depend on K3s being healthy. SAML against Authentik keeps the dependency direction sane (browser → Cloudflared → Traefik → Zabbix → SAML to Authentik), and a local-Admin break-glass account bypasses SAML for the `hugin-direct.niflheim.xiiisins.com` backdoor when Authentik itself is down.

---

## Hermod — notification fan-out

Hermod (LXC 1103, `10.0.11.22`) is the single notification endpoint for every alert producer in the homelab. Producers POST JSON to `/notify/<config-key>`; AppriseAPI fans the notification out to Discord webhooks based on a tag-driven routing config.

### The tag taxonomy

Severity is encoded in the **tag**, not the body. Tags map to Discord channels with Valkyrie display names.

| Tag | Response window | Producers | Channel |
|---|---|---|---|
| `critical` | Within minutes, even at 2am | Apply blew up, cluster quorum lost, service hard-unavailable, disk >90% | Hrist — `#infra-critical`, `@everyone` mention |
| `alert` | Within hours, business-day OK | Drift detected, single-node failure (cluster degraded), sustained resource load, disk 70–80% | Mist — `#infra-alerts`, no mention |
| `media` (future) | Whenever | Sonarr/Radarr release notifications | Ölrún — `#media`, no mention |
| _(no tag)_ | Producer bug — should have tagged | Any POST without a `tag` field | Hel — `#hermod-untagged` quarantine |

Severity drives Discord embed colour; tag drives which channel. Decoupling means a future `media` producer can reuse the same hub without sharing the infra-alerts channel. The untagged-quarantine channel exists because Apprise's default behaviour on a tagless notification is to match every URL whose `tag:` is unspecified — without an explicit catch-all, a stray producer bug would silently fan out. The fix is structural: every routed URL has an explicit `tag:`, and exactly one URL (the quarantine webhook) has no `tag:` clause.

### Producer wiring

- **Zabbix** → Hermod via a `community.zabbix.zabbix_mediatype` webhook + the stock "Report problems to Zabbix administrators" trigger action. Severity bitmask `Average+High+Disaster` routes to the `alert` tag.
- **Patroni** → Hermod via an `on_role_change` bash callback on every PG node. Promotion/demotion fires a `patroni` tag. The callback always exits 0 (a non-zero exit would deadlock Patroni's supervisor).
- **Semaphore (drift-check)** → Hermod via a webhook notifier on the template. Drift detected or hard failure fires `alert`; clean runs are silent. Manual `asgard-apply` failure fires `critical`.

---

## Failure surfaces worth knowing

- **VictoriaMetrics down.** K8s metrics ingestion stops; vmagent buffers in its remote-write queue (disk-backed, bounded). Pre-existing series remain queryable through whichever vmsingle replica returns first; on single-replica the queries fail. Zabbix is unaffected — separate stack.
- **VictoriaLogs down.** Log ingestion stops; vlagent buffers locally (DaemonSet pods + each off-cluster systemd unit). Pre-existing logs are unqueryable until vlsingle returns. Same independence from Zabbix.
- **Zabbix LXC down.** Host-level alerting stops. K8s-layer queries (VM/VL) keep working. Hermod still serves other producers — Patroni callbacks, Semaphore drift-check.
- **Hermod down.** Every producer's webhook POST fails. Caddy's local journal still records the attempt, so the producer-side audit trail survives. Discord channels go quiet — operator has no out-of-band alert path for as long as Hermod is out. Single-LXC, no HA; the directional answer is a second LXC + DNS round-robin or VRRP, deferred until a real outage justifies the work.
- **Cluster quorum lost.** Patroni's `on_role_change` callback fires from whichever surviving node was the previous leader. The Zabbix Patroni/etcd templates detect cluster-wide quorum loss as defence-in-depth — even if the callback fails to fire, Zabbix sees it.
- **Authentik down.** vmui and the VictoriaLogs UI 401 (their ForwardAuth dependency is broken). Zabbix UI falls back to its local `Admin` account or the backdoor hostname — neither path needs Authentik. Off-cluster log shippers continue to POST to vlsingle via the auth-bypassed `/insert/*` path; nothing about producer-side observability depends on Authentik.

---

## See also

- **Identity & secrets** (this section) — Authentik ForwardAuth for vmui + VL UI, native SAML for Zabbix, the Vault paths that hold each piece's secrets.
- **Edge** (this section) — niflheim Gateway HTTPRoutes for both UIs, Cloudflared tunnel route for Zabbix's external apex hostname, ClusterIP companion Service for K3s-VM-host log shipping.
- **GitOps & automation** (this section) — Semaphore is the first concrete `alert`-tag producer; drift-check failures arrive in Hermod via this path.
- **Services and purpose** — per-service notes on which Zabbix templates apply, what log streams each ships, what alert tags each producer wires.
- **Troubleshooting** — vmagent missing `nodes/proxy`, cAdvisor `node`-label asymmetry, vmui `unit` field as suffix not formatter, vlagent CLI-flag schema and `/insert/native` endpoint, Apprise YAML tag-as-dict-key shape.
