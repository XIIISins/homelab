<!-- docs/incidents/2026-05-24-phase-7-observability.md -->

# 2026-05-24/25 — Phase 7a: VL + VM + vmagent + vlagent end-to-end

**Scope**: full Phase 7a deployment in one pass — VictoriaLogs + VictoriaMetrics in asgard K3s, K8s pod-log DaemonSet, vmagent + kube-state-metrics for K8s-layer metrics, vlagent systemd binary on 23 off-cluster hosts (LXCs + K3s VM hosts + Proxmox hosts + PBS), Authentik OIDC for vmui/VL-UI, four vmui custom dashboards. Manifests were prepped earlier (decisions captured 2026-05-24 pre-flight); this work landed the actual deploys + surfaced everything below.

## Trigger

Operator green-lit autonomous deploy ("Can I let you setup VL/VM alone without my input?"). No specific incident kicked it off — straight Phase 7 build-out following the asgard-not-jotunheim + no-Grafana decisions made in [`docs/operations/decisions.md`](../operations/decisions.md).

## Findings — landed as gotchas / decisions

### 1. VL fsGroup quirk on iSCSI — recurrence

Synology CSI iSCSI volumes don't honour `securityContext.fsGroup` (long-known pattern documented for Vault + Authentik Redis). vlsingle hit the same wall on first deploy: pod crashed at startup trying to write to `/storage` owned by root. **Fix**: chown init container as UID 0 (`busybox` → `chown -R 1000:1000 /storage`). **Wrinkle**: the chart's values key is `server.initContainers` (NOT `extraInitContainers` as I first tried — silently ignored, no error). Pattern is in CLAUDE.md as the universal CSI quirk; the chart-specific key name is a new sub-gotcha.

### 2. vlagent is CLI-flag-only — no YAML config

Built the Ansible role assuming a `vlagent.yaml` like other VictoriaMetrics shippers. First run errored: `-filelog.config: flag provided but not defined`. **Reality**: vlagent takes ALL config via CLI flags. `-fileCollector.glob` + `-fileCollector.extraFields` are array flags — pass each multiple times, they align by position to give per-file tags `{"host":"<host>","service":"<service>"}`. Rewrote `templates/vlagent.service.j2` to build the flag list via Jinja loop over `vlagent_log_inputs`. Removed the YAML template entirely.

### 3. Debian 13 trixie ships journald-only — no rsyslog

First vlagent deploy on Debian 13 hosts shipped nothing — `/var/log/syslog` was an empty stale file. trixie's default install has no rsyslog; journald-only is the new default. Added rsyslog install + `systemctl enable rsyslog` to the role's Debian apt path so the filelog input actually has content. Native journald collection is upstream-pending ([VictoriaLogs issue #1274](https://github.com/VictoriaMetrics/VictoriaLogs/issues/1274)); rsyslog bridge is the tactical answer.

### 4. vlagent's default protobuf doesn't match `/insert/jsonline`

Set `vlagent_remote_write_url` to `…/insert/jsonline` initially (matching the chart's documented endpoint). vlagent sent its native protobuf format; VL parsed it as malformed JSON and dropped every entry. **Fix**: switched to `/insert/native` — vlagent's native protobuf protocol, more efficient + matches what vlagent actually sends by default. The HTTPRoute's `/insert/*` PathPrefix catches both endpoints, so the change was purely the URL.

### 5. K3s host network can't reach MetalLB-announced VIPs

K3s VM hosts (Göndul/Hlökk/Sigrún + Einherjar-*) needed to ship their journald logs to VL. The natural target — Traefik VIP at `10.0.20.10` — was unreachable from the host network namespace (curl hung, no SYN-ACK). MetalLB L2 ARPs for the VIP, but the kernel on the announcing node has no DNAT to the backend; packets from the same host's main namespace can't loop back through the kube-proxy chain to the actual pod. **Fix**: companion `ClusterIP` Service `victorialogs-ingest` (the chart's primary is headless), point K3s VMs at the ClusterIP `10.43.14.105:9428` via `group_vars/asgard_k3s.yml`. ClusterIP IS reachable from K3s hosts (it's how `kubectl` itself works). Generalizable: any host-network process on a K3s node needing an in-cluster Service can't use MetalLB VIPs — use ClusterIP.

### 6. RHEL SELinux confines log-collector DaemonSet containers

vlagent DaemonSet (the `victoria-logs-collector` chart) CrashLoopBackOff'd on every RHEL worker. First fix: `runAsUser: 0` (permission-denied on `/var/lib/vlagent`). Still failed — SELinux confined the container even as root, denying access to `/var/log/pods`. **Fix**: `privileged: true` + `runAsUser: 0` + `fsGroup: 0` in values. The DaemonSet's whole reason to exist is reading host log files — accepting privileged on this specific workload is the right tradeoff.

### 7. Authentik embedded outpost `config.authentik_host` bypasses brand resolution

After cutover to `authentik.midgard.xiiisins.com` (brand blueprint updated, default flipped), the vmui/VL-UI login buttons STILL redirected to `authentik.niflheim.xiiisins.com` (which doesn't resolve externally). Brand resolution is per-domain — but the embedded outpost has its OWN `config.authentik_host` field cached at startup that bypasses brand entirely. **Fix**: API PATCH the outpost UUID setting `config.authentik_host = https://authentik.midgard.xiiisins.com` + restart `authentik-server` pods. Made it declarative: blueprint `02-embedded-outpost.yaml` pins `config.authentik_host` so cluster rebuild reconstitutes it.

### 8. vmagent's default ClusterRole missing `nodes/proxy`

After deploying vmagent + KSM, the `container_cpu_*` + `container_memory_*` metrics never appeared in VM. Logs showed 403 on every kubelet/cAdvisor scrape: `nodes "<name>" is forbidden: ... cannot get resource "nodes/proxy" in API group ""`. The chart's default ClusterRole has `nodes` but NOT `nodes/proxy` — the apiserver-proxy scrape pattern (`__address__: kubernetes.default.svc:443` + `__metrics_path__: /api/v1/nodes/<name>/proxy/metrics/cadvisor`) needs the subresource. **Fix**: `rbac.extraRules` in the HelmRelease values granting `nodes/proxy` + `nodes/metrics`. 161 `container_cpu_usage_seconds_total` series appeared on the next scrape cycle. Dashboard 02 populated.

### 9. vmui's `unit` field is a label suffix, not a formatter

Added `"unit": "bytes"` to byte panels expecting auto-scaling like Grafana's `bytes` unit. vmui rendered raw bytes (`11,580,000,000 bytes`). Read the v1.143.0 source — `app/vmui/.../utils/uplot/helpers.ts::formatTicks` appends the `unit` string verbatim after `toLocaleString("en-US")`. No B/KiB/MiB/GiB logic exists. **Fix**: PromQL-side `/1024^n` with fixed-unit-per-panel labels (GiB for node/namespace sums, MiB for pod-level).

### 10. cAdvisor `container_*` metrics group by `instance`, not `node`

After fixing the byte formatting, the "Cluster CPU by node" + "Cluster memory by node" panels only drew one line. Inspection: `sum by(node)` collapsed everything because cAdvisor metrics scraped via the apiserver-proxy pattern have an EMPTY `node` label — the chart's default relabel sets the node identifier in `instance` (and `kubernetes_io_hostname`), not `node`. KSM metrics (`kube_*`) DO have `node` set normally; this asymmetry is cAdvisor-specific. **Fix**: `sum by(instance)` instead. 6 lines (gondul/hlokk/sigrun + einherjar-urd/verd/skuld) appeared.

### 11. PBS apt-cache update 401 without subscription

`apt update` on the PBS LXC failed with 401 Unauthorized on the Proxmox Enterprise repo (homelab has no subscription). Baseline role's `update_cache: yes` propagated the failure. **Fix**: split the apt-cache-update into a separate task with `failed_when: false` + use `update_cache: false` on the install task. Trades clean-update-on-every-run for "the role works on subscription-less hosts." Reasonable for homelab.

### 12. VL HelmRelease pending-install lockout

Mid-iteration, VL got stuck in `pending-install` state — Flux couldn't roll back because there was no previous rollback target. Manifestation: `MissingRollbackTarget` on the HelmRelease. **Fix**: `helm uninstall victorialogs -n monitoring` outside Flux → `flux reconcile hr victorialogs -n monitoring` to reinstall fresh. Generic pattern for any first-deploy-that-failed scenario; already captured in CLAUDE.md under Flux/Helm/Kustomize as the recovery procedure but worth noting it fired here.

## Resolution

All twelve findings closed in-session. Twenty-three off-cluster hosts shipping logs via vlagent systemd binary at the end of the run. K8s pod logs flowing via the DaemonSet. vmagent + KSM scraping K8s-layer metrics; 161 cAdvisor series, 20,757 VM series total. Four vmui dashboards (cluster-overview, resource-usage, flux-gitops, victoria-self) live. Authentik OIDC working for vmui + VL UI login.

Phase 7b (vm-operator migration with `VLSingle` / `VMSingle` CRDs + `VMServiceScrape`) deferred — no current need; Helm chart deploys are stable. Revisit when ServiceMonitor-emitting charts land or alerting becomes a need.

## Root-cause patterns

- **Long-tail of "default chart values assume a more permissive environment."** vmagent RBAC (missing `nodes/proxy`), VL fsGroup-on-CSI, log-collector DaemonSet vs RHEL SELinux — all chart defaults built for vanilla upstream K8s where these constraints don't exist. Pattern: every new chart, audit its assumed environment vs ours (SELinux enforcing, Synology CSI iSCSI-only, multi-homed workers, custom RBAC needs) before declaring deploy done.
- **Documentation drift on shipper config formats.** vlagent was advertised in some upstream docs as YAML-configurable; in v1.50.0 it's CLI-flags-only. Don't trust shipper config examples without grepping the actual binary's `--help`.
- **Label-shape assumptions across metric sources.** cAdvisor `container_*` vs KSM `kube_*` use different label keys for the same concept (node identifier). Inspecting actual labels via `/api/v1/labels?match[]=...` is faster than guessing.
- **Auth domain artifacts cached in unexpected places.** Authentik's per-outpost `config.authentik_host` overrides brand resolution. Class: any service holding cached "where am I exposed" state separate from its declarative source needs explicit re-pinning at config change time.

## Decision tie-backs

- **Phase 7 asgard-not-jotunheim placement** ([decisions row 2026-05-24](../operations/decisions.md)) — validated. In-cluster ingest path was useful (lower latency, no cross-VLAN routing for the bulk of producers). Failure-domain separation via Zabbix LXC remains the right move.
- **No Grafana** ([decisions row 2026-05-24](../operations/decisions.md)) — validated. vmui + VL UI cover the ad-hoc query workflow. Custom dashboards via `customDashboardsPath` ConfigMap work; the JSON schema is limited (no unit formatters, no annotations) but it's enough.
- **vlagent over Vector/Fluent Bit** ([CLAUDE.md services/placement invariant](../../CLAUDE.md)) — validated. CLI-flag-only config was a sharp edge but the role hides it; the actual ingest is stable + low-CPU.

## What "good" looks like next

- Phase 7b vm-operator refactor when a chart starts emitting ServiceMonitor CRs (currently we'd have to translate by hand).
- VMAlert + alert routing when we want paged alerts beyond Zabbix's coverage.
- VL retention bump from 30d → 6mo once measured disk-use pattern is known (current 50Gi PV is sized for 30d at observed ingest rate; need a week of data to extrapolate).
