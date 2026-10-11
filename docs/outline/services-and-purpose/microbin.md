<!-- docs/outline/services-and-purpose/microbin.md -->

# MicroBin

A pastebin and file-share at `paste.xiiisins.com`: paste a script or share a file, with optional password, expiry and encryption. Anyone can create and view; a small trusted crew gets the cleanup pages.

![MicroBin: public pastebin with a gated janitor door](/api/attachments.redirect?id=157e6443-751e-48a6-8592-b8b2c54add16)

---

## Why this tool

Several pastebins were compared against four needs at once: syntax highlighting, file sharing with expiry, password sharing with expiry, and a private cleanup surface. MicroBin was the only single tool covering the first three. The cost is that it has **no accounts**, so the "private" part is a gate in front of specific paths, and cleanup is shared janitor duty rather than per-user ownership.

## Access

| Path | Who |
|---|---|
| Create, view, upload | Anyone on the internet |
| `/list` and `/admin` | Authentik sign-in (ForwardAuth middleware), janitor group only |

It is public through the Cloudflare tunnel only, with no LAN shortcut.

## How it runs

- A small Deployment in K3s using a **patched build** of the upstream image. Upstream has a bug in its JSON database mode where cleaning up an expired paste can panic while holding the data lock, after which every POST returns 502 while the pod still looks healthy. The patched image is built from the repository's own Dockerfile by CI.
- Data is a JSON file on an NFS volume (file-class, so the right tier).
- Estate-wide security headers apply to it like every other route.

---

## See also

- **Edge** (Components): the public path and ForwardAuth.
- **Storage & data** (Components): the NFS tier.
- **MicroBin diagram** (Diagrams): the open routes and the gated janitor routes.
