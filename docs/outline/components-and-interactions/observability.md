<!-- docs/outline/components-and-interactions/observability.md -->

# Observability

Knowing what the homelab is doing and being told when it is broken. The design splits by failure domain: in-cluster metrics and logs run in Kubernetes; host and LXC monitoring runs outside it, so an outage of the cluster is still reported.

![Observability: VictoriaMetrics and VictoriaLogs, no Grafana](/api/attachments.redirect?id=260fe7c8-75f5-440c-a1ea-424916a2c3f1)

---

## The split

| Concern | Tool | Where it runs | Why there |
|---|---|---|---|
| Kubernetes metrics | **VictoriaMetrics** + vmagent + kube-state-metrics | asgard K3s, `monitoring` namespace | Most producers are in the cluster; shortest ingest path |
| Logs, everywhere | **VictoriaLogs** + vlagent shippers | asgard K3s | One searchable place for every host's logs |
| Hosts, LXCs, services | **Zabbix** (server "Hugin") | LXC 1102 on Urd, **outside K3s** | If the cluster is on fire, the thing that tells you must not be on it |
| Notifications | **Hermod** (AppriseAPI) | LXC 1103 on Verd | Same independence: alerts about K3s must work when K3s is down |
| Active checks | **infra-health-check** prober | Semaphore | Certificates, tokens, quorum, backups: things a metric scrape cannot see |
| At-a-glance status | **Overview page** | asgard K3s | A one-screen status page and app launcher |
| Outside view | **Gatus** watcher | Offsite DigitalOcean node | Detects the homelab being unreachable altogether |

**No Grafana.** VictoriaMetrics ships `vmui` (interactive PromQL and charts) and VictoriaLogs ships its own LogsQL UI; at homelab scale a separate dashboard layer is not load-bearing. A handful of custom vmui dashboards are shipped as a ConfigMap. Prometheus and Loki are not used; VictoriaMetrics is far lighter for the same ingest, and its query language is PromQL-compatible.

---

## Metrics

- **VictoriaMetrics** runs as a single instance (not cluster mode) on a 20 GiB `local-path` volume with one month of retention. Long-term, low-resolution trends are Zabbix's job.
- **vmagent** scrapes kubelet and cAdvisor through the API server proxy (so it needs `nodes/proxy` permission, without which every cAdvisor scrape returns 403) and kube-state-metrics every 30 seconds. kube-state-metrics gets scraped through more than one path, so every query pins its job label or sums read double.
- **Proxmox host metrics are not in VictoriaMetrics.** There is no exporter: host data goes to Zabbix. The overview page reads the Proxmox API itself, with a dedicated read-only token.
- **vmalert is not enabled** and the vm-operator migration is deferred until a charted component needs CRD-based scraping or there is a metrics-alerting case Zabbix does not cover.

## Logs

Every shipper is **vlagent**, the VictoriaLogs project's own agent (chosen over Vector and Fluent Bit on throughput, CPU and file-rotation correctness).

| Producer | Path |
|---|---|
| Pods | A privileged DaemonSet (needed to read host log paths under SELinux) ships to VictoriaLogs inside the cluster |
| LXCs and Proxmox hosts | A systemd vlagent binary, installed by an Ansible role, reads syslog and ships to VictoriaLogs through an HTTPRoute on the internal Gateway (the `/insert` path skips SSO so shippers need no login) |
| K3s node VMs | Same binary, but targeting a ClusterIP, because a node's own network namespace cannot reach a MetalLB VIP |

About two dozen hosts ship logs. Retention is 30 days on a 20 GiB volume. Debian 13 is journald-only by default, so the role installs rsyslog to give vlagent files to read.

The metrics and logs UIs (`metric.` and `logs.` under `niflheim`) are internal-only behind Authentik ForwardAuth. Separate read-only query routes, IP-allowlisted to the control node, serve the AIOps Toolbelt without SSO.

---

## Zabbix

Zabbix 7.0 LTS in its own LXC watches the layer below Kubernetes: Proxmox hosts, every LXC and VM, and service-level templates (Postgres, HAProxy, AdGuard and so on). The agent runs on every host. Its database is the shared Patroni cluster. Sign-in is native SAML with Authentik (a local `Admin` remains for break-glass), and there are three ways in: the public name, an internal alias through Traefik, and a **direct backdoor** straight to the LXC for when Traefik is the thing being debugged.

Zabbix events go to Hermod and, in parallel, to the diagnosis agent. Zabbix has no HA of its own: if its LXC is lost, collection pauses until restored from backup, and the data survives.

---

## Hermod: notifications

One HTTP endpoint every alert source posts to. Hermod, an AppriseAPI instance fronted by Caddy with a source-IP allowlist, fans notifications out to Discord channels by **tag**:

| Tag | Meaning | Channel |
|---|---|---|
| `critical` | Look within minutes, even at 2 am: quorum lost, service hard-down | Hrist (with `@everyone`) |
| `alert` | Look within hours: degraded but up, drift detected | Mist |
| `info` | Never needs a response; the cap tier for anything non-production | Randgrid |
| `media` | Sonarr events (sent straight to Discord by Sonarr, not via Hermod) | Ölrún |
| *(none)* | Producer bug | Hel (quarantine, so nothing vanishes) |

Severity sets the embed colour; the tag sets the channel. Definitions come from the expected *response time*, not from impact adjectives. Routine successes (a clean drift-check) are not sent at all: they are log lines in VictoriaLogs.

**Producers:** Zabbix (a webhook media type on its standard "report problems" action), Patroni (an `on_role_change` callback posts every promotion or demotion within seconds), Semaphore (drift-check, apply, health-check, restore-test), the AIOps components, and the offsite watcher (which has its own independent Discord channel).

---

## What watches the watchers

- Zabbix is outside K3s; Hermod is outside K3s.
- Semaphore's infra-health-check and weekly restore test post their own results; a silent Wednesday means the test stopped, not that all is well.
- The offsite Gatus watcher on DigitalOcean checks the homelab from outside and expects a heartbeat from the control node.

---

## Failure surfaces

| What fails | Effect |
|---|---|
| VictoriaMetrics or VictoriaLogs | Cluster metrics or logs gap until the pod returns (the worker volume is pinned). Zabbix and alerting are unaffected. |
| A log shipper | That host's logs stop; the host itself is unaffected. |
| Zabbix LXC | Host monitoring pauses; K3s-side metrics continue. |
| Hermod | Alerts are not delivered to Discord; the sources still record them, and logs still reach VictoriaLogs. |
| Discord | Same as Hermod from the operator's view. The offsite watcher uses a separate channel for this reason. |
| The whole cluster | Zabbix and Hermod keep running and report it. |

---

## See also

- **Edge** (this section): the Gateways and SSO in front of the UIs.
- **GitOps & automation** (this section): Semaphore's drift-check and the AIOps layer that consumes these signals.
- **Storage & data** (this section): the local-path volumes behind the Victoria stack.
- **Zabbix** and **Hermod** (Services and purpose): the per-service summaries.
- **Troubleshooting**: first checks for common alerts.
- **Observability diagram** (Diagrams): the shippers, the stores and who looks.
