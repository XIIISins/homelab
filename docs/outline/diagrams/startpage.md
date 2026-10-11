<!-- docs/outline/diagrams/startpage.md -->

# Startpage diagram

A static page cloned from Git at pod start. Public, no auth, three replicas (one per worker), and each pod clones the page itself.

![Startpage: a static page cloned from Git at pod start](/api/attachments.redirect?id=8d7071f4-31e6-47f7-99da-04356634e838)

---

## How to read it

- **Top row: the request path.** A browser at `home.xiiisins.com` goes through the Cloudflare tunnel (external only) and Traefik's Gateway API to Caddy in the pod.
- **The pod (dashed box, three copies, required anti-affinity).** Caddy serves `/srv/www` from an `emptyDir`. An init container clones the repository over SSH and copies a whitelist of files into that directory.
- **Bottom row: how the clone is authorised.** Vault holds a read-only deploy key and the pinned `known_hosts`; External Secrets mounts them as a Secret at `/keys`. The init container uses them to clone the private startpage repository on GitHub (about 47 MB, mostly big images).
- **Why a Git clone, not a ConfigMap:** the repository is far over the 1 MB ConfigMap limit.
- **Why a webroot whitelist:** `.git`, the README and the Dockerfile can never be served.
- **Why it is not part of the apex static site:** a failed clone must never block the WebFinger page that Tailscale's OIDC discovery depends on.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[startpage.excalidraw 34867](/api/attachments.redirect?id=92ffa3ac-e886-4176-9fca-8c6f0c114a2d)

[startpage.svg 47097](/api/attachments.redirect?id=76f47fe6-c637-481b-86c0-1220522ebcf3)

---

## See also

- **Startpage** (Services): the service page.
- **Edge** (Components): the public path.
