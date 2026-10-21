<?php
// Debian watch: fetch and digest two public Debian sources, server-side because
// neither sends CORS headers.
//   security-tracker.debian.org  per-CVE and per-source-package pages (HTML)
//   sources.debian.org/api       what versions sit in which suite, incl. *-proposed-updates
// Hosts are fixed and CVE ids / package names are validated, so a request cannot
// make the pod fetch anything else.
declare(strict_types=1);

const TRACKER = 'https://security-tracker.debian.org/tracker/';
const SOURCES = 'https://sources.debian.org/api/src/';
const TTL = 900;                 // seconds a page is reused before Debian is asked again
const MAX_CVES = 8;
const DEFAULT_CVES = ['CVE-2026-109618', 'CVE-2026-109619'];
// source package => the Debian release that ships it
const PACKAGES = ['php8.2' => 'bookworm', 'php8.4' => 'trixie', 'php8.5' => 'experimental'];
const STABLE = ['bookworm', 'trixie'];

/** GET several URLs in parallel. Returns url => [code, body]; code 0 means the request failed. */
function http_multi(array $urls): array
{
    $mh = curl_multi_init();
    $chs = [];
    foreach ($urls as $url) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_FOLLOWLOCATION => false,
            CURLOPT_TIMEOUT => 12,
            CURLOPT_CONNECTTIMEOUT => 5,
            CURLOPT_MAXFILESIZE => 2_000_000,
            CURLOPT_USERAGENT => 'homelab-php-watch (read-only status page)',
            CURLOPT_PROTOCOLS => CURLPROTO_HTTPS,
        ]);
        curl_multi_add_handle($mh, $ch);
        $chs[$url] = $ch;
    }
    do {
        $status = curl_multi_exec($mh, $running);
        if ($running) {
            curl_multi_select($mh, 1.0);
        }
    } while ($running && $status === CURLM_OK);
    $out = [];
    foreach ($chs as $url => $ch) {
        $out[$url] = [(int) curl_getinfo($ch, CURLINFO_HTTP_CODE), (string) curl_multi_getcontent($ch)];
        if ($out[$url][0] !== 200 && $out[$url][0] !== 404) {
            error_log("php-watch: $url -> HTTP {$out[$url][0]} " . curl_error($ch));
        }
        curl_multi_remove_handle($mh, $ch);
    }
    curl_multi_close($mh);
    return $out;
}

/** Fetch many URLs through a small file cache; a failed fetch is retried once, then falls back to a stale copy. */
function fetch_all(array $urls): array
{
    $dir = sys_get_temp_dir() . '/php-watch';
    if (!is_dir($dir)) {
        @mkdir($dir, 0700, true);
    }
    $out = [];
    $todo = [];
    foreach (array_unique($urls) as $url) {
        $file = $dir . '/' . sha1($url) . '.json';
        $hit = is_file($file) ? json_decode((string) file_get_contents($file), true) : null;
        $age = $hit ? time() - filemtime($file) : null;
        if ($hit && $age < TTL) {
            $out[$url] = ['code' => $hit['code'], 'body' => $hit['body'], 'age' => $age, 'stale' => false];
        } else {
            $todo[$url] = [$file, $hit, $age];
        }
    }
    $got = $todo ? http_multi(array_keys($todo)) : [];
    $retry = array_keys(array_filter($got, fn($r) => $r[0] !== 200 && $r[0] !== 404));
    if ($retry) {
        usleep(400_000);
        $got = array_merge($got, http_multi($retry));
    }
    foreach ($todo as $url => [$file, $hit, $age]) {
        [$code, $body] = $got[$url];
        if ($code === 200 || $code === 404) {
            file_put_contents($file, json_encode(['code' => $code, 'body' => $body]));
            $out[$url] = ['code' => $code, 'body' => $body, 'age' => 0, 'stale' => false];
        } elseif ($hit) {
            $out[$url] = ['code' => $hit['code'], 'body' => $hit['body'], 'age' => $age, 'stale' => true];
        } else {
            $out[$url] = ['code' => 0, 'body' => '', 'age' => 0, 'stale' => true];
        }
    }
    return $out;
}

function dom_of(string $html): DOMXPath
{
    $doc = new DOMDocument();
    libxml_use_internal_errors(true);
    $doc->loadHTML('<?xml encoding="utf-8"?>' . $html);
    libxml_clear_errors();
    return new DOMXPath($doc);
}

function clean(string $s): string
{
    return trim((string) preg_replace('/\s+/u', ' ', $s));
}

/** A tracker status word -> ok / bad / none / warn. */
function level_of(string $status): string
{
    $s = strtolower($status);
    if (str_contains($s, 'vulnerable')) {
        return 'bad';
    }
    if (str_contains($s, 'fixed')) {
        return 'ok';
    }
    if (preg_match('/not.affected|unimportant|end-of-life|ignored|no-dsa/', $s)) {
        return 'none';
    }
    return 'warn';
}

/** Rows of the table that follows the first <h2> whose text contains $heading. */
function table_rows(DOMXPath $x, string $heading): array
{
    $rows = [];
    $trs = $x->query("//h2[contains(., '$heading')]/following::table[1]//tr[td]");
    foreach ($trs ?: [] as $tr) {
        $cells = [];
        foreach ($x->query('./td', $tr) as $td) {
            $cells[] = ['text' => clean($td->textContent), 'link' => ($a = $x->query('.//a', $td)->item(0)) ? clean($a->textContent) : ''];
        }
        $rows[] = $cells;
    }
    return $rows;
}

function parse_cve(string $id, array $res): array
{
    $cve = ['id' => $id, 'url' => TRACKER . $id, 'state' => 'unknown', 'description' => '', 'rows' => [], 'others' => 0];
    if ($res['code'] === 404) {
        return $cve;
    }
    if ($res['code'] !== 200) {
        $cve['state'] = 'error';
        return $cve;
    }
    $x = dom_of($res['body']);
    foreach ($x->query('//table[1]//tr') as $tr) {
        $cells = $x->query('./td', $tr);
        if ($cells->length === 2 && clean($cells->item(0)->textContent) === 'Description') {
            $cve['description'] = clean($cells->item(1)->textContent);
        }
    }
    $pkg = '';
    foreach (table_rows($x, 'Vulnerable and fixed') as $c) {
        if (count($c) < 4) {
            continue;
        }
        $pkg = $c[0]['link'] !== '' ? $c[0]['link'] : $pkg;
        if (!str_starts_with($pkg, 'php')) {
            $cve['others']++;
            continue;
        }
        $status = $c[count($c) - 1]['text'];
        $cve['rows'][] = ['package' => $pkg, 'release' => $c[1]['text'], 'version' => $c[2]['text'], 'status' => $status, 'level' => level_of($status)];
    }
    // One row per package and release: the "(security)" upload supersedes the plain row,
    // so a fixed row wins over the vulnerable one it replaces.
    $per = [];
    foreach ($cve['rows'] as $r) {
        $r['release'] = trim(preg_replace('/\s*\(security\)/', '', $r['release']));
        $key = $r['package'] . '|' . $r['release'];
        if (!isset($per[$key]) || ($r['level'] === 'ok' && $per[$key]['level'] !== 'ok')) {
            $per[$key] = $r;
        }
    }
    $cve['rows'] = array_values($per);
    $levels = [];
    foreach ($cve['rows'] as $r) {
        if (in_array($r['release'], STABLE, true)) {
            $levels[] = $r['level'];
        }
    }
    $cve['state'] = in_array('bad', $levels, true) ? 'vulnerable'
        : (in_array('ok', $levels, true) ? 'fixed' : 'tracked');
    return $cve;
}

function parse_package(string $name, string $release, array $tracker, array $sources): array
{
    $pkg = ['name' => $name, 'release' => $release, 'tracked' => $tracker['code'] === 200, 'versions' => [], 'open' => [], 'pending' => null];
    if ($tracker['code'] === 200) {
        $x = dom_of($tracker['body']);
        foreach (table_rows($x, 'Available versions') as $c) {
            if (count($c) >= 2) {
                $pkg['versions'][] = ['suite' => $c[0]['text'], 'version' => $c[1]['text']];
            }
        }
        foreach (table_rows($x, 'Open issues') as $c) {
            if ($c && str_starts_with($c[0]['link'], 'CVE-')) {
                $pkg['open'][] = ['id' => $c[0]['link'], 'description' => $c[1]['text'] ?? ''];
            }
        }
    }
    $json = $sources['code'] === 200 ? json_decode($sources['body'], true) : null;
    foreach ($json['versions'] ?? [] as $v) {
        foreach ($v['suites'] as $suite) {
            if ($suite === $release . '-proposed-updates' || $suite === 'experimental') {
                $pkg['versions'][] = ['suite' => $suite, 'version' => $v['version']];
            }
        }
    }
    $have = array_column($pkg['versions'], 'version', 'suite');
    $stable = $have[$release . ' (security)'] ?? $have[$release] ?? null;
    $queued = $have[$release . '-proposed-updates'] ?? null;
    $pkg['current'] = $stable ?? ($have['experimental'] ?? null);
    $pkg['pending'] = ($queued !== null && $stable !== null && version_compare($queued, $stable, '>')) ? $queued : null;
    return $pkg;
}

/** The whole picture for the page. */
function build(array $cveIds): array
{
    $urls = [];
    foreach ($cveIds as $id) {
        $urls[] = TRACKER . $id;
    }
    foreach (array_keys(PACKAGES) as $p) {
        $urls[] = TRACKER . 'source-package/' . $p;
        $urls[] = SOURCES . $p . '/';
    }
    $res = fetch_all($urls);
    $age = 0;
    $stale = false;
    foreach ($res as $r) {
        $age = max($age, $r['age']);
        $stale = $stale || $r['stale'];
    }
    $packages = [];
    foreach (PACKAGES as $p => $release) {
        $packages[] = parse_package($p, $release, $res[TRACKER . 'source-package/' . $p], $res[SOURCES . $p . '/']);
    }
    $cves = [];
    foreach ($cveIds as $id) {
        $cves[] = parse_cve($id, $res[TRACKER . $id]);
    }
    return ['generated' => time(), 'age' => $age, 'stale' => $stale, 'ttl' => TTL, 'cves' => $cves, 'packages' => $packages];
}
