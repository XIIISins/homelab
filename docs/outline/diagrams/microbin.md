<!-- docs/outline/diagrams/microbin.md -->

# MicroBin diagram

A public pastebin with a gated janitor door. Anyone can paste and share; only the janitor crew can browse or delete, through Authentik at the proxy.

![MicroBin: public pastebin with a gated janitor door](/api/attachments.redirect?id=157e6443-751e-48a6-8592-b8b2c54add16)

---

## How to read it

- **Left: two kinds of visitor.** Anyone on the internet pastes, uploads and views. Janitors clean up pastes. Both arrive through the Cloudflare tunnel at `paste.xiiisins.com`, which is external only: there is no LAN shortcut.
- **Middle: Traefik's Gateway API routes.** The longest path wins, so the specific routes beat the catch-all. The catch-all (`/`, `/upload`, paste view, file download) is open. `/list` and `/admin` sit behind ForwardAuth for the `paste-janitors` group, which asks the Authentik outpost "who are you?".
- **The MicroBin pod.** One replica, `Recreate` strategy. It keeps a JSON database with hashed paste IDs, burn-after-read and expiry, and has no user accounts. Its data (the JSON database and attachments) is a 20 Gi NFS volume.
- **Top right: the image.** GitHub Actions builds a patched image that fixes the expired-paste panic.
- **By design:** a janitor can delete any paste (shared cleanup). URL deletion is off, so every delete goes through `/admin`, which also needs MicroBin's own admin login.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[microbin.excalidraw 37674](/api/attachments.redirect?id=b977a5f6-ffdb-4449-8d0f-9bf30fead239)

[microbin.svg 50129](/api/attachments.redirect?id=f7753793-50e2-4b10-957e-60fa08bf901f)

---

## See also

- **MicroBin** (Services): the service page.
- **Edge** (Components): the public path and ForwardAuth.
