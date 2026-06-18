<!-- docs/services/startpage.md -->
# Startpage

Personal browser-homepage served at **`home.xiiisins.com`** (public, external-only).
A static site (the [`XIIISins/startpage`](https://github.com/XIIISins/startpage) repo,
branch `tokyonight_colors`) served by Caddy in asgard K3s.

- **Manifests:** `k8s/asgard/apps/startpage/`
- **Hostname:** `home.xiiisins.com` — external-only, via cloudflared. No LAN bypass, no auth.
- **Replicas:** 3 (one per worker, required pod anti-affinity).

## Why this shape

- **Git-clone at pod start, not files-in-ConfigMap.** The repo is ~47MB (large
  `background.png`, GIF banners, icon fonts) — well over the 1MB ConfigMap cap that
  `apex-static` uses for WebFinger. An init container clones the repo into a shared
  `emptyDir`; Caddy serves it.
- **Separate app from `apex-static`.** `apex-static` serves WebFinger, which is
  OIDC-critical (Tailscale custom-OIDC discovery). Folding the startpage in would mean
  a failed clone could block that pod from starting, and every content update would
  bounce WebFinger. A dedicated app keeps the failure domains apart.
- **Whitelist, not blacklist, for the webroot.** The init container copies only the
  page's required paths — `index.html`, `userconfig.js`, `src`, `img` (the same set
  the old nginx `Dockerfile` `COPY`ed) — into `/srv/www`. Nothing else from the repo
  (`.git/`, `README.md`, `LICENSE`, `Dockerfile`, `.eslintrc.json`, …) can ever be
  served, now or if the repo grows new files.

## Secrets — read-only deploy key

The repo is private, so the init container clones over SSH with a **read-only ed25519
deploy key**:

- Vault: `secret/k8s/startpage/deploy-key` — fields `ssh-privatekey` + `known_hosts`
  (a pinned `ssh-keyscan github.com` so the clone uses `StrictHostKeyChecking=yes`,
  no TOFU). Operator-minted (not TF — ssh-keygen output isn't a `random_password`).
- 1Password offline mirror: `[Asgard] - Startpage - GitHub deploy key`.
- GitHub: deploy key titled `asgard-startpage (read-only)` on `XIIISins/startpage`.
- ESO `ExternalSecret` `startpage-deploy-key` materialises the K8s Secret; the init
  container mounts it at `/keys`, copies the key to a tmp path at `0400` (SSH refuses
  the symlinked secret-mount perms otherwise), and clones.

Rotating the key: `ssh-keygen` a new pair → `gh repo deploy-key add` the public half
(and delete the old) → `vault kv put secret/k8s/startpage/deploy-key ssh-privatekey=@…
known_hosts=@…` → update the 1P mirror → `kubectl rollout restart deployment/startpage
-n startpage`.

## Routing (external)

```
browser → Cloudflare edge → tunnel → cloudflared pod → Traefik → startpage Caddy pod
```

- **Cloudflare DNS:** `cloudflare_dns_record.startpage` in `terraform/cloudflare/main.tf`
  — `home.xiiisins.com` CNAME → tunnel (proxied).
- **cloudflared:** ingress rule `home.xiiisins.com → https://traefik.traefik.svc:443`
  (`httpHostHeader` + `noTLSVerify`) in `k8s/asgard/infrastructure/cloudflared/configmap.yaml`.
  cloudflared reads config only at startup — `kubectl rollout restart deployment/cloudflared
  -n cloudflared` after any ingress change.
- **HTTPRoute:** attaches to the midgard Gateway `websecure-apex-wildcard` listener
  (the same listener `wiki.xiiisins.com` uses).

Caddy serves plain HTTP on `:80` inside the pod; TLS terminates at Traefik upstream
(`*.xiiisins.com` LE wildcard). There is no AGH rewrite — LAN clients trombone out to
Cloudflare and back (accepted; it's a tiny page).

## Updating the page

1. Push your change to the `XIIISins/startpage` repo (branch `tokyonight_colors`).
2. `kubectl rollout restart deployment/startpage -n startpage`

Each new pod re-clones HEAD of the branch and republishes the whitelist. The rolling
restart (`maxSurge: 0` / `maxUnavailable: 1`) keeps the service up throughout. No image
build, no registry.

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
- **Pod won't start / `CreateContainerConfigError`** — ESO hasn't resolved
  `secret/k8s/startpage/deploy-key`. Check `kubectl get externalsecret -n startpage`
  and that the Vault path has both fields.
- **Init container clone fails** — check `kubectl logs -n startpage <pod> -c clone`.
  Usual causes: deploy key revoked/rotated on GitHub, or the branch was renamed.
