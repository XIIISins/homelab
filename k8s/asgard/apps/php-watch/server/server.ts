// PHP watch: a zero-dependency Node server. Serves the static page and one JSON endpoint.
// Run directly by Node (type stripping), no build step:  node server.ts
//
// It is reachable from the internet, so:
//   - only GET/HEAD, only an allowlist of static files (nothing is read from disk per request)
//   - the default watch list is served from a background-refreshed cache, so ordinary
//     visitors never cause an outbound request
//   - other CVE ids are validated, capped per request, rate-limited per client and drawn
//     from a small shared budget of new lookups
//   - strict CSP, no cookies, no inline script or style

import { readFileSync } from 'node:fs';
import http from 'node:http';
import { isIP } from 'node:net';
import { join } from 'node:path';
import { Fetcher } from './fetcher.ts';
import { Budget, WindowLimiter } from './ratelimit.ts';
import { build, CVE_RE, DEFAULT_CVES, MAX_CVES } from './sources.ts';

const PORT = Number(process.env.PORT ?? 8080);
const SITE_DIR = process.env.SITE_DIR ?? '/srv/site';
const WARM_MS = 10 * 60_000;
const READY_AFTER_MS = 45_000; // ready anyway after this, so a slow source cannot keep the pod out of service

const STATIC: Record<string, { file: string; type: string; cache: string }> = {
  '/': { file: 'index.html', type: 'text/html; charset=utf-8', cache: 'public, max-age=300' },
  '/index.html': { file: 'index.html', type: 'text/html; charset=utf-8', cache: 'public, max-age=300' },
  '/style.css': { file: 'style.css', type: 'text/css; charset=utf-8', cache: 'public, max-age=300' },
  '/app.js': { file: 'app.js', type: 'text/javascript; charset=utf-8', cache: 'public, max-age=300' },
  '/theme.js': { file: 'theme.js', type: 'text/javascript; charset=utf-8', cache: 'public, max-age=300' },
  '/familjen-grotesk.woff2': { file: 'familjen-grotesk.woff2', type: 'font/woff2', cache: 'public, max-age=86400, immutable' },
};

// Read once at start: a missing file stops the pod instead of failing per request.
const files = new Map<string, Buffer>();
for (const { file } of Object.values(STATIC)) if (!files.has(file)) files.set(file, readFileSync(join(SITE_DIR, file)));

const SECURITY_HEADERS = {
  'content-security-policy': "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'no-referrer',
  'permissions-policy': 'camera=(), microphone=(), geolocation=(), interest-cohort=()',
  'cross-origin-opener-policy': 'same-origin',
  'cross-origin-resource-policy': 'same-origin',
};

const fetcher = new Fetcher();
const budget = new Budget(10, 60_000); // new, uncached CVE ids per minute, for everyone together
const perClient = new WindowLimiter(60, 60_000); // /api/status requests per client per minute

/** The caller's address: Cloudflare's header when the request came through the tunnel. */
function clientOf(req: http.IncomingMessage): string {
  const cf = req.headers['cf-connecting-ip'];
  if (typeof cf === 'string' && isIP(cf)) return cf;
  const xff = req.headers['x-forwarded-for'];
  const first = typeof xff === 'string' ? xff.split(',')[0].trim() : '';
  return isIP(first) ? first : (req.socket.remoteAddress ?? 'unknown');
}

/** ?cve=A&cve=B -> validated, upper-cased, de-duplicated ids (empty = the default list). */
export function idsFrom(url: URL): string[] {
  const ids = new Set<string>();
  for (const raw of url.searchParams.getAll('cve')) {
    const id = raw.trim().toUpperCase();
    if (CVE_RE.test(id)) ids.add(id);
  }
  return ids.size ? [...ids].slice(0, MAX_CVES) : DEFAULT_CVES;
}

function send(res: http.ServerResponse, status: number, type: string, body: Buffer | string, cache = 'no-store', head = false, extra: Record<string, string> = {}): void {
  const buf = typeof body === 'string' ? Buffer.from(body) : body;
  res.writeHead(status, { ...SECURITY_HEADERS, 'content-type': type, 'content-length': buf.length, 'cache-control': cache, ...extra });
  res.end(head ? undefined : buf);
}

const server = http.createServer(async (req, res) => {
  const head = req.method === 'HEAD';
  if (req.method !== 'GET' && !head) return send(res, 405, 'text/plain', 'GET only\n', 'no-store', false, { allow: 'GET, HEAD' });
  if ((req.url ?? '').length > 2048) return send(res, 414, 'text/plain', 'URL too long\n');
  const url = new URL(req.url ?? '/', 'http://localhost');

  const asset = STATIC[url.pathname];
  if (asset) return send(res, 200, asset.type, files.get(asset.file) as Buffer, asset.cache, head);
  if (url.pathname === '/healthz') return send(res, 200, 'text/plain', 'ok\n', 'no-store', head);
  // Ready once the default list is in memory, so a new pod does not serve visitors cold.
  if (url.pathname === '/readyz') return warmed ? send(res, 200, 'text/plain', 'ready\n', 'no-store', head) : send(res, 503, 'text/plain', 'warming up\n', 'no-store', head);

  if (url.pathname === '/api/status') {
    if (!perClient.allow(clientOf(req))) return send(res, 429, 'application/json', '{"error":"slow down"}', 'no-store', head, { 'retry-after': '30' });
    try {
      const status = await build(idsFrom(url), { fetcher, budget });
      return send(res, 200, 'application/json; charset=utf-8', JSON.stringify(status), 'public, max-age=60', head);
    } catch (err) {
      console.error('php-watch: build failed:', err);
      return send(res, 500, 'application/json', '{"error":"internal"}', 'no-store', head);
    }
  }
  return send(res, 404, 'text/plain', 'not found\n', 'no-store', head);
});

server.requestTimeout = 30_000;
server.headersTimeout = 10_000;
server.keepAliveTimeout = 5_000;
server.maxHeadersCount = 50;
server.maxConnections = 256;

// Keep the default list's data fresh in the background so visitors are served from memory.
let warmed = false;
async function warm(): Promise<void> {
  try {
    await build(DEFAULT_CVES, { fetcher, budget });
  } catch (err) {
    console.error('php-watch: warm-up failed:', err);
  } finally {
    warmed = true;
  }
}
setTimeout(() => (warmed = true), READY_AFTER_MS).unref();
setInterval(() => perClient.prune(), 60_000).unref();
setInterval(() => void warm(), WARM_MS).unref();

server.listen(PORT, '0.0.0.0', () => {
  console.log(`php-watch listening on :${PORT}`);
  void warm();
});

for (const sig of ['SIGTERM', 'SIGINT'] as const) {
  process.on(sig, () => {
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 5_000).unref();
  });
}
