<!-- docs/known-issues/traefik-gateway-api.md -->

# Known gotchas — Traefik / Gateway API

*Migrated from `CLAUDE.md`. Recovery commands + rules. Incident retros in [`../incidents/`](../incidents/).*

## Traefik / Gateway API

- **Traefik chart v39+ replaced shorthand entrypoint syntax with upstream nesting.** Old `ports.web.redirectTo` / `ports.websecure.tls.enabled` removed → use `ports.web.http.redirections.entryPoint` / `ports.websecure.http.tls: {}`. Service block upstream-aligned in v40.
- **Traefik chart has no `kubernetesIngressRoute` toggle.** `kubernetesCRD` handles ALL Traefik CRDs together (IngressRoute, Middleware, etc). To use Gateway API only: keep `kubernetesCRD: enabled: true` for Middleware CR support, just don't create IngressRoute CRs.
- **Traefik Gateway listener `port:` matches the entrypoint's internal listen port**, NOT the Service `exposedPort`. Either set Gateway listeners to chart defaults (`8000`/`8443`) — muddy — or grant `NET_BIND_SERVICE` and bind 80/443 directly. We use the latter; Gateway manifests read naturally.

