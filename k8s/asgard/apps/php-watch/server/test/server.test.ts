import assert from 'node:assert/strict';
import { spawn, type ChildProcess } from 'node:child_process';
import { after, before, test } from 'node:test';

let child: ChildProcess;
const port = 18080 + Math.floor(Math.random() * 1000);
const base = `http://127.0.0.1:${port}`;

before(async () => {
  child = spawn(process.execPath, ['server.ts'], { cwd: new URL('..', import.meta.url), env: { ...process.env, PORT: String(port), SITE_DIR: new URL('../../site', import.meta.url).pathname }, stdio: 'ignore' });
  for (let i = 0; i < 50; i++) {
    try {
      if ((await fetch(`${base}/healthz`)).ok) return;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error('server did not start');
});
after(() => child.kill());

test('serves the page with a strict CSP and no cookies', async () => {
  const r = await fetch(`${base}/`);
  assert.equal(r.status, 200);
  assert.match(r.headers.get('content-type') ?? '', /text\/html/);
  assert.match(r.headers.get('content-security-policy') ?? '', /default-src 'none'.*script-src 'self'/);
  assert.equal(r.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(r.headers.get('set-cookie'), null);
  assert.match(await r.text(), /PHP watch/);
});

test('serves only the allowlisted static files', async () => {
  for (const p of ['/style.css', '/app.js', '/theme.js', '/familjen-grotesk.woff2']) assert.equal((await fetch(base + p)).status, 200, p);
  for (const p of ['/server.ts', '/../server.ts', '/%2e%2e/server.ts', '/etc/passwd', '/api/other', '/api.php']) assert.equal((await fetch(base + p)).status, 404, p);
});

test('only GET and HEAD', async () => {
  assert.equal((await fetch(`${base}/`, { method: 'POST' })).status, 405);
  assert.equal((await fetch(`${base}/`, { method: 'PUT' })).status, 405);
  const head = await fetch(`${base}/`, { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), '');
});

test('overlong URLs are refused', async () => {
  assert.equal((await fetch(`${base}/api/status?cve=${'A'.repeat(3000)}`)).status, 414);
});
