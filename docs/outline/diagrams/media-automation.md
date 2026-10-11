<!-- docs/outline/diagrams/media-automation.md -->

# Media automation diagram

Sonarr, SABnzbd and Recyclarr. Anime only, Usenet only: Sonarr finds, SABnzbd downloads, Sonarr imports by rename, Jellyfin reads.

![Media automation: Sonarr, SABnzbd and Recyclarr](/api/attachments.redirect?id=4fdb90ac-bb5a-467a-a126-8d17ca362606)

---

## How to read it

- **Top row: outside the lab.** A Usenet provider (TLS, several connections) supplies articles to SABnzbd. A Newznab indexer (anime category) feeds Sonarr by search and RSS. Sonarr sends the NZB to SABnzbd.
- **The `media` namespace (dashed box).** SABnzbd and Sonarr each run one replica with their configuration on `local-path` (Sonarr's is SQLite). SABnzbd is capped at 2 Gi of memory and unpacks directly. A Recyclarr CronJob syncs the TRaSH anime profile into Sonarr daily.
- **Left: how the operator gets in.** Traefik with Authentik ForwardAuth (group `media-admins`) in front of both UIs. API keys and the Usenet and indexer credentials come from Vault through External Secrets.
- **Bottom: files.** Both pods use the one media share on Munin (NFS). Downloads land in `Downloads/incomplete`, move to `complete/anime`, and Sonarr imports into `Anime/<Series>/Season NN/`. The import is a rename, not a copy, because everything is on one share.
- **Bottom right: the consumer.** The Jellyfin LXC reads `Anime/` read-only, and Sonarr tells it to refresh.
- **Top right: notifications.** Sonarr posts grabs, imports and upgrades straight to Discord `#media` over its own webhook. It skips Hermod on purpose because it cannot send Apprise's payload.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[media-automation.excalidraw 44540](/api/attachments.redirect?id=aad161a9-602c-470f-b811-6f3651236c41)

[media-automation.svg 54492](/api/attachments.redirect?id=1d6a2615-fa59-4ec2-911e-de44b9f8213d)

---

## See also

- **Media automation** (Services): the service page.
- **Jellyfin** (Services): the consumer of the library.
- **Hermod** (Services): why media events skip it.
