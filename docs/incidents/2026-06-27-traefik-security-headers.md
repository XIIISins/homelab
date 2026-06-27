<!-- docs/incidents/2026-06-27-traefik-security-headers.md -->

# 2026-06-27 — Estate-wide security headers + a latent Traefik rollout deadlock

## Summary

A website scan of `paste.xiiisins.com` flagged three missing security headers (HSTS, `X-Content-Type-Options`, `Referrer-Policy`) + backend server-software disclosure. The whole estate (~13 HTTPRoutes) shared the gap, so the fix was a single `security-headers` Traefik `Middleware` attached to the **`websecure` entrypoint** (`ports.websecure.http.middlewares` → `traefik-security-headers@kubernetescrd`) — one source of truth that covers every current + future route, rather than a per-route filter duplicated ~25×. Deploying it triggered the first Traefik pod-roll in 33 days, which **surfaced a pre-existing latent bug**: the deployment update-strategy was set under the wrong chart key (`deployment.strategy`, silently ignored) so the live deployment ran the chart-default `maxSurge:1/maxUnavailable:0` — which deadlocks against the per-node anti-affinity. No outage (old ReplicaSet kept serving under `maxUnavailable:0`); fixed by moving the strategy to the chart's root-level `updateStrategy`. Headers verified present on both the public Cloudflare path and the direct Traefik VIP.

## Trigger

Operator relayed a security scan of `paste.xiiisins.com` (MicroBin): three Low-risk "missing security header" findings + one "server software/technology found" (Uncertain). Operator: "Probably more of our reachable stuff has the same issue. Can we fix these and possible fix other security posture things?"

## Decisions made

- **Mechanism: entrypoint-default middleware**, chosen over per-route `ResponseHeaderModifier` / per-namespace `Middleware` + `ExtensionRef`. Single source of truth, auto-covers future routes. Trade: Traefik docs only explicitly confirm entrypoint defaults for IngressRoute/Ingress — Gateway-API coverage was unverified going in (~80% confidence), with per-route `ResponseHeaderModifier` as the documented fallback. **Coverage confirmed live** (see Sequence) — fallback not needed.
- **HSTS strength: `max-age=31536000; includeSubDomains`, no preload** — preload is effectively one-way for `*.xiiisins.com`.
- **`X-Frame-Options` / CSP `frame-ancestors` deliberately NOT in the global default** — they break OIDC iframe / Authentik embedded-outpost flows; need per-app testing. Tracked in open-questions.

## Sequence

1. Mapped routing: ~13 HTTPRoutes across ~12 namespaces, all on one Traefik. Confirmed chart key for entrypoint middleware is `ports.<ep>.http.middlewares` (v40.2.0 values), reference form `<namespace>-<name>@<provider>`.
2. Added `infrastructure/traefik/middleware-security-headers.yaml` (`headers` middleware: `stsSeconds`/`stsIncludeSubdomains`, `contentTypeNosniff`, `referrerPolicy`, strip `Server`/`X-Powered-By`), wired into the kustomization, attached to `websecure`. Committed; `kubectl kustomize` clean. Did NOT push (operator's call at first).
3. Operator authorized deploy. Pushed to `origin/main`, `flux reconcile` → Middleware created correctly, HelmRelease began a Helm upgrade (first Traefik roll in 33d).
4. **Rollout deadlocked.** New pod `Pending`, `FailedScheduling … didn't match pod anti-affinity rules`, 3 old pods still Running (4 total — a surge that `maxSurge:0` was supposed to forbid). Live `deploy.spec.strategy` read `maxSurge:1/maxUnavailable:0` — the K8s 25%/25% default for 3 replicas — NOT the `maxSurge:0/maxUnavailable:1` written in `helmrelease.yaml`.
5. **Root cause:** the Traefik chart's strategy key is root-level **`updateStrategy`**, not `deployment.strategy`. The latter is silently ignored; the chart fell back to its default. Latent for 33 days because nothing had forced a roll.
6. Attempted a manual `kubectl patch` to unstick — **correctly blocked by the auto-mode classifier** (direct kubectl mutation violates the Flux-only invariant; operator authorized a Flux deploy, not hand-patching). Did it the GitOps way instead.
7. Moved the strategy block to root-level `updateStrategy`, committed, pushed, reconciled. The stuck Helm upgrade hit its 10m timeout; helm-controller retried with corrected values → Deployment strategy flipped to `maxUnavailable:1` → controller dropped one old pod → freed a node → new pods rolled in serially. Converged to 3/3, one per worker. Zero downtime throughout.
8. **Verified:** headers present on `https://paste.xiiisins.com` (public, via Cloudflare) AND on the direct Traefik VIP (`--resolve …:10.0.20.10`). The VIP response has no `Server` header (origin strip works); the public path shows `server: cloudflare` (edge header, not our stack). Direct-VIP success **confirms entrypoint defaults DO attach to Gateway-API routers.**

## Findings (encoded as gotchas)

1. **Entrypoint-default middleware is the only apply-to-all path with Gateway API** (no Gateway-/listener-level filter; HTTPRoute filters are per-rule). Reference form is `<namespace>-<name>@kubernetescrd`. Entrypoint middlewares run *before* route-declared ones and wrap short-circuit responses (a ForwardAuth 302 still gets the headers). **Confirmed to cover Gateway-API routers** despite the docs only naming IngressRoute/Ingress. → [`known-issues/traefik-gateway-api.md`](../known-issues/traefik-gateway-api.md).
2. **The Traefik chart's update strategy is root-level `updateStrategy`, not `deployment.strategy`** — wrong key is silently ignored, defaulting to `maxSurge:1/maxUnavailable:0`, which deadlocks against our per-node anti-affinity (no 4th node for the surge pod). No outage because the old RS holds under `maxUnavailable:0`. → [`known-issues/traefik-gateway-api.md`](../known-issues/traefik-gateway-api.md).
3. **A "succeeded" Helm upgrade does not mean a values block was honored.** The last upgrade reported success 33d ago with the strategy values silently dropped. Verify the *rendered object* (`kubectl get deploy … -o jsonpath='{.spec.strategy}'`), not just release status — this is the same class as the Semaphore collection-pin skew (pin only real where installed).
4. **The auto-mode classifier correctly blocked a live `kubectl patch`** of shared prod infra. The Flux-only invariant held under pressure; the GitOps fix (correct the values, push, let helm-controller converge) was the right path and was not meaningfully slower given there was no outage.

## What's still open

- `X-Frame-Options` / CSP `frame-ancestors` per-app rollout (needs OIDC-iframe testing) — see open-questions.
- Optional `Permissions-Policy` global default — not added; scoped this change to the scan findings.
