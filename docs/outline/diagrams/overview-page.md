<!-- docs/outline/diagrams/overview-page.md -->

# Overview page diagram

The status page and app launcher, and the two APIs it reads. Internal only, no login: a small Caddy serves the page and proxies exactly two read-only things. (For the whole lab on one sheet, see the **Overview diagram**.)

![Overview page: a status page that reads two APIs](/api/attachments.redirect?id=3e0c171a-72bc-41fd-8818-cad2b0d8d68c)

---

## How to read it

- **Left to middle: the path in.** A browser on the LAN or tailnet polls every 30 seconds, through Traefik at the `overview.niflheim` status alias (no tunnel), to a Caddy with two replicas, one per worker.
- **Right: what Caddy serves.** `/` returns the static page from a ConfigMap volume. `GET /api/v1/query` and `query_range` go to VictoriaMetrics (the single-node instance; its admin, export and write paths are not routable). `GET /api/pve/...` goes to the first Proxmox host that answers, with the Proxmox API token added by Caddy. Any other `/api/*` path is a 404.
- **Config.** A hashed ConfigMap: an edit rolls the pods.
- **Bottom: the Proxmox token.** Terraform creates a read-only `overview@pve` user (the `PVEAuditor` role) and an API token, and mints it into Vault. External Secrets hands it to Caddy as an optional environment variable. Without it the page still works; only the Proxmox slide does not appear.
- **Why a proxy, not the metrics UI:** that host sits behind Authentik ForwardAuth, and a page cannot finish a login redirect inside a background fetch.
- **Reachability badges** on the app icons are the browser's own no-cors fetch: "can I open this from here".
- **Accepted cost:** anyone on the LAN can run PromQL against cluster telemetry through this page.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[overview.excalidraw 41841](/api/attachments.redirect?id=1c405b16-38fb-43f8-a106-0ffab9e930fc)

[overview.svg 56624](/api/attachments.redirect?id=c3784608-6e4e-4683-a14f-2f6166d6facd)

---

## See also

- **Overview page** (Services): the service page.
- **Observability** (Components): the metrics it reads.
- **Overview diagram** (Diagrams): the whole lab on one sheet.
