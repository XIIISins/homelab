<!-- docs/services/php-watch.md -->
# PHP watch

A one-screen page answering "has Debian shipped a fix for this PHP CVE yet?", served at
**`php-watch.niflheim.xiiisins.com`**. Same look as the [overview page](overview.md) (Tokyo Night tokens, verdict card,
status pill, light/dark switch), same trust level: internal-only, no sign-in, read-only.

- **Manifests:** `k8s/asgard/apps/php-watch/` (+ `k8s/asgard/vpa-config/php-watch.yaml`)
- **DNS:** `php-watch.niflheim.xiiisins.com` → `10.0.20.10` in `terraform/adguard/rewrites.tf`. Not public: no Cloudflare
  record and no tunnel rule. The record needs a `terraform apply` from the main checkout after the PR merges.
- **Runtime:** the official `php:<version>-cli-alpine` image running PHP's built-in web server
  (`PHP_CLI_SERVER_WORKERS=4`), non-root, read-only root filesystem, 2 replicas (one per worker). The site is a hashed
  ConfigMap, so an edit rolls the pods. No image build.
- **Auth:** none. It fetches two public Debian sites on a fixed host list; CVE ids are validated against
  `CVE-YYYY-NNNN+` and at most eight are taken per request, so a request cannot make the pod fetch anything else.

## Why Debian, and why server-side

The question that matters to the homelab is not "has upstream PHP fixed it" but "does the Debian package I would install
carry the fix". Debian backports fixes into its own version numbers, so upstream release notes do not answer it. The
Debian security tracker does, but it sends no CORS headers (and its JSON dump is 80+ MB), so a PHP backend fetches the small
per-CVE and per-package pages, digests them and serves one JSON document. PHP is the natural fit: `curl` and `dom` ship in
the official image.

## What it shows

1. **A headline** (`PHP Patched` / `PHP: N to Watch` / `PHP: N Issues` / `PHP: No Data`) with a summary sentence, or a short
   list of what needs attention (up to four rows, then `+N more`). Red = a watched CVE is still vulnerable in a stable
   release (bookworm, trixie); yellow = Debian has no record of a CVE yet, the tracker did not answer, or a package has open
   issues; green = every watched CVE is fixed.
2. **Watched CVEs.** One card per id with the tracker's description and one row per package and release (`fixed`,
   `vulnerable`, `not-affected`…). The `(security)` upload supersedes the plain release row, so a fixed security version
   wins over the vulnerable version it replaces. The list defaults to `DEFAULT_CVES` in `site/lib.php` and is edited in the
   browser (add box and the × on each card, kept in `localStorage`, per browser).
3. **PHP in Debian.** `php8.2` (bookworm), `php8.4` (trixie) and `php8.5` (experimental): the version a host gets today
   from the security archive, every suite it sits in, anything queued in `<release>-proposed-updates` that is newer than
   what is released, and the tracker's open issues. `php8.5` is not in the tracker (no stable release ships it).

A CVE the tracker answers 404 for is shown as **"No Debian record yet"**: either too new to be triaged or not a PHP CVE.
That is a state to watch, not a pass.

## How it fetches

`api.php` → `lib.php`: up to 3 packages × 2 pages plus one page per CVE, fetched in parallel with `curl_multi`, through a
file cache in `/tmp` (an emptyDir). A page is reused for 15 minutes (`TTL`); a failed fetch is retried once, then the
stale copy is served and the status pill turns amber ("Debian unreachable, showing data from N minutes ago"). The page
re-reads every 5 minutes and when the tab becomes visible again. Failures are logged to the container's stderr
(`php-watch: <url> -> HTTP <code>`).

To watch another package, add it to `PACKAGES` in `site/lib.php` (source package → the Debian release that ships it).
