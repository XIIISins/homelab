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

1. **A verdict sentence** ("Everything is running." / "2 things need attention.") with the reasons listed underneath,
   and a **slideshow of trend graphs** under it. Three slides of three graphs each, rotating every 9 seconds:
   *K3s cluster* (CPU, memory, web traffic; six hours), *K3s workloads* (running pods, pod network, volume use; six
   hours) and *Proxmox hosts* (CPU, memory, network over 24 hours, with the host and guest counts in the caption).
   The Proxmox slide appears only once Proxmox has answered. The tab strip jumps to any slide; the active tab carries
   the timer bar; hovering or focusing the card, or leaving the browser tab, pauses it; the round button pauses and
   resumes it; and nothing rotates on its own under `prefers-reduced-motion`.
   Bad: a node not ready or missing, a Proxmox host not online, pending/failed pods, a certificate not ready or
   expiring within 7 days, a volume 95 % full. Watch: a Proxmox host over 90 % CPU or memory, Proxmox not answering
   (after it has worked once), CPU or memory at 85 % or more, deployments below their replica count,
   five or more restarts in the last hour, a volume 85 % full, a certificate expiring within 14 days.
2. **The three machines** (Urd, Verd, Skuld), each with its control-plane and worker VM and their CPU (cyan)
   and memory (magenta) as ring gauges; a ring turns yellow at 85 %. A node the page does not list still
   appears, under "Other nodes".
3. **Busiest namespaces:** the top six by memory used by pods, with CPU beside each.
4. **Eight facts:** web traffic through Traefik, pod network in and out, deployments with every replica up,
   restarts in the last hour, the fullest volume, volume use in total, certificates (with the soonest to expire,
   by name), and how many containers, pods and namespaces are running.
5. **Apps.** The everyday ones (Outline, Immich, MicroBin, Bookmarks) are four tiles beside the headline. The lab
   and hardware apps live in a **left rail**: an icon strip by default that widens on hover or keyboard focus, with
   the overview giving up that width in step, and a pin button that keeps it open (remembered per browser in
   `localStorage`). The rail is as tall as the overview: it starts level with the header and ends level with the
   last card. Search sits in the rail and filters both: `/` or Ctrl/Cmd+K focuses it, Enter opens the first match,
   the arrow keys walk the results. Hosts with a browser-trusted certificate get a reachability badge on their icon,
   a no-cors `fetch` from the viewer's own browser, so it answers "can I open this from here". Under 1000 px wide
   there is no rail: headline, everyday tiles, the rest of the apps, then machines, namespaces and facts.

When VictoriaMetrics does not answer the page says so, blanks every figure and trend line and keeps the buttons
working; it recovers by itself on the next poll.

**Look.** Tokyo Night (Night) by default and Tokyo Night Day when the OS asks for light (accents deepened a little
for contrast). Blue, magenta and cyan only sort things (everyday apps, lab tools, hardware); green, yellow and red
only ever mean status. The colour tokens are at the top of `site/style.css`.

When VictoriaMetrics does not answer the page says so, blanks the figures and keeps the buttons working.

## How it is built

```
browser ── https ──▶ Traefik (niflheim Gateway) ──▶ Caddy :8080
                                                     ├─ /            static page (ConfigMap volume at /srv)
                                                     ├─ /healthz     probes
                                                     ├─ GET /api/v1/query{,_range} ──▶ vmsingle.monitoring.svc:8428
                                                     ├─ GET /api/pve/cluster/resources           ─┐ token added by Caddy,
                                                     ├─ GET /api/pve/nodes/<node>/rrddata        ─┴▶ first of 10.0.254.11/12/13:8006
                                                     └─ any other /api/*     404
```

- **Static first.** The machines and the app buttons are in `index.html`, so the first paint is complete
  and works without JavaScript; `app.js` only fills in numbers. The page is about 37 KB on the wire on a first
  visit (about 18 KB once the font is cached), makes no external requests, and fires its 26 instant queries in
  parallel (each answers in under 30 ms; HTTP/2 through Traefik multiplexes them). The three trend lines are
  range queries (six hours, 5 minute steps) fetched once and then every five minutes. Polling runs every 30 s
  (vmagent's scrape interval) and pauses while the tab is hidden; reachability probes start after the first metrics attempt, when the browser is idle.
- **Why a proxy and not `metric.niflheim`.** That host sits behind Authentik ForwardAuth, and a page cannot
  complete the login redirect for a background `fetch`. The Caddy route exposes exactly two VictoriaMetrics paths
  (`/api/v1/query` and `/api/v1/query_range`, GET only). The admin API (`delete_series`, snapshots), export and every
  write path are not routable. The cost: anyone on the LAN can run PromQL against cluster telemetry through this page. Accepted;
  see the decision row.
- **Proxmox, and why it is wired this way.** Proxmox metrics are not in VictoriaMetrics (host-level data goes to
  Zabbix, there is no exporter), so the page reads the Proxmox API itself. It uses a dedicated read-only identity,
  `overview@pve` with the stock `PVEAuditor` role, minted by `terraform/proxmox/overview-access/` into Vault
  `secret/k8s/overview/pve-token`; External Secrets builds the finished `Authorization` value into the Secret
  `overview-pve-token`; the Deployment reads it as the **optional** env var `OVERVIEW_PVE_AUTH`; Caddy adds it to the
  request. The browser never sees it. Only the two GET shapes above are routable (everything else, including path
  traversal and encoded variants, is 404), a client-supplied `Authorization` is replaced and cookies are stripped.
  Caddy tries the three hypervisors in order, so one down (Skuld has frozen before) does not blank the slide.
  Certificate verification is skipped (self-signed, management-VLAN IPs, read-only token).
- **Order of operations (matters).** Apply `terraform/proxmox/overview-access` first, then merge. Until the Vault
  path exists ESO cannot build the Secret; because the env var is optional the pods still start, and the page shows two
  slides and says nothing, which is the right behaviour for "not connected yet". Env vars are read once, so a token
  that appears after the pods started needs `kubectl rollout restart deploy/overview -n overview`.
- **Revoking it:** delete the `overview-access` Terraform resources (or the token in Proxmox); the page drops back to
  two slides.
- **ConfigMaps are generated with the name hash**, so editing the page or the Caddyfile gives a new ConfigMap
  name, kustomize rewrites the Deployment and the pods roll. No `rollout restart` step (unlike the subPath
  mounts in `apex-static`).
- **Font:** Familjen Grotesk (SIL OFL), latin subset, variable weight, vendored as `site/familjen-grotesk.woff2`
  (from the fontsource package via jsDelivr, 18.9 KB). It is served from the pod with a one-week cache.

## Changing it

- **Add or move an app button:** edit `site/index.html` (each `<li class="app">`; the first group, `quick`, is the main-area tiles, the others are the rail; `data-probe` on the
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
- **A Proxmox that is down must not slow the K3s numbers.** The page caps its Proxmox request at 3 s; Caddy gives up on
  all three hypervisors after about 4 s.
- **No Flux tile.** The `gotk_reconcile_condition` series do not exist in VictoriaMetrics, so there is nothing to read.
- **Memory is the pods' working set against the node's allocatable memory**, not the VM's total. It leaves out the
  OS and K3s reservation (2 GiB per worker), so it reads lower than the Proxmox or Zabbix figure.
