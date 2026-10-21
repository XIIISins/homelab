<?php
// One section per source. Two questions are answered:
//   "Is there a fix for CVE-X, and where?"   php.net changelog, Debian, Ubuntu, Red Hat, Alpine
//   "Which PHP does each repo ship today?"   php.net, Debian, Sury (Debian repo + Ubuntu PPA), Docker official image
// Every source is a public read-only URL on a fixed host; none of them sends CORS headers
// that would let the browser ask directly, which is why this runs server-side.
declare(strict_types=1);

require __DIR__ . '/lib.php';

const MAX_CVES = 8;
const DEFAULT_CVES = ['CVE-2026-109618', 'CVE-2026-109619'];
const BRANCHES = ['8.2', '8.3', '8.4', '8.5'];

const PHPNET_ACTIVE = 'https://www.php.net/releases/active.php';
const PHPNET_CHANGELOG = 'https://www.php.net/ChangeLog-8.php';
const DEBIAN_TRACKER = 'https://security-tracker.debian.org/tracker/';
const DEBIAN_SOURCES = 'https://sources.debian.org/api/src/';
const UBUNTU_CVE = 'https://ubuntu.com/security/cves/%s.json';
const REDHAT_CVE = 'https://access.redhat.com/hydra/rest/securitydata/cve/%s.json';
const ALPINE_SECDB = 'https://secdb.alpinelinux.org/%s/community.json';
const SURY_PACKAGES = 'https://packages.sury.org/php/dists/%s/main/binary-amd64/Packages.gz';
const SURY_PPA_PACKAGES = 'https://ppa.launchpadcontent.net/ondrej/php/ubuntu/dists/%s/main/binary-amd64/Packages.gz';
const DOCKER_TAGS = 'https://hub.docker.com/v2/repositories/library/php/tags?name=cli-alpine&page_size=100&ordering=last_updated';

// Debian source package => the Debian release that ships it and the PHP branch it is
const DEBIAN_PACKAGES = ['php8.2' => ['bookworm', '8.2'], 'php8.4' => ['trixie', '8.4'], 'php8.5' => ['experimental', '8.5']];
const DEBIAN_STABLE = ['bookworm', 'trixie'];
const SURY_SUITES = ['bookworm', 'trixie'];
const SURY_PPA_SUITES = ['jammy', 'noble'];
const ALPINE_BRANCHES = ['v3.23', 'v3.24'];
// Ubuntu releases that still get PHP fixes; older ones (ESM) would keep a CVE "vulnerable" forever.
const UBUNTU_RELEASES = ['jammy', 'noble', 'resolute', 'stonking'];
const REDHAT_RELEASE_RE = '/Enterprise Linux (8|9|10)$/';

/** "8.4.27-1+0~2026…+debian13~1.gbp7636b9", "8.2.34-1~deb12u1", "1:8.4.27-r0" -> "8.4.27". */
function upstream_of(string $v): ?string
{
    return preg_match('/^(?:\d+:)?(\d+\.\d+\.\d+)/', $v, $m) ? $m[1] : null;
}

function branch_of(string $v): ?string
{
    $u = upstream_of($v);
    return $u === null ? null : implode('.', array_slice(explode('.', $u), 0, 2));
}

// ---------- Reducers: boil a source down before it is cached ----------

/** php.net ChangeLog (750 KB of HTML) -> {"8.4.27": {date, cves: [...]}} for versions that name CVEs. */
function reduce_changelog(string $html): ?string
{
    if (!preg_match_all('~<section class="version" id="([\d.]+)">(.*?)</section>~s', $html, $m, PREG_SET_ORDER)) {
        return null;
    }
    $out = [];
    foreach ($m as [, $ver, $body]) {
        if (preg_match_all('/CVE-\d{4}-\d{4,7}/', $body, $c)) {
            preg_match("~datetime='([\d-]+)'~", $body, $d);
            $out[$ver] = ['date' => $d[1] ?? '', 'cves' => array_values(array_unique($c[0]))];
        }
    }
    return json_encode($out);
}

/** Sury Packages.gz -> {"php8.4": "8.4.27-1+…"} */
function reduce_sury(string $gz): ?string
{
    $txt = @gzdecode($gz);
    if ($txt === false || !preg_match_all('/^Package: (php8\.\d)\nVersion: (\S+)/m', $txt, $m, PREG_SET_ORDER)) {
        return null;
    }
    $out = [];
    foreach ($m as [, $pkg, $ver]) {
        $out[$pkg] ??= $ver;
    }
    return json_encode($out);
}

/** Docker Hub tag list -> {"8.4": {version, date}}: newest plain X.Y.Z-cli-alpine tag per branch. */
function reduce_docker(string $json): ?string
{
    $d = json_decode($json, true);
    if (!isset($d['results'])) {
        return null;
    }
    $out = [];
    foreach ($d['results'] as $t) {
        if (preg_match('/^(\d+\.\d+)\.(\d+)-cli-alpine$/', $t['name'], $m)) {
            $v = $m[1] . '.' . $m[2];
            if (!isset($out[$m[1]]) || version_compare($v, $out[$m[1]]['version'], '>')) {
                $out[$m[1]] = ['version' => $v, 'date' => substr((string) $t['last_updated'], 0, 10)];
            }
        }
    }
    return json_encode($out);
}

/** Ubuntu CVE JSON -> only the php packages and the releases that matter. */
function reduce_ubuntu(string $json): ?string
{
    $d = json_decode($json, true);
    if (!isset($d['packages'])) {
        return null;
    }
    $pk = [];
    foreach ($d['packages'] as $p) {
        if (!str_starts_with($p['name'], 'php')) {
            continue;
        }
        foreach ($p['statuses'] as $s) {
            if (in_array($s['release_codename'], UBUNTU_RELEASES, true) && $s['status'] !== 'DNE') {
                $pk[] = ['package' => $p['name'], 'release' => $s['release_codename'], 'status' => $s['status'], 'version' => (string) $s['description']];
            }
        }
    }
    return json_encode(['description' => (string) ($d['description'] ?? ''), 'rows' => $pk]);
}

/** Red Hat CVE JSON -> rows for RHEL 8-10 only. */
function reduce_redhat(string $json): ?string
{
    $d = json_decode($json, true);
    if (!is_array($d)) {
        return null;
    }
    $rows = [];
    foreach ($d['affected_release'] ?? [] as $a) {
        if (preg_match(REDHAT_RELEASE_RE, (string) $a['product_name'])) {
            $rows[] = ['release' => $a['product_name'], 'package' => (string) ($a['package'] ?? ''), 'status' => 'fixed ' . ($a['advisory'] ?? ''), 'level' => 'ok'];
        }
    }
    foreach ($d['package_state'] ?? [] as $s) {
        if (preg_match(REDHAT_RELEASE_RE, (string) $s['product_name'])) {
            $state = (string) $s['fix_state'];
            $rows[] = ['release' => $s['product_name'], 'package' => (string) $s['package_name'], 'status' => strtolower($state), 'level' => match ($state) {
                'Affected' => 'bad',
                'Not affected', 'Out of support scope' => 'none',
                default => 'warn',
            }];
        }
    }
    return json_encode(['description' => clean((string) ($d['bugzilla']['description'] ?? '')), 'rows' => $rows]);
}

/** Alpine secdb -> {"php84": {"8.4.16-r0": ["CVE-…"]}} */
function reduce_alpine(string $json): ?string
{
    $d = json_decode($json, true);
    if (!isset($d['packages'])) {
        return null;
    }
    $out = [];
    foreach ($d['packages'] as $p) {
        if (str_starts_with($p['pkg']['name'], 'php')) {
            $out[$p['pkg']['name']] = $p['pkg']['secfixes'] ?? [];
        }
    }
    return json_encode($out);
}

// ---------- Parsers: one per source ----------

/** A tracker status word -> ok / bad / none / warn. */
function level_of(string $status): string
{
    $s = strtolower($status);
    if (str_contains($s, 'vulnerable') || in_array($s, ['needed', 'pending'], true)) {
        return 'bad';
    }
    if (str_contains($s, 'fixed') || $s === 'released') {
        return 'ok';
    }
    if (preg_match('/not.affected|unimportant|end-of-life|ignored|no-dsa|deferred/', $s)) {
        return 'none';
    }
    return 'warn';
}

/** Rows of the table that follows the first <h2> whose text contains $heading. */
function table_rows(DOMXPath $x, string $heading): array
{
    $rows = [];
    foreach ($x->query("//h2[contains(., '$heading')]/following::table[1]//tr[td]") ?: [] as $tr) {
        $cells = [];
        foreach ($x->query('./td', $tr) as $td) {
            $cells[] = ['text' => clean($td->textContent), 'link' => ($a = $x->query('.//a', $td)->item(0)) ? clean($a->textContent) : ''];
        }
        $rows[] = $cells;
    }
    return $rows;
}

/** Debian tracker CVE page -> ['description' => …, 'rows' => [...]]; null on 404. */
function parse_debian_cve(array $res): ?array
{
    if ($res['code'] !== 200) {
        return null;
    }
    $x = dom_of($res['body']);
    $out = ['description' => '', 'rows' => []];
    foreach ($x->query('//table[1]//tr') as $tr) {
        $c = $x->query('./td', $tr);
        if ($c->length === 2 && clean($c->item(0)->textContent) === 'Description') {
            $out['description'] = clean($c->item(1)->textContent);
        }
    }
    $pkg = '';
    $per = [];
    foreach (table_rows($x, 'Vulnerable and fixed') as $c) {
        if (count($c) < 4) {
            continue;
        }
        $pkg = $c[0]['link'] !== '' ? $c[0]['link'] : $pkg;
        if (!str_starts_with($pkg, 'php')) {
            continue;
        }
        $status = $c[count($c) - 1]['text'];
        // One row per package and release: the "(security)" upload supersedes the plain row.
        $r = ['package' => $pkg, 'release' => trim(preg_replace('/\s*\(security\)/', '', $c[1]['text'])), 'version' => $c[2]['text'], 'status' => $status, 'level' => level_of($status)];
        $key = $r['package'] . '|' . $r['release'];
        if (!isset($per[$key]) || ($r['level'] === 'ok' && $per[$key]['level'] !== 'ok')) {
            $per[$key] = $r;
        }
    }
    $out['rows'] = array_values($per);
    return $out;
}

function parse_ubuntu_cve(array $res): ?array
{
    $d = $res['code'] === 200 ? json_decode($res['body'], true) : null;
    if (!$d || !$d['rows']) {
        return null;
    }
    $rows = array_map(fn($r) => ['package' => $r['package'], 'release' => $r['release'], 'version' => $r['version'], 'status' => $r['status'], 'level' => level_of($r['status'])], $d['rows']);
    return ['description' => clean($d['description']), 'rows' => $rows];
}

function parse_redhat_cve(array $res): ?array
{
    $d = $res['code'] === 200 ? json_decode($res['body'], true) : null;
    if (!$d || !$d['rows']) {
        return null;
    }
    // Collapse to one row per release: a fix beats the "will not fix" state of another package.
    $per = [];
    foreach ($d['rows'] as $r) {
        $rank = ['bad' => 3, 'warn' => 2, 'none' => 1, 'ok' => 4][$r['level']];
        if (!isset($per[$r['release']]) || $rank > $per[$r['release']]['rank']) {
            $per[$r['release']] = $r + ['rank' => $rank, 'version' => ''];
        }
    }
    return ['description' => $d['description'], 'rows' => array_map(fn($r) => array_diff_key($r, ['rank' => 0]), array_values($per))];
}

function parse_alpine_cve(string $id, array $byBranch): ?array
{
    $rows = [];
    foreach ($byBranch as $branch => $res) {
        $d = $res['code'] === 200 ? json_decode($res['body'], true) : null;
        foreach ($d ?? [] as $pkg => $fixes) {
            foreach ($fixes as $ver => $cves) {
                if (in_array($id, $cves, true)) {
                    $rows[] = ['package' => $pkg, 'release' => $branch, 'version' => $ver, 'status' => 'fixed', 'level' => 'ok'];
                }
            }
        }
    }
    return $rows ? ['description' => '', 'rows' => $rows] : null;
}

/** Debian source-package pages + sources.debian.org -> [suite label => version]. */
function parse_debian_package(array $tracker, array $sources, string $release): array
{
    $have = [];
    if ($tracker['code'] === 200) {
        foreach (table_rows(dom_of($tracker['body']), 'Available versions') as $c) {
            if (count($c) >= 2) {
                $have[$c[0]['text']] = $c[1]['text'];
            }
        }
    }
    $json = $sources['code'] === 200 ? json_decode($sources['body'], true) : null;
    foreach ($json['versions'] ?? [] as $v) {
        foreach ($v['suites'] as $suite) {
            if ($suite === $release . '-proposed-updates' || $suite === 'experimental') {
                $have[$suite] = $v['version'];
            }
        }
    }
    return $have;
}

function open_issues(array $tracker): array
{
    $out = [];
    if ($tracker['code'] === 200) {
        foreach (table_rows(dom_of($tracker['body']), 'Open issues') as $c) {
            if ($c && str_starts_with($c[0]['link'], 'CVE-')) {
                $out[] = ['id' => $c[0]['link'], 'description' => $c[1]['text'] ?? ''];
            }
        }
    }
    return $out;
}

// ---------- The version matrix ----------

/** One matrix cell: the version, and whether it is as new as php.net's latest for that branch. */
function cell(?string $version, string $branch, array $upstream): ?array
{
    if ($version === null) {
        return null;
    }
    $u = upstream_of($version);
    $latest = $upstream[$branch]['version'] ?? null;
    $state = ($u === null || $latest === null) ? 'none' : (version_compare($u, $latest, '<') ? 'behind' : 'current');
    return ['version' => $version, 'short' => $u ?? $version, 'state' => $state];
}

function matrix_row(string $repo, string $label, array $versions, array $upstream): array
{
    $cells = [];
    foreach (BRANCHES as $b) {
        $cells[$b] = cell($versions[$b] ?? null, $b, $upstream);
    }
    return ['repo' => $repo, 'label' => $label, 'cells' => $cells];
}

// ---------- The whole picture ----------

function build(array $cveIds): array
{
    $dpk = array_keys(DEBIAN_PACKAGES);
    $urls = [PHPNET_ACTIVE, PHPNET_CHANGELOG, DOCKER_TAGS];
    $reduce = [PHPNET_CHANGELOG => 'reduce_changelog', DOCKER_TAGS => 'reduce_docker'];
    foreach ($dpk as $p) {
        $urls[] = DEBIAN_TRACKER . 'source-package/' . $p;
        $urls[] = DEBIAN_SOURCES . $p . '/';
    }
    foreach (SURY_SUITES as $s) {
        $urls[] = $u = sprintf(SURY_PACKAGES, $s);
        $reduce[$u] = 'reduce_sury';
    }
    foreach (SURY_PPA_SUITES as $s) {
        $urls[] = $u = sprintf(SURY_PPA_PACKAGES, $s);
        $reduce[$u] = 'reduce_sury';
    }
    foreach (ALPINE_BRANCHES as $a) {
        $urls[] = $u = sprintf(ALPINE_SECDB, $a);
        $reduce[$u] = 'reduce_alpine';
    }
    foreach ($cveIds as $id) {
        $urls[] = DEBIAN_TRACKER . $id;
        $urls[] = $u = sprintf(UBUNTU_CVE, $id);
        $reduce[$u] = 'reduce_ubuntu';
        $urls[] = $u = sprintf(REDHAT_CVE, $id);
        $reduce[$u] = 'reduce_redhat';
    }
    $res = fetch_all($urls, $reduce);

    $age = 0;
    $stale = false;
    $down = [];
    foreach ($res as $url => $r) {
        $age = max($age, $r['age']);
        $stale = $stale || $r['stale'];
        if ($r['code'] === 0) {
            $down[parse_url($url, PHP_URL_HOST)] = true;
        }
    }
    $json = fn(string $url) => $res[$url]['code'] === 200 ? (json_decode($res[$url]['body'], true) ?: []) : [];

    // php.net: the latest release per active branch, and which releases name which CVE.
    $upstream = [];
    foreach ($json(PHPNET_ACTIVE)['8'] ?? [] as $branch => $rel) {
        if (preg_match('/PHP (\d+\.\d+\.\d+)/', $rel['source'][0]['name'] ?? '', $m)) {
            $upstream[$branch] = ['version' => $m[1], 'date' => $rel['date'] ?? '', 'security' => in_array('security', $rel['tags'] ?? [], true)];
        }
    }
    $changelog = $json(PHPNET_CHANGELOG);

    // The version matrix.
    $matrix = [];
    $matrix[] = matrix_row('php.net', 'upstream', array_map(fn($u) => $u['version'], $upstream), $upstream);
    $docker = $json(DOCKER_TAGS);
    $matrix[] = matrix_row('Docker', 'official image, cli-alpine', array_map(fn($d) => $d['version'], $docker), $upstream);

    $dsuites = [];   // [release label][branch] => version
    $packages = [];
    foreach (DEBIAN_PACKAGES as $p => [$release, $branch]) {
        $tr = $res[DEBIAN_TRACKER . 'source-package/' . $p];
        $have = parse_debian_package($tr, $res[DEBIAN_SOURCES . $p . '/'], $release);
        if (in_array($release, DEBIAN_STABLE, true)) {
            $dsuites[$release][$branch] = $have[$release . ' (security)'] ?? $have[$release] ?? null;
        }
        if (isset($have['sid'])) {
            $dsuites['sid'][$branch] = $have['sid'];
        }
        if (isset($have['experimental'])) {
            $dsuites['experimental'][$branch] = $have['experimental'];
        }
        $queued = $have[$release . '-proposed-updates'] ?? null;
        $current = $have[$release . ' (security)'] ?? $have[$release] ?? $have['experimental'] ?? null;
        $packages[] = [
            'name' => $p, 'release' => $release, 'tracked' => $tr['code'] === 200, 'current' => $current,
            'pending' => ($queued !== null && $current !== null && version_compare($queued, $current, '>')) ? $queued : null,
            'open' => open_issues($tr),
            'versions' => array_map(fn($s, $v) => ['suite' => $s, 'version' => $v], array_keys($have), $have),
        ];
    }
    foreach (['bookworm', 'trixie', 'sid', 'experimental'] as $label) {
        if (!empty($dsuites[$label])) {
            $matrix[] = matrix_row('Debian', $label, array_filter($dsuites[$label]), $upstream);
        }
    }
    foreach (SURY_SUITES as $s) {
        $v = $json(sprintf(SURY_PACKAGES, $s));
        $matrix[] = matrix_row('Sury (Debian)', $s, array_combine(BRANCHES, array_map(fn($b) => $v["php$b"] ?? null, BRANCHES)), $upstream);
    }
    foreach (SURY_PPA_SUITES as $s) {
        $v = $json(sprintf(SURY_PPA_PACKAGES, $s));
        if ($v) {
            $matrix[] = matrix_row('Sury PPA (Ubuntu)', $s, array_combine(BRANCHES, array_map(fn($b) => $v["php$b"] ?? null, BRANCHES)), $upstream);
        }
    }

    // The CVEs.
    $cves = [];
    foreach ($cveIds as $id) {
        $sources = [
            ['name' => 'Debian', 'url' => DEBIAN_TRACKER . $id, 'got' => $res[DEBIAN_TRACKER . $id], 'data' => parse_debian_cve($res[DEBIAN_TRACKER . $id])],
            ['name' => 'Ubuntu', 'url' => 'https://ubuntu.com/security/' . $id, 'got' => $res[sprintf(UBUNTU_CVE, $id)], 'data' => parse_ubuntu_cve($res[sprintf(UBUNTU_CVE, $id)])],
            ['name' => 'Red Hat', 'url' => 'https://access.redhat.com/security/cve/' . $id, 'got' => $res[sprintf(REDHAT_CVE, $id)], 'data' => parse_redhat_cve($res[sprintf(REDHAT_CVE, $id)])],
        ];
        $alp = [];
        foreach (ALPINE_BRANCHES as $a) {
            $alp[$a] = $res[sprintf(ALPINE_SECDB, $a)];
        }
        $sources[] = ['name' => 'Alpine', 'url' => 'https://security.alpinelinux.org/vuln/' . $id, 'got' => ['code' => min(array_column($alp, 'code')) ?: 0], 'data' => parse_alpine_cve($id, $alp)];

        $desc = '';
        $levels = [];
        $all = [];
        $out = [];
        foreach ($sources as $s) {
            $rows = $s['data']['rows'] ?? [];
            $desc = $desc !== '' ? $desc : ($s['data']['description'] ?? '');
            foreach ($rows as $r) {
                $all[] = $r;
                // Debian also lists forky/sid/testing rows; only the stable releases decide "vulnerable".
                if ($s['name'] !== 'Debian' || in_array($r['release'], DEBIAN_STABLE, true)) {
                    $levels[] = $r['level'];
                }
            }
            $out[] = ['name' => $s['name'], 'url' => $s['url'], 'status' => $s['data'] ? 'found' : (in_array($s['got']['code'], [200, 404], true) ? 'none' : 'error'), 'rows' => $rows];
        }
        $state = in_array('bad', $levels, true) ? 'vulnerable' : (in_array('ok', $levels, true) ? 'fixed' : ($all ? 'tracked' : 'unknown'));
        if ($state === 'unknown' && !array_filter($out, fn($o) => $o['status'] === 'none' || $o['status'] === 'found')) {
            $state = 'error';
        }
        $mentions = [];
        foreach ($changelog as $ver => $c) {
            if (in_array($id, $c['cves'], true)) {
                $mentions[] = ['version' => $ver, 'date' => $c['date']];
            }
        }
        $cves[] = ['id' => $id, 'state' => $state, 'description' => $desc, 'upstream' => $mentions, 'sources' => $out];
    }

    return ['generated' => time(), 'age' => $age, 'stale' => $stale, 'ttl' => TTL, 'unreachable' => array_keys($down),
        'upstream' => $upstream, 'matrix' => $matrix, 'cves' => $cves, 'packages' => $packages];
}
