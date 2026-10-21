<?php
// Shared plumbing for the PHP watch backend: a parallel, cached, retrying HTTP fetch.
// The per-source parsing lives in sources.php. Hosts are fixed there and CVE ids are
// validated, so a request cannot make the pod fetch anything else.
declare(strict_types=1);

const TTL = 900;                 // seconds a page is reused before the source is asked again
// Hosts that are slow but change rarely: a longer timeout and a longer reuse time.
const SLOW_HOSTS = ['ppa.launchpadcontent.net'];
const SLOW_TTL = 3600;

function is_slow(string $url): bool
{
    return in_array(parse_url($url, PHP_URL_HOST), SLOW_HOSTS, true);
}

/** GET several URLs in parallel. Returns url => [code, body]; code 0 means the request failed. */
function http_multi(array $urls): array
{
    $mh = curl_multi_init();
    $chs = [];
    foreach ($urls as $url) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_FOLLOWLOCATION => true,
            CURLOPT_MAXREDIRS => 3,
            CURLOPT_REDIR_PROTOCOLS => CURLPROTO_HTTPS,
            CURLOPT_TIMEOUT => is_slow($url) ? 25 : 12,
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

/**
 * Fetch many URLs through a small file cache; a failed fetch is retried once, then falls back to a stale copy.
 * $reduce maps a URL to fn(string $body): ?string that boils a big or binary body down BEFORE it is cached
 * (null = unusable, treated like a failed fetch). The cache therefore only ever holds the small form.
 */
function fetch_all(array $urls, array $reduce = []): array
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
        if ($hit && $age < (is_slow($url) ? SLOW_TTL : TTL)) {
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
        if ($code === 200 && isset($reduce[$url])) {
            $body = $reduce[$url]($body);
            if ($body === null) {
                $code = 0;
            }
        }
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
