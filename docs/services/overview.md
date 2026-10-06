<!-- docs/services/overview.md -->
# Overview page

A one-screen status page and launcher for the homelab, served at
**`overview.niflheim.xiiisins.com`**. Internal-only: the name exists only in AdGuard, so it
resolves on the LAN and the tailnet and nowhere else. It is **not** the Startpage:
[`startpage.md`](startpage.md) stays the personal bookmarks page at `home.xiiisins.com`, and
the overview links to it as "Bookmarks".

- **Manifests:** `k8s/asgard/apps/overview/` (+ `k8s/asgard/vpa-config/overview.yaml`)
- **DNS:** `overview.niflheim.xiiisins.com → 10.0.20.10` in `terraform/adguard/rewrites.tf`
- **Replicas:** 2, one per worker (required anti-affinity), Caddy `2.11.2-alpine`
- **Auth:** none. Read-only numbers, LAN/tailnet reach only, same trust level as the smoketest endpoint.

## What it shows

1. **A verdict sentence** ("Everything is running." / "2 things need attention.") with the reasons listed underneath.
   Bad: a node not ready or missing, pending/failed pods, a certificate not ready or expiring within 7 days,
   a volume 95 % full. Watch: CPU or memory at 85 % or more, deployments below their replica count,
   five or more restarts in the last hour, a volume 85 % full, a certificate expiring within 14 days.
2. **The three machines** (Urd, Verd, Skuld), each with its control-plane and worker VM and their CPU and memory.
   A node the page does not list still appears, under "Other nodes".
3. **Four facts:** web traffic through Traefik, restarts in the last hour, the fullest volume, certificates.
4. **App buttons**, grouped, filterable (press `/`). Hosts with a browser-trusted certificate get a reachability
   dot, a no-cors `fetch` from the viewer's own browser, so it answers "can I open this from here".

When VictoriaMetrics does not answer the page says so, blanks the figures and keeps the buttons working.

## How it is built

```
browser ── https ──▶ Traefik (niflheim Gateway) ──▶ Caddy :8080
                                                     ├─ /            static page (ConfigMap volume at /srv)
                                                     ├─ /healthz     probes
                                                     ├─ GET /api/v1/query ──▶ vmsingle.monitoring.svc:8428
                                                     └─ any other /api/*     404
```

- **Static first.** The machines and the app buttons are in `index.html`, so the first paint is complete
  and works without JavaScript; `app.js` only fills in numbers. The page is about 30 KB on the wire on a first
  visit (about 11 KB once the font is cached), makes no external requests, and fires its 14 queries in
  parallel (each answers in under 30 ms). Polling runs every 30 s (vmagent's scrape interval) and pauses while
  the tab is hidden; reachability probes start after the first metrics attempt, when the browser is idle.
- **Why a proxy and not `metric.niflheim`.** That host sits behind Authentik ForwardAuth, and a page cannot
  complete the login redirect for a background `fetch`. The Caddy route exposes exactly one VictoriaMetrics path
  (instant queries, GET). Range queries, the admin API (`delete_series`, snapshots) and every write path are not
  routable. The cost: anyone on the LAN can run PromQL against cluster telemetry through this page. Accepted;
  see the decision row.
- **ConfigMaps are generated with the name hash**, so editing the page or the Caddyfile gives a new ConfigMap
  name, kustomize rewrites the Deployment and the pods roll. No `rollout restart` step (unlike the subPath
  mounts in `apex-static`).
- **Font:** Familjen Grotesk (SIL OFL), latin subset, variable weight, vendored as `site/familjen-grotesk.woff2`
  (from the fontsource package via jsDelivr, 18.9 KB). It is served from the pod with a one-week cache.

## Changing it

- **Add or move an app button:** edit the list in `site/index.html` (each `<li class="app">`; `data-probe` on the
  link turns on the reachability dot, which only works for hosts whose certificate browsers trust, so not for
  Proxmox, PBS, DSM or the router). Merge; Flux rolls the pods.
- **Change a metric or a threshold:** `QUERIES` and `HIGH` at the top of `site/app.js`.
- **Test without a cluster:** serve `site/` and proxy `/api/v1/query` to a `kubectl port-forward` of
  `svc/victoriametrics-victoria-metrics-single-server` (read-only). Point the proxy at a stub that returns bad
  data to see the warn and bad states.

## Gotchas

- **Every `kube_*` query must pin `job="kube-state-metrics"`.** kube-state-metrics is scraped twice (see
  [`known-issues/observability.md`](../known-issues/observability.md)); unpinned sums read double (188 running
  pods instead of 94, 12 nodes instead of 6).
- **No Flux tile.** The `gotk_reconcile_condition` series do not exist in VictoriaMetrics, so there is nothing to read.
- **Memory is the pods' working set against the node's allocatable memory**, not the VM's total. It leaves out the
  OS and K3s reservation (2 GiB per worker), so it reads lower than the Proxmox or Zabbix figure.
