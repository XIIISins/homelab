<!-- docs/known-issues/traefik-gateway-api.md -->

# Known gotchas — Traefik / Gateway API

*Migrated from `CLAUDE.md`. Recovery commands + rules. Incident retros in [`../incidents/`](../incidents/).*

## Traefik / Gateway API

- **Traefik chart v39+ replaced shorthand entrypoint syntax with upstream nesting.** Old `ports.web.redirectTo` / `ports.websecure.tls.enabled` removed → use `ports.web.http.redirections.entryPoint` / `ports.websecure.http.tls: {}`. Service block upstream-aligned in v40.
- **Traefik chart has no `kubernetesIngressRoute` toggle.** `kubernetesCRD` handles ALL Traefik CRDs together (IngressRoute, Middleware, etc). To use Gateway API only: keep `kubernetesCRD: enabled: true` for Middleware CR support, just don't create IngressRoute CRs.
- **Traefik Gateway listener `port:` matches the entrypoint's internal listen port**, NOT the Service `exposedPort`. Either set Gateway listeners to chart defaults (`8000`/`8443`) — muddy — or grant `NET_BIND_SERVICE` and bind 80/443 directly. We use the latter; Gateway manifests read naturally.

- **Entrypoint-default middlewares are the only way to apply a Traefik `Middleware` to every route at once** — Gateway API has no listener-/Gateway-level filter, and HTTPRoute `ExtensionRef`/`ResponseHeaderModifier` filters are per-rule. Set via the chart at `ports.<entrypoint>.http.middlewares: [ "<namespace>-<name>@<provider>" ]`. The reference is provider-qualified and uses the namespace-`name` form, NOT `namespace/name`: a `Middleware` named `security-headers` in ns `traefik` → **`traefik-security-headers@kubernetescrd`** (needs `kubernetesCRD` provider on — it is). Used for the estate-wide `security-headers` middleware (`infrastructure/traefik/middleware-security-headers.yaml`; see decisions.md "Security response headers"). Order: entrypoint middlewares run *before* route-declared ones, and being outermost they still wrap short-circuit responses (e.g. a ForwardAuth 302 still gets the headers).
  - **Caveat — verify Gateway-API coverage after any change here.** Traefik's docs only explicitly confirm entrypoint defaults attach to IngressRoute/Ingress routers; Gateway-API (HTTPRoute) coverage is true in practice but undocumented. Confirm with `curl -sI https://paste.xiiisins.com | grep -iE 'strict-transport|x-content-type|referrer-policy'` after Flux reconciles. If it ever no-ops, the documented fallback is a per-rule `ResponseHeaderModifier` filter on each HTTPRoute.

