<!-- docs/outline/services-and-purpose/overview.md -->

# Overview page

A one-screen status page and app launcher for the homelab at `overview.niflheim.xiiisins.com` (also `status.xiiisins.com`). Internal-only: the names exist only in AdGuard, so they resolve on the LAN and the tailnet and nowhere else.

---

## What it shows

- **A headline and verdict** ("Homelab Healthy", or a count of warnings or issues) with either a one-line summary or a short table of what is wrong and where, and a rotating slideshow of trend graphs for the cluster, the workloads and the Proxmox hosts.
- **The three machines**, each with its control-plane and worker VM and ring gauges for CPU and memory.
- **Busiest namespaces and eight key facts**: web traffic, pod network, deployments fully up, restarts, the fullest volume, certificates and their expiry, and container counts.
- **Apps**: everyday ones as tiles, lab and hardware tools in a collapsible side rail, with search, light/dark switching and a reachability badge.

What counts as bad (a node not ready, a certificate expiring within a week, a volume 95% full) and what counts as "watch" (CPU or memory at 85%, a certificate within 14 days) is defined in the page's own script.

---

## How it is built

A Caddy pod serves a static page and proxies two narrow things:

- **VictoriaMetrics**, read-only: only the instant and range query paths, GET only. The admin and write APIs are not routable. It proxies because the normal metrics address sits behind Authentik sign-in, which a background request cannot complete. The cost is that anyone on the LAN can run queries through the page, which was accepted for read-only telemetry.
- **The Proxmox API**, through a dedicated read-only identity (auditor role only), minted by Terraform and delivered through Vault and External Secrets. Caddy adds the token itself, so a browser never sees it, and tries all three hypervisors in turn so one frozen node does not blank the slide. If the token is missing the page shows two slides and says nothing.

The page is complete without JavaScript on first paint and makes no external requests. It fits one window height and trims its lowest sections to avoid scrolling. When metrics are unavailable it says so, blanks its figures and keeps the app buttons working.

It has no sign-in: read-only numbers, LAN and tailnet only. Making it public would expose the metrics proxy, so Authentik would have to go in front first.

---

## See also

- **Observability** (Components): the metrics it reads.
- **Startpage** (this section): the personal bookmarks page.
- **URLs**: the full hostname directory.
