<!-- docs/services/php-watch.md -->
# PHP watch

A one-screen **public** page answering "has a PHP fix shipped yet, and where?", at **`php-watch.xiiisins.com`**. Same look
as the [overview page](overview.md) (Tokyo Night tokens, verdict card, status pill, light/dark switch), but unlike it,
reachable by anyone and with no sign-in.

- **Manifests:** `k8s/asgard/apps/php-watch/` (+ `k8s/asgard/vpa-config/php-watch.yaml`)
- **Public path:** Cloudflare edge → tunnel → cloudflared → Traefik (midgard apex listener) → the pods, like
  `paste.xiiisins.com`. Three places carry the hostname: the HTTPRoute (`httproute.yaml`), the cloudflared ingress rule
  (`infrastructure/cloudflared/configmap.yaml`) and the Cloudflare CNAME (`cloudflare_dns_record.php_watch` in
  `terraform/cloudflare/main.tf`). No AdGuard rewrite: LAN clients resolve the public name like everyone else.
- **Runtime:** stock `node:<version>-alpine` (Node 24 LTS), 2 replicas (one per worker), non-root, read-only root
  filesystem. **No image build:** the code is two hashed ConfigMaps (`php-watch-site`, `php-watch-server`), so an edit
  rolls the pods. Node runs the TypeScript directly (type stripping), with **no dependencies**.
- **Auth:** none, by design. See "Going public".

## Code

`k8s/asgard/apps/php-watch/server/` (TypeScript, run as `node server.ts`) and `site/` (the browser page: plain JS, HTML,
CSS, self-hosted font).

| File | Job |
|---|---|
| `server.ts` | HTTP server: static allowlist, `/api/status`, `/healthz`, `/readyz`, headers, rate limits, background warm-up |
| `sources.ts` | One reducer and one parser per source, and `build()`, which assembles the JSON the page reads |
| `fetcher.ts` | Cached fetch: stale-while-revalidate, one shared request per URL, retry, failure back-off, reduce-before-cache |
| `html.ts` | A small table reader for the Debian tracker's flat HTML (no HTML-parser dependency) |
| `versions.ts` | `upstreamOf`, numeric and dpkg version comparison |
| `ratelimit.ts` | A per-client window limiter and a shared token bucket |
| `types.ts` | The shape of `/api/status` |
| `test/*.test.ts`, `tsconfig.json` | Not shipped (kept out of the ConfigMap) |

Checks, from `server/`: `node --test "test/*.test.ts"` (36 tests: parsers against fixtures, the cache and its failure
modes, the limiters, `build()` end to end, the server's hardening) and `tsc -p .` (type-check only; `erasableSyntaxOnly` keeps
the code inside what type stripping can run, so no enums, namespaces or constructor parameter properties). Neither runs in
CI yet.

## Sources

None of these send CORS headers a browser could use (and some are huge: the Debian tracker's JSON dump is 80+ MB), so the
server fetches them, boils each down, caches it and serves one JSON document.

| Source | What it answers |
|---|---|
| [php.net](https://www.php.net/releases/) `releases/active.php`, `ChangeLog-8.php` | The latest release per active branch (the changelog is the fallback when `active.php` 5xxs, which it does), and which releases name a watched CVE |
| [Debian security tracker](https://security-tracker.debian.org/tracker/) + `sources.debian.org` | Per CVE: fixed / vulnerable per release (the `(security)` upload supersedes the plain row). Per package: versions per suite, the `proposed-updates` queue, open issues |
| [Ubuntu](https://ubuntu.com/security/cves) CVE JSON | Per CVE: status for jammy, noble and newer (older ESM releases are left out, they would stay "vulnerable" forever) |
| [Red Hat](https://access.redhat.com/security/security-updates/cve) CVE API | Per CVE: RHEL 8 to 10 (fixed with advisory, or the fix state) |
| [Alpine](https://security.alpinelinux.org/) secdb (v3.23, v3.24) | Per CVE: which `php8x` package version fixed it. Lags the others |
| [Sury](https://packages.sury.org/php/) `Packages.gz` (bookworm, trixie) and the Ubuntu PPA index (jammy, noble) | Which PHP each Sury repo ships today. The two indexes order their fields differently, so each paragraph is read by field name |
| [Docker official image](https://hub.docker.com/_/php) tags | Newest `X.Y.Z-cli-alpine` per branch |

Not covered: Remi's RPM repo (no CVE API; its metadata is multi-MB XML).

## What it shows

1. **A headline** (`PHP Patched` / `PHP: N to Watch` / `PHP: N Issues` / `PHP: No Data`) with a summary, or up to four
   attention rows then `+N more`. Red = a watched CVE is vulnerable in a stable release of Debian, Ubuntu or RHEL; yellow =
   listed but not fixed anywhere yet, no record anywhere yet, repos behind php.net, open Debian issues, or the lookup limit
   hit; green = nothing to watch.
2. **Watched CVEs.** One card per id: description, a php.net line (named in the changelog of which release, or not yet; the
   changelog often cites a GHSA id instead, so "not named" is not "not fixed"), one block per source with rows, and one "No
   record at …" line for the rest. The default list is `DEFAULT_CVES` in `server/sources.ts`; visitors edit their own in the
   browser (add box, × on each card), kept in their `localStorage` only.
3. **Latest PHP per repo.** A matrix of repos × PHP 8.2–8.5, each cell compared with php.net's latest patch release for the
   branch on the upstream part of the version (`8.4.26-1~deb13u1` counts as 8.4.26). Amber = behind.
4. **Debian queue and open issues** per Debian source package (`php8.2`, `php8.4`, `php8.5`).

A source that cannot be reached turns the status pill amber and names the host; the rest keeps working.

## How it fetches

About 20 pages plus 3 per CVE, in parallel, through an in-memory cache. A page is fresh for 15 minutes (an hour for the
Launchpad-hosted PPA index, which is slow and returns 503 now and then). A stale page is served at once and refreshed behind
the request; a failed refresh is retried once, the old copy keeps being served and is flagged stale, and a source with no copy
is remembered as down for a minute so it is not hammered. Concurrent callers share one request. Big or binary bodies (the
750 KB changelog, the gzipped package indexes, the Ubuntu and Red Hat JSON) are reduced *before* they are cached. The
default list is re-fetched in the background every 10 minutes and once at start; `/readyz` is 503 until that first pass ends
(or 45 s), so a new pod never serves a visitor cold. Failures go to stderr (`php-watch: <url> -> <reason>`).

To watch another Debian source package, add it to `DEBIAN_PACKAGES` in `server/sources.ts`.

## Going public

Anonymous callers can only do what the one JSON endpoint lets them, and that is bounded in the app:

- **Methods and paths:** GET/HEAD only; a fixed allowlist of six static files, read into memory at start (no file is opened
  per request, so no path traversal); URLs over 2 KB refused.
- **Outbound:** every URL is built from a fixed host list; a CVE id must match `CVE-YYYY-NNNN+`; at most 8 per request; https
  only, 3 redirects, 4 MB per body. A visitor cannot make the pod fetch anything else.
- **Amplification:** the default list is served from the background-refreshed cache, so ordinary visitors cause no outbound
  request. CVE ids outside it cost each upstream three requests, so they draw on one shared budget (10 new ids a minute, for
  everyone together); beyond it the card says "Lookup limit reached". Already-cached ids are free.
- **Per client:** 60 `/api/status` requests a minute (keyed on `CF-Connecting-IP`, else `X-Forwarded-For`), then `429`.
- **Browser side:** a strict CSP (`default-src 'none'`, scripts, styles and fonts from self, no inline anything), no cookies,
  all third-party text inserted with `textContent`. Traefik's entrypoint middleware adds HSTS, nosniff and the referrer policy
  on top ([incident](../incidents/2026-06-27-traefik-security-headers.md)).
- **Not done:** a Cloudflare rate-limiting rule (an unapplied zone change, see
  [`known-issues/cloudflare.md`](../known-issues/cloudflare.md) for how the zone is managed), and a Gatus probe on `do1`. Both
  are tracked in [`open-questions.md`](../operations/open-questions.md).

## Rolling it out (operator)

The PR changes three systems; order matters because cloudflared's ConfigMap has a static name and is not hashed:

1. Merge the PR. Flux deploys the pods, the Service, the HTTPRoute and the new cloudflared ConfigMap.
2. `terraform apply` in `terraform/cloudflare/` from the main checkout (the proxied CNAME).
3. `kubectl rollout restart deployment/cloudflared -n cloudflared` so the pods read the new ingress rule.
4. Check `https://php-watch.xiiisins.com/healthz` from outside the LAN and the page's status pill.
