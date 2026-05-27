<!-- docs/outline/components-and-interactions/edge.md -->

# Edge

This subpage covers how HTTP(S) traffic reaches in-cluster workloads — from a browser on a phone outside the house, from a laptop on the LAN, and from one pod in the cluster reaching another via the same hostname an external client would use. Four components carry the load: **Traefik** for in-cluster routing, **Gateway API** as the routing schema, **cert-manager** for TLS, and **Cloudflared** for external exposure.

The principle that shapes every choice on this page: one hostname per service across every access path, with TLS terminated as close to the consumer as possible. A request to `authentik.xiiisins.com` from a phone on 5G and from a laptop on the LAN reach the same Authentik pod via different network paths, but the URL is identical. The certificate the browser sees, where TLS terminates, and which network the bytes traverse all differ — but never the hostname.

---

## The three DNS zones, three Gateways

Every service belongs to one of three DNS zones, and each zone has its own purpose, its own wildcard certificate, and its own Gateway object inside Traefik.

| Zone | Resolved by | Purpose | Gateway | Wildcard |
|---|---|---|---|---|
| `xiiisins.com` (apex) | Cloudflare (public) | Publicly reachable services. Browsers reach via Cloudflared. | `midgard` | `*.xiiisins.com` |
| `midgard.xiiisins.com` | AdGuard Home (internal) | LAN alias for publicly-reachable services — homelab clients don't trombone through Cloudflare. | `midgard` | `*.midgard.xiiisins.com` |
| `niflheim.xiiisins.com` | AdGuard Home (internal) | Internal-only services. Never publicly reachable. | `niflheim` | `*.niflheim.xiiisins.com` |

The two-Gateway split (`niflheim` and `midgard`) exists so route attachment policy can stay strict: HTTPRoutes for internal-only services attach only to `niflheim`, HTTPRoutes for publicly-exposed services (and their LAN aliases) attach to `midgard`. Either Gateway can be reconfigured independently without affecting the other's routes.

---

## Traefik + Gateway API — in-cluster routing

Traefik runs as a 3-replica DaemonSet-shaped Deployment in the `traefik` namespace — one pod per worker, required pod anti-affinity, `externalTrafficPolicy: Local` to preserve source IPs.

- **Gateway API only.** Traefik is configured with the Gateway API provider, no `IngressRoute`, no `Ingress`. Routes are upstream-standard `HTTPRoute` / `TCPRoute` objects, not Traefik-proprietary CRDs. (The `kubernetesCRD` provider stays enabled for `Middleware` CRs, which Gateway API doesn't cover yet.)
- **Direct port binding.** Traefik holds `NET_BIND_SERVICE` capability and listens on 80/443 directly inside the pod, not on Helm-chart-default 8000/8443. Gateway listener `port:` matches what's inside the pod.
- **One MetalLB VIP** at `10.0.20.10`. AdGuard's internal zones point every `*.midgard` and `*.niflheim` FQDN at this single IP. The Gateway's `hostname:` field on each HTTPRoute disambiguates which backend gets the request.

### What a Gateway looks like

The `niflheim` Gateway has one HTTPS listener on `*.niflheim.xiiisins.com` (TLS terminated with the niflheim wildcard cert). `allowedRoutes.namespaces.from: All` so any namespace can attach an HTTPRoute. Internal-only services (NetBox, Vault UI, Semaphore, vmui, the VictoriaLogs UI, Zabbix backdoor) attach here.

The `midgard` Gateway has three HTTPS listeners: `*.xiiisins.com`, `*.midgard.xiiisins.com`, and the bare apex `xiiisins.com` (for WebFinger). Each listener references its own wildcard cert. Publicly-exposed services (Authentik, Outline, Zabbix front-end) attach HTTPRoutes here with `hostnames: [foo.xiiisins.com, foo.midgard.xiiisins.com]` — the same backend serves both names.

---

## cert-manager — TLS for the three zones

cert-manager issues three wildcard certificates, all via Let's Encrypt DNS-01 challenge against the same zone-scoped Cloudflare API token.

- **One Cloudflare token** stored at Vault path `secret/k8s/cert-manager/cloudflare`. Scope: `Zone:DNS:Edit + Zone:Zone:Read` on `xiiisins.com`. The token serves all three certs because they're subdomains of the same zone.
- **DNS-01, not HTTP-01.** Wildcards aren't issuable by HTTP-01 (Let's Encrypt requires DNS proof of zone control for `*` certs). DNS-01 also means cert issuance works for internal-only zones with no public path — `*.niflheim.xiiisins.com` has no reachable HTTP endpoint, but Let's Encrypt only ever talks to Cloudflare's DNS API.
- **ECDSA P-256**, 90-day validity, 30-day renewal window. Same posture for all three wildcards.
- **Two ClusterIssuers** — `letsencrypt-staging` (for testing changes that risk burning issuance quota) and `letsencrypt-prod`. Production certs always issued by `letsencrypt-prod`.

The flow: cert-manager asks Let's Encrypt for a cert → ACME challenge arrives as a DNS record name → cert-manager uses the Cloudflare token to write the `_acme-challenge` TXT record → Let's Encrypt verifies and issues → cert-manager stores the cert + key in a K8s Secret → the Gateway listener references the Secret. Renewals run automatically on the 30-day window.

---

## Cloudflared — external traffic

External browsers reach the homelab via Cloudflare Tunnel. The `cloudflared` Deployment in the `cloudflared` namespace runs three replicas, each with an outbound persistent connection to Cloudflare's edge. No inbound port-forwards on the UCG-Ultra; the tunnel is the only external ingress.

- **Locally-managed tunnel.** Tunnel credentials live in Vault at `secret/k8s/cloudflared/tunnel-credentials` and project into the pod via ESO. Routes (which hostname maps to which backend) are managed via a `cloudflared` ConfigMap, not the Cloudflare dashboard.
- **TLS terminates twice.** Cloudflare's universal cert terminates browser TLS at the Cloudflare edge (free-plan Custom SSL is out of scope). Cloudflared re-encrypts to Traefik using the apex wildcard cert at the origin. The browser sees Cloudflare's cert; Traefik sees the `*.xiiisins.com` cert it issued for itself.
- **Backend target is Traefik's ClusterIP DNS, never a MetalLB VIP.** Cloudflared runs in-cluster, so it can dial `traefik.traefik.svc.cluster.local` directly. Pointing it at MetalLB IPs would create a trombone path inside the cluster.
- **`originRequest.httpHostHeader: <fqdn>`** is set per route so Traefik routes by the original public hostname and serves the matching HTTPRoute. The host header survives the tunnel; the IP layer doesn't.

### TCP services

The Tunnel handles HTTP(S) by default but can carry arbitrary TCP via cloudflared's `tcp://` ingress type — Teamspeak's voice port is the current example. Cloudflared opens a TCP forward from the edge to a backend Service inside the cluster.

---

## In-cluster pods reaching K8s-fronted hostnames

A pod talking to `authentik.midgard.xiiisins.com` from inside the cluster needs special routing — the FQDN resolves to the Traefik MetalLB VIP for external clients, but a pod dialing that VIP from a worker creates a trombone through MetalLB's L2 announcement.

The fix is a CoreDNS rewrite. `k8s/asgard/infrastructure/coredns-custom/` declares one rewrite per K8s-fronted internal FQDN:

```
rewrite name exact authentik.midgard.xiiisins.com traefik.traefik.svc.cluster.local
```

CoreDNS resolves the rewritten name to Traefik's ClusterIP at query time, the pod connects to Traefik with the original SNI and Host header, and Traefik routes by hostname to the actual backend. External clients (browsers, tailnet devices) bypass CoreDNS entirely and continue to resolve via AdGuard.

The rewrite list grows as new K8s-fronted internal FQDNs land. LXC/VM FQDNs aren't rewritten — those aren't behind Traefik.

---

## The three access paths, end to end

A single service typically has three paths to reach it. Authentik is the canonical example.

| Path | Client | DNS | TLS terminated by | Bytes traverse |
|---|---|---|---|---|
| External | Phone on 5G | Cloudflare → `authentik.xiiisins.com` | Cloudflare edge, then Traefik | Cloudflare Tunnel → cloudflared → Traefik → Authentik pod |
| LAN | Laptop on Wi-Fi | AdGuard → `authentik.midgard.xiiisins.com` → `10.0.20.10` | Traefik | Switch → worker eth1 → Traefik → Authentik pod |
| In-cluster | Outline pod | CoreDNS rewrite → Traefik ClusterIP | Traefik | Pod network → Traefik → Authentik pod |

Same URL prefix scheme, three radically different network paths, one backend pod.

---

## Failure surfaces worth knowing

- **Traefik pod fails on one worker.** Required pod anti-affinity + 3 replicas across 3 workers means losing one pod drops capacity by a third. MetalLB's L2 announcement keeps the VIP advertised by the remaining workers. Rolling updates use `maxSurge: 0, maxUnavailable: 1` because there's no fourth slot — the rollout runs briefly at 2/3.
- **cert-manager renewal fails.** The existing cert keeps working until expiry (90-day duration, 30-day renewal window — there's a 60-day buffer). Failures show up in cert-manager events; the most common cause is the Cloudflare token rotating without a Vault update.
- **Cloudflared tunnel down.** External access breaks. LAN access via `*.midgard.xiiisins.com` and `*.niflheim.xiiisins.com` continues — those don't traverse the tunnel. Three replicas with outbound persistent connections mean a single replica failure is invisible.
- **MetalLB VIP unreachable.** All LAN and in-cluster paths to Traefik fail. External (Cloudflared) traffic is unaffected because cloudflared dials Traefik's ClusterIP, not the VIP. Diagnosis: the four landmine fixes for multi-homed workers documented in the **Network** subpage are the usual suspects.
- **CoreDNS custom rewrite missing for a new internal FQDN.** A pod consuming the FQDN sees the resolution succeed (AdGuard returns the Traefik VIP) but the TCP connection hangs. Add the rewrite, `kubectl rollout restart deploy -n kube-system coredns`, and the pod's next connection succeeds.
- **Authentik down (auth gate for many services).** Routing still works — Traefik continues to serve the protected hostnames — but ForwardAuth middleware (vmui, VictoriaLogs UI, etc.) and OIDC discovery fail, so the affected services 401. See **Identity & secrets** for break-glass.

---

## See also

- **Network** (this section) — MetalLB VIP allocation, multi-homed worker landmines that affect Traefik reachability.
- **Identity & secrets** (this section) — Authentik OIDC/SAML/ForwardAuth integration, Cloudflare token storage in Vault.
- **GitOps & automation** (this section) — gateway-config + cert-manager-config Kustomizations (CRD-dependent, `dependsOn: infrastructure`).
- **Services and purpose** — per-service HTTPRoute hostnames, which Gateway each attaches to, which auth integration each uses.
- **Troubleshooting** — Traefik chart entrypoint syntax across versions, Gateway listener port mismatch, cert-manager DNS-01 propagation delays, Cloudflared host-header gotchas.
