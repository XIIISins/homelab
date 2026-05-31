<!-- docs/services/microbin.md -->
# MicroBin

Public pastebin + file-share at **`paste.xiiisins.com`** (internet-exposed via
cloudflared). Anonymous create/view/upload; a trusted "janitor" crew gets the
gated `/pastalist` + `/admin` for cleanup.

- **Manifests:** `k8s/asgard/apps/microbin/`
- **Image:** `danielszabo99/microbin:2.1.4` (the official image — repo README badges)
- **Hostname:** `paste.xiiisins.com` — external-only, internet-public.

## Why MicroBin (tool selection)

Picked over PrivateBin, Cryptgeon, Opengist, Hemmelig, Pastefy after a
requirements pass. The need was: syntax highlighting (for scripts) **and**
file/image sharing with expiry **and** password sharing with expiry **and** a
private/janitor surface. No tool nails *all* of: highlighting + files +
password/expiry + native OIDC accounts. MicroBin was the only single tool
covering the feature set (highlighting + files + password + expiry, opt-in
encryption) — the cost is it's **accountless**, so the "private section" is a
proxy-level gate (Authentik ForwardAuth on specific routes) and cleanup is
*shared janitor duty* rather than per-user ownership. The decision thread also
considered PrivateBin (zero-knowledge-by-default, dated UI, no janitor list)
and Cryptgeon (modern UI, no highlighting); MicroBin won on covering every
stated use in one box.

## Access model

| Path | Access |
|---|---|
| `/`, `/upload`, paste view, `/static`, file download | **open / anonymous** |
| `/pastalist` (browse paste list/metadata) | **Authentik ForwardAuth** (`paste-janitors`) |
| `/admin` (manage + delete any paste) | **Authentik ForwardAuth** (`paste-janitors`) + MicroBin admin login |

Partial gating is done at Traefik: the HTTPRoute has specific `/pastalist` +
`/admin` rules carrying the `authentik-forward-auth` Middleware via Gateway-API
`extensionRef`; the `/` catchall has none (Gateway-API longest-prefix matching
picks the specific rules first). This is the inverse of the VictoriaLogs route
(which *exempts* `/insert` from an otherwise-gated app). The Middleware is a
same-namespace copy of `monitoring/authentik-forward-auth` (extensionRef
resolves within the route's namespace).

Because MicroBin has no user accounts, a janitor can delete **any** paste, not
just their own — shared cleanup, by design. `MICROBIN_EDITABLE=false` means
there's no URL-based deletion at all; all deletion flows through the gated
`/admin`. Per-paste *private* pastes (`MICROBIN_PRIVATE=true`) are hidden from
`/pastalist` even for janitors — those are managed via `/admin` (sees all) or
just expire.

## Config (`configmap.yaml` + admin secret)

Key settings: `JSON_DB=true`, `PRIVATE=true`, `EDITABLE=false`,
`ENABLE_BURN_AFTER=true`, `HASH_IDS=true` (unguessable URLs),
`MAX_FILE_SIZE_*_MB=256`, `DEFAULT_EXPIRY=24hour`, `MAX_EXPIRY=never`,
`PUBLIC_PATH=https://paste.xiiisins.com`, telemetry/update-check off.

Per-paste encryption is **opt-in**: the *secret* level is client-side
(zero-knowledge — the server, and you, can't read it); the *private* level is
server-side; default is plaintext. Use the secret level for passwords.

Admin credentials (`MICROBIN_ADMIN_USERNAME/PASSWORD`) are TF-minted to Vault
`secret/k8s/microbin/admin` and projected via ESO. The `/admin` interface is
double-gated: Authentik ForwardAuth (group) *and* the MicroBin admin password
(shared among janitors).

## Storage

MicroBin is local-disk only (no S3/Postgres backend). JSON DB + attachments
live on **NFS** (`nfs-client`, RWO 20Gi, survives crashes/reboots,
reschedulable). 1 replica, `Recreate` strategy (single-writer JSON DB).

**Gotcha — the JSON-DB path is hardcoded.** `src/util/db_json.rs` pins
`pasta_data/database.json` (relative to `WORKDIR /app`) and ignores
`MICROBIN_DATA_DIR` (which only moves *attachments*). So the NFS PV is mounted
at **`/app/pasta_data`** and `MICROBIN_DATA_DIR` points there too, co-locating
DB + attachments on the one volume. (SQLite mode honors `data_dir`, but
SQLite-on-NFS is the mmap/locking footgun; JSON's tmp-write+atomic-rename is
NFS-safe.) The NFS export allows root, so a root init-container chowns
`/app/pasta_data` to `1000:1000` and MicroBin runs as non-root `1000` — same
pattern as Garage.

## Routing (external)

```
browser → Cloudflare edge → tunnel → cloudflared → Traefik → microbin
```
- CF CNAME `cloudflare_dns_record.microbin` (`terraform/cloudflare/main.tf`).
- cloudflared ingress `paste.xiiisins.com → Traefik backchannel`
  (`k8s/asgard/infrastructure/cloudflared/configmap.yaml`) — restart cloudflared
  after any ingress change.
- HTTPRoute on midgard `websecure-apex-wildcard`.

## Authentik (`terraform/authentik/microbin.tf`)

`forward_single` proxy provider (`external_host=https://paste.xiiisins.com`) +
application + policy binding gating on the `paste-janitors` group (membership in
`users.yaml`). **One-time manual step after `terraform apply`:** attach the
MicroBin provider to the embedded outpost (TF doesn't manage it) —
`curl -X PATCH .../api/v3/outposts/instances/<embedded-pk>/ -d
'{"providers":[<existing…>,<microbin-pk>]}'`, or via the UI (Applications →
Outposts → edit embedded → add MicroBin). Forgetting it = gated routes loop on
login / 500 from the outpost.

## Troubleshooting

- **Pod crashloops on `db_json.rs … failed to create temporary database file`** —
  the NFS PV isn't mounted at `/app/pasta_data` (the hardcoded DB dir). See the
  storage gotcha.
- **`/pastalist` or `/admin` returns 200 to anonymous / never asks for login** —
  the embedded-outpost provider attach was missed, or the Middleware isn't on
  that route. Gating is at Traefik; curling the Service directly always returns
  200 (test through the tunnel: should 302 → Authentik).
- **`paste.xiiisins.com` resolves on `dig` but `curl`/browser 000** — client
  (macOS `mDNSResponder`) negative-DNS cache from before the CF record existed.
  `sudo killall -HUP mDNSResponder`, or wait out the 1800s apex SOA neg-TTL. AGH
  caches it too — flush via `cache_clear` on all three nodes (see the DNS gotcha
  in CLAUDE.md). Prove the server path independent of DNS with
  `curl --resolve paste.xiiisins.com:443:<cf-edge-ip> https://paste.xiiisins.com/`.
- **Verify config a build actually honors** — MicroBin docs are unreliable on
  defaults/var names. Probe the image (`kubectl run … --command -- sh -c
  'microbin --help; microbin --version'`) or grep the source at the matching tag.
