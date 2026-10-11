<!-- docs/outline/services-and-purpose/startpage.md -->

# Startpage

The operator's personal browser homepage at `home.xiiisins.com`: a static page of bookmarks. The status-and-launcher view for homelab apps is a different page, the **Overview page**, which links back here as "Bookmarks".

![Startpage: a static page cloned from Git at pod start](/api/attachments.redirect?id=8d7071f4-31e6-47f7-99da-04356634e838)

---

## How it works

Caddy serves the static site from three pods, one per worker. At pod start an init container clones the (private) startpage repository over SSH with a read-only deploy key and copies only an explicit whitelist of files into the web root, so nothing else in the repository can ever be served. It is cloned rather than packed into a ConfigMap because the repo, with its images and fonts, is far over a ConfigMap's size limit.

It is a separate app from the small static-file service that serves WebFinger (needed for Tailscale's OIDC discovery) on purpose: a failed clone must not be able to stop WebFinger from starting.

Public through the Cloudflare tunnel only, with no sign-in. To publish a change: push to the repository, then restart the deployment so the pods re-clone.

---

## See also

- **Overview page** (this section): the status page and app launcher.
- **Edge** (Components): the public path.
- **Startpage diagram** (Diagrams): the pod, the init clone and the deploy key.
