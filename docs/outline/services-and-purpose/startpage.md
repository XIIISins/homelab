<!-- docs/outline/services-and-purpose/startpage.md -->

# Startpage

A personal browser homepage at **`home.xiiisins.com`** — the landing/dashboard page for a new tab. Public and external-only, no login; it's a tiny static site, so there's nothing to gate.

---

## Where it runs

A static site served by **Caddy in asgard K3s** (3 replicas, one per worker). The page content lives in a private GitHub repo (`XIIISins/startpage`), not in the cluster — each pod **clones the repo at startup** into an `emptyDir` and Caddy serves it. Updating the page is a push to the repo + a rollout restart; no image build, no registry.

Reached from anywhere via the Cloudflare tunnel → Traefik → Caddy. TLS terminates at Traefik on the `*.xiiisins.com` wildcard.

---

## Why this shape

- **Clone-at-startup, not files-in-ConfigMap.** The repo is ~47 MB (background image, banners, icon fonts) — far over the 1 MB ConfigMap limit the WebFinger static site uses. An init container clones it instead.
- **Separate app from the apex static site.** The apex static pod serves WebFinger, which is OIDC-critical (Tailscale discovery). Keeping the startpage in its own app means a failed clone or a content update can't disturb that. Different failure domains.
- **Whitelisted webroot.** The init container copies only the page's required paths into the webroot — nothing else from the repo (`.git/`, license, build files) can ever be served, even if the repo grows.
- **Read-only deploy key.** The private repo is cloned over SSH with a read-only ed25519 deploy key (Vault `secret/k8s/startpage/deploy-key`, 1P mirror), pinned `known_hosts` so there's no trust-on-first-use.

---

## See also

- **Edge** (Components) — the Cloudflare tunnel → Traefik path every public service shares.
- **Identity & secrets** (Components) — where the deploy key lives and the mint-to-Vault-mirror-to-1P pattern.
- **MicroBin** — the other small public-facing utility in the same external-only shape.
