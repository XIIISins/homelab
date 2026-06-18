<!-- docs/outline/services-and-purpose/microbin.md -->

# MicroBin

A public pastebin and quick file-share at **`paste.xiiisins.com`** — internet-reachable, for sharing snippets, files, and password-protected text with expiry. Anyone can create and view pastes anonymously; a small trusted "janitor" crew gets a gated admin surface for cleanup.

---

## Where it runs

A single-replica deployment in **asgard K3s**, exposed externally via the Cloudflare tunnel → Traefik. No database or object store — MicroBin keeps a local JSON DB plus attachments on an **NFS** volume (file-class, survives restarts and reschedules).

---

## Access model

MicroBin has **no user accounts** — it's anonymous by design. Access is split at the proxy:

| Path | Who |
|---|---|
| Create, view, upload, download | Anyone (anonymous) |
| `/list` (browse all pastes) | Janitors — Authentik ForwardAuth (`paste-janitors`) |
| `/admin` (delete any paste) | Janitors — ForwardAuth **+** a shared MicroBin admin password |

Only the `/list` and `/admin` routes carry the Authentik gate (a Traefik filter on those specific HTTPRoute rules); everything else is open. Because there are no accounts, a janitor can delete **any** paste — cleanup is shared duty, not per-owner ownership.

Pastes can opt into encryption: the *secret* level is client-side zero-knowledge (the server can't read it — use it for passwords), the *private* level is server-side and hidden from `/list`. Default is plaintext. URLs are hashed (unguessable), and there's no URL-based deletion — all deletion goes through the gated admin.

---

## Why MicroBin

Chosen over PrivateBin, Cryptgeon, Opengist, and others because it was the only single tool covering the full need in one box: syntax highlighting **and** file/image sharing **and** password sharing **and** expiry **and** opt-in encryption. The tradeoff for that coverage is that it's accountless — hence the proxy-level janitor gate and shared-cleanup model rather than per-user ownership.

---

## See also

- **Edge** (Components) — the Cloudflare tunnel path and how a single route can be partially gated.
- **Identity & secrets** (Components) — the `paste-janitors` group and Authentik ForwardAuth.
- **Storage & data** (Components) — the NFS file-class tier MicroBin's JSON DB lives on.
