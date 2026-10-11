<!-- docs/outline/diagrams/observability.md -->

# Observability diagram

VictoriaMetrics and VictoriaLogs, with no Grafana. The stores run in the cluster with their own UIs; vlagent ships every log, vmagent scrapes Kubernetes metrics, and Zabbix watches hosts separately.

![Observability: VictoriaMetrics and VictoriaLogs, no Grafana](/api/attachments.redirect?id=260fe7c8-75f5-440c-a1ea-424916a2c3f1)

---

## How to read it

- **Left: four sources, each with its own shipper.** Pod logs go through a vlagent DaemonSet. Node VM system logs go through the vlagent binary (installed by Ansible) to a ClusterIP companion Service, because node networking cannot reach MetalLB addresses. LXC, Proxmox host and PBS syslog go through the vlagent binary to an HTTPRoute with no auth on `/insert`. Metrics (kubelet, cAdvisor, kube-state-metrics, annotated pods) are scraped by vmagent through the apiserver proxy.
- **The `monitoring` namespace.** VictoriaLogs (single node, 20 Gi on `local-path`, 30 days, LogsQL UI) has two ingest doors: the in-cluster Service and the niflheim HTTPRoute. VictoriaMetrics (single node, 20 Gi on `local-path`, one month) serves vmui and four custom dashboards. Both are on `local-path` because they mmap, so never NFS.
- **Right: who looks.** The operator's browser, through Traefik and Authentik ForwardAuth on the `metric.` and `logs.` hostnames. The Overview status page sends GET-only PromQL through its Caddy. Zabbix on the Hugin LXC (dashed) watches hosts and LXCs outside K3s on purpose.
- **No Grafana:** vmui and the LogsQL UI are the dashboard layer. `vmalert` stays off until an application-level alert needs it, and vm-operator is a later phase. vlagent was chosen over Vector and Fluent Bit for several times the throughput at a fraction of the CPU.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[observability.excalidraw 44479](/api/attachments.redirect?id=dec1ea02-9967-47fc-9c73-29719feb0c41)

[observability.svg 58041](/api/attachments.redirect?id=56967589-5590-4a51-9f01-373f9ba3966f)

---

## See also

- **Observability** (Components): the split, metrics, logs and failure surfaces.
- **Zabbix** (Services): host and LXC monitoring.
- **Overview page** (Services): the status page that reads the metrics.
