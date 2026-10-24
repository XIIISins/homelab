<!-- docs/services/startpage.md -->
# Startpage

<!-- The diagram (diagrams/startpage.*) still shows the old git-clone flow and is not embedded until redrawn: open-questions.md "Startpage ghcr follow-ups". -->

Personal browser-homepage served at **`home.xiiisins.com`** (public, external-only).
A static site (the [`XIIISins/startpage`](https://github.com/XIIISins/startpage) repo,
branch `master`) built by that repo's CI into the private image
`ghcr.io/xiiisins/startpage` (nginx) and run in asgard K3s.

- **Manifests:** `k8s/asgard/apps/startpage/`
- **Hostname:** `home.xiiisins.com` — external-only, via cloudflared. No LAN bypass, no auth.
- **Replicas:** 3 (one per worker, required pod anti-affinity).

## Why this shape

- **Image, sha-pinned.** The repo's `image` workflow publishes `latest` and
  `sha-<short>` on every push to `master`. The Deployment pins the sha tag, so a
  rollout is a reviewed git change and Flux can revert it. No runtime git access.
- **Separate app from `apex-static`.** `apex-static` serves WebFinger, which is
  OIDC-critical (Tailscale custom-OIDC discovery). Folding the startpage in would mean
  a bad image could block that pod from starting, and every content update would
  bounce WebFinger. A dedicated app keeps the failure domains apart.

## Secrets — ghcr pull token

The package is private, so the Deployment pulls with an `imagePullSecret`:

- Vault: `secret/k8s/startpage/ghcr` — fields `username` + `token`. The token is a
  **classic PAT with only `read:packages`** (ghcr accepts neither fine-grained PATs nor
  the repo deploy key). Operator-minted, not TF.
- 1Password offline mirror: the PAT's 1P item (Homelab vault).
- ESO `ExternalSecret` `startpage-ghcr-pull` renders it as a `dockerconfigjson` Secret.

Rotating: mint a new PAT → `vault kv put secret/k8s/startpage/ghcr username=… token=…`
→ update the 1P mirror. ESO refreshes hourly; running pods keep working, new pods use
the new token. An expired PAT shows as `ImagePullBackOff` on the next reschedule.

## Routing (external)

```
browser → Cloudflare edge → tunnel → cloudflared pod → Traefik → startpage nginx pod
```

- **Cloudflare DNS:** `cloudflare_dns_record.startpage` in `terraform/cloudflare/main.tf`
  — `home.xiiisins.com` CNAME → tunnel (proxied).
- **cloudflared:** ingress rule `home.xiiisins.com → https://traefik.traefik.svc:443`
  (`httpHostHeader` + `noTLSVerify`) in `k8s/asgard/infrastructure/cloudflared/configmap.yaml`.
  cloudflared reads config only at startup — `kubectl rollout restart deployment/cloudflared
  -n cloudflared` after any ingress change.
- **HTTPRoute:** attaches to the midgard Gateway `websecure-apex-wildcard` listener
  (the same listener `wiki.xiiisins.com` uses).

nginx serves plain HTTP on `:80` inside the pod; TLS terminates at Traefik upstream
(`*.xiiisins.com` LE wildcard). There is no AGH rewrite — LAN clients trombone out to
Cloudflare and back (accepted; it's a tiny page).

## Updating the page

1. Merge your change to `XIIISins/startpage` `master`; wait for its `image` workflow.
2. Find the new tag (`sha-<short>`; the short commit sha) and bump `image:` in
   `k8s/asgard/apps/startpage/deployment.yaml` via a PR. Flux rolls it out.

The rolling update (`maxSurge: 0` / `maxUnavailable: 1`) keeps the service up.

## Troubleshooting

- **`home.xiiisins.com` resolves on `dig` but `curl`/browser fails (`000`)** — client
  OS negative-DNS cache (macOS `mDNSResponder`) holding a stale NXDOMAIN from before the
  CF record existed. `dig` queries the nameserver directly and bypasses it. Fix:
  `sudo killall -HUP mDNSResponder`, or wait out the apex SOA negative-TTL (1800s).
- **`dig @10.0.10.200` returns NXDOMAIN while `@1.1.1.1` is NOERROR** — AGH negative
  cache. Flush all three nodes: `curl -u "$ADGUARD_USERNAME:$ADGUARD_PASSWORD" -X POST
  http://10.0.11.20{1,2,3}/control/cache_clear`.
- **Prove the server path independent of DNS:** `curl --resolve home.xiiisins.com:443:<cf-edge-ip>
  https://home.xiiisins.com/` (a CF edge IP from `dig home.xiiisins.com @1.1.1.1`).
- **`ImagePullBackOff` / `ErrImagePull`** — the pull token. `kubectl get externalsecret
  -n startpage` (must be `SecretSynced`), that `secret/k8s/startpage/ghcr` has both
  fields, and that the PAT hasn't expired or lost `read:packages`. Or the tag does not
  exist: check the repo's `image` workflow.
