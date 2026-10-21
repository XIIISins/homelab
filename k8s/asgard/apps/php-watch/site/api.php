<?php
// GET /api.php?cve=CVE-2026-1&cve=CVE-2026-2   (no cve= -> the default watch list)
declare(strict_types=1);
require __DIR__ . '/sources.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');

if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
    http_response_code(405);
    exit('{"error":"GET only"}');
}

// PHP's own parser turns repeated cve= into the last one; read the raw query instead.
$ids = [];
foreach (explode('&', (string) ($_SERVER['QUERY_STRING'] ?? '')) as $pair) {
    [$k, $v] = array_pad(explode('=', $pair, 2), 2, '');
    $v = strtoupper(urldecode($v));
    if ($k === 'cve' && preg_match('/^CVE-\d{4}-\d{4,7}$/', $v)) {
        $ids[$v] = true;
    }
}
$ids = array_slice(array_keys($ids) ?: DEFAULT_CVES, 0, MAX_CVES);

echo json_encode(build($ids), JSON_UNESCAPED_SLASHES);
