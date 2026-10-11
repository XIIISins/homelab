<!-- docs/outline/components-and-interactions/edge.md -->

# Edge

The edge is how a request from a person reaches a workload: DNS, TLS, the Gateways, Traefik, and the Cloudflare tunnel for public traffic. Everything here lives in the asgard cluster and is managed by Flux.

---

## The pieces

| Component | Job |
|---|---|
| **Traefik** | The only ingress controller. Three replicas, one per worker, behind MetalLB address `10.0.20.10`. Gateway API only: no `Ingress` or `IngressRoute` objects (the Traefik CRDs are on solely so `Middleware` resources work). |
| **Gateway API** | Two Gateways, `niflheim` and `midgard`, define listeners and certificates. Apps attach an `HTTPRoute`. |
| **cert-manager** | Issues certificates by DNS-01 against Cloudflare, plus an internal CA for cluster-internal TLS. |
| **trust-manager** | Distributes the internal CA bundle to namespaces that need to trust it. |
| **Cloudflared** | Outbound tunnel from the cluster to Cloudflare for public hostnames. Three replicas. |
| **CoreDNS custom rewrites** | In-cluster split horizon so pods reach Traefik without leaving the cluster path. |
| **Authentik ForwardAuth** | A shared Traefik middleware that gates internal tools behind SSO. |

---

## Three zones, two Gateways, three certificates

| Zone | Gateway listener | Certificate | Exposure |
|---|---|---|---|
| `*.niflheim.xiiisins.com` | `niflheim` | Wildcard (Let's Encrypt) | Internal only |
| `*.midgard.xiiisins.com` | `midgard` | Wildcard | Internal alias of public services |
| `*.xiiisins.com` and `xiiisins.com` | `midgard` (apex listeners) | Wildcard | Public, via tunnel |

The two Gateways are separate so the rules about who may attach routes can differ for public and internal services. All three wildcards come from cert-manager DNS-01 using one Cloudflare token scoped to the DNS zone only. DNS-01 means no inbound port 80 is needed for issuance.

TLS for external traffic works in two hops. The browser sees Cloudflare's universal certificate at the edge (custom certificates on Cloudflare need a plan the homelab does not have). Cloudflare then re-encrypts to the origin, where Traefik presents the Let's Encrypt apex wildcard.

---

## Three ways in

### 1. Public (from the internet)

```
Browser → Cloudflare edge (Cloudflare cert)
        → Cloudflare tunnel "asgard-k3s"
        → cloudflared pod
        → Traefik (in-cluster Service, never a MetalLB VIP)
        → HTTPRoute → app
```

The tunnel is locally managed: its ingress rules are a ConfigMap in Git, not the Cloudflare dashboard. Cloudflared targets Traefik's ClusterIP service; a MetalLB IP would not work from inside the cluster. Each rule sets the Host header so Traefik picks the right route. The public hostnames are `authentik`, the apex (WebFinger), `wiki`, `hugin`, `home`, `paste` and `immich` under `xiiisins.com`, and anything else hits a 404 catch-all. Terraform owns the matching Cloudflare DNS records.

Not everything is allowed through the tunnel. Video streaming (Jellyfin) is **internal-only on purpose**: pushing media through a Cloudflare tunnel risks the whole zone under Cloudflare's terms.

### 2. Internal, publicly-reachable service

A LAN or tailnet client asks AdGuard for `wiki.midgard.xiiisins.com`, gets `10.0.20.10`, and reaches Traefik directly. Same backend as the public name, no Cloudflare round trip.

### 3. Internal-only

`netbox`, `vault`, `semaphore`, `metric`, `logs`, `sonarr`, `sabnzbd`, `overview` and others live under `niflheim.xiiisins.com`. AdGuard resolves them to Traefik; the firewall means the internet cannot reach them. Tools that need a login are gated by Authentik: native OIDC where the app supports it, or the ForwardAuth middleware where it does not.

### Backdoors

Some services run on LXCs outside Kubernetes, and Traefik fronts them with a Service plus a hand-written EndpointSlice. For those, an explicit direct name skips Traefik so the service stays reachable when Kubernetes is the thing being debugged: `hugin-direct` for Zabbix, `jellyfin-direct` for playback.

---

## Operational rules

- **Rollouts need `maxSurge: 0`.** One Traefik pod per worker plus required anti-affinity leaves no room for a surge pod, so a default rolling update would deadlock. The strategy key is root-level in the chart, and a Helm release that "succeeded" does not prove the value was applied.
- **Estate-wide security headers** are one Traefik middleware attached as an entrypoint default, since Gateway API has no listener-level filter.
- **Source IPs are preserved** (`externalTrafficPolicy: Local`), which Teamspeak's per-IP bans and the access logs rely on.
- **A new internal name** needs three things: an HTTPRoute, an AdGuard rewrite in Terraform, and (if pods call it) a CoreDNS rewrite. A new public name also needs a Cloudflare record and a Cloudflared ingress rule.
- **Non-HTTP traffic** (Teamspeak voice) bypasses all of this: a UCG-Ultra port-forward goes straight to a MetalLB VIP with a worker elected by ARP.

---

## Failure surfaces

| What fails | Effect |
|---|---|
| One Traefik pod or worker | Others keep serving; MetalLB re-elects the VIP holder. |
| All Traefik pods | All web UIs stop, internal and public. LXC backdoors still work. |
| Cloudflared | Public hostnames fail; internal access is unaffected. |
| Cloudflare token stale or invalid | Certificates cannot renew; existing certs keep working until expiry. |
| AdGuard | Internal names do not resolve; public sites work. |
| Authentik | SSO-gated tools reject logins; local break-glass accounts and direct backdoors remain. |

---

## See also

- **Network** (this section): DNS, VLANs, MetalLB.
- **Identity & secrets** (this section): the SSO the edge delegates to.
- **Observability** (this section): the monitoring UIs behind the edge.
- **Identity & edge** troubleshooting (Troubleshooting): outpost caching, stale tokens, discovery errors.
