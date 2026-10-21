<!-- docs/services/php-watch.md -->
# PHP watch

A one-screen page answering "has a PHP fix shipped yet, and where?", served at **`php-watch.niflheim.xiiisins.com`**.
Same look as the [overview page](overview.md) (Tokyo Night tokens, verdict card, status pill, light/dark switch), same
trust level: internal-only, no sign-in, read-only.

- **Manifests:** `k8s/asgard/apps/php-watch/` (+ `k8s/asgard/vpa-config/php-watch.yaml`)
- **DNS:** `php-watch.niflheim.xiiisins.com` → `10.0.20.10` in `terraform/adguard/rewrites.tf`. Not public: no Cloudflare
  record and no tunnel rule. The record needs a `terraform apply` from the main checkout after the PR merges.
- **Runtime:** the official `php:<version>-cli-alpine` image running PHP's built-in web server
  (`PHP_CLI_SERVER_WORKERS=4`), non-root, read-only root filesystem, 2 replicas (one per worker). The site is a hashed
  ConfigMap, so an edit rolls the pods. No image build.
- **Auth:** none. It fetches public pages on a fixed host list; CVE ids are validated against `CVE-YYYY-NNNN+` and at most
  eight are taken per request, so a request cannot make the pod fetch anything else.

## Sources

None of these send CORS headers a browser could use (and some are huge: the Debian tracker's JSON dump is 80+ MB), so the
PHP backend (`site/api.php`, `lib.php`, `sources.php`) fetches them, boils each down, caches it and serves one JSON document.

| Source | What it answers |
|---|---|
| [php.net](https://www.php.net/releases/) `releases/active.php`, `ChangeLog-8.php` | The latest release per active branch, and which releases name a watched CVE in their changelog |
| [Debian security tracker](https://security-tracker.debian.org/tracker/) + `sources.debian.org` | Per CVE: fixed / vulnerable per release (the `(security)` upload supersedes the plain row). Per package: versions per suite, `proposed-updates` queue, open issues |
| [Ubuntu](https://ubuntu.com/security/cves) CVE JSON | Per CVE: status for jammy, noble and newer (older ESM releases are left out, they would stay "vulnerable" forever) |
| [Red Hat](https://access.redhat.com/security/security-updates/cve) CVE API | Per CVE: RHEL 8 to 10 (fixed with advisory, or the fix state) |
| [Alpine](https://security.alpinelinux.org/) secdb (v3.23, v3.24) | Per CVE: which `php8x` package version fixed it. Lags the others |
| [Sury](https://packages.sury.org/php/) `Packages.gz` (bookworm, trixie) and the Ubuntu PPA index (jammy, noble) | Which PHP each Sury repo ships today |
| [Docker official image](https://hub.docker.com/_/php) tags | Newest `X.Y.Z-cli-alpine` per branch (what the pod's own image tracks) |

## What it shows

1. **A headline** (`PHP Patched` / `PHP: N to Watch` / `PHP: N Issues` / `PHP: No Data`) with a summary, or up to four
   attention rows then `+N more`. Red = a watched CVE is vulnerable in a stable release of Debian, Ubuntu or RHEL; yellow =
   listed but not fixed anywhere yet, no record anywhere yet, repos behind php.net, or open Debian issues; green = nothing
   to watch.
2. **Watched CVEs.** One card per id: description, a php.net line (named in the changelog of which release, or not yet; the
   changelog sometimes cites a GHSA id instead, so "not named" is not "not fixed"), one block per source with rows, and a
   single "No record at …" line for the rest. The list defaults to `DEFAULT_CVES` in `site/sources.php` and is edited in the
   browser (add box, × on each card, kept in `localStorage`, per browser).
3. **Latest PHP per repo.** A matrix of repos × PHP 8.2–8.5, each cell compared with php.net's latest patch release for the
   branch (on the upstream part of the version, so `8.4.26-1~deb13u1` counts as 8.4.26). Amber = behind. Debian and Docker
   trail a release by days; Sury follows within hours.
4. **Debian queue and open issues** per Debian source package (`php8.2`, `php8.4`, `php8.5`).

A source that cannot be reached turns the status pill amber and names the host; the rest of the page keeps working.

## How it fetches

Roughly 20 + 3 per CVE requests, in parallel with `curl_multi`, through a file cache in `/tmp` (an emptyDir). Big or binary
sources (the 750 KB changelog, the gzipped Sury package indexes, the Ubuntu and Red Hat JSON) go through a *reducer* before
they are cached, so the cache holds only the small form. A page is reused for 15 minutes (`TTL`), the Launchpad-hosted PPA
index for an hour (`SLOW_HOSTS`: it is slow and flaky, 503s included). A failed fetch is retried once, then a stale copy is
served. The page re-reads every 5 minutes. Failures are logged to stderr (`php-watch: <url> -> HTTP <code>`).

To watch another Debian source package, add it to `DEBIAN_PACKAGES` in `site/sources.php`. Not covered: Remi's RPM repo
(no CVE API; its metadata is multi-MB XML).
