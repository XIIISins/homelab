import assert from 'node:assert/strict';
import { test } from 'node:test';
import { Fetcher, type RawGet } from '../fetcher.ts';

/** Let a background refresh (which retries on a timer) run to the end. */
const settle = () => new Promise((r) => setTimeout(r, 20));

function setup(script: (url: string, call: number) => { code: number; body: string } | Error) {
  let clock = 1_000_000;
  const calls: Record<string, number> = {};
  const get: RawGet = async (url) => {
    calls[url] = (calls[url] ?? 0) + 1;
    const r = script(url, calls[url]);
    if (r instanceof Error) throw r;
    return { code: r.code, body: Buffer.from(r.body) };
  };
  const f = new Fetcher({ get, now: () => clock, ttlMs: 1000, failTtlMs: 500, retryDelayMs: 0, maxStaleMs: 60_000 });
  return { f, calls, tick: (ms: number) => (clock += ms) };
}

test('a fresh entry is served from memory', async () => {
  const { f, calls } = setup(() => ({ code: 200, body: 'a' }));
  assert.equal((await f.get({ url: 'u' })).body, 'a');
  assert.equal((await f.get({ url: 'u' })).body, 'a');
  assert.equal(calls.u, 1);
});

test('concurrent callers share one request', async () => {
  const { f, calls } = setup(() => ({ code: 200, body: 'a' }));
  await Promise.all([f.get({ url: 'u' }), f.get({ url: 'u' }), f.get({ url: 'u' })]);
  assert.equal(calls.u, 1);
});

test('404 is cached as an answer, not an error', async () => {
  const { f, calls } = setup(() => ({ code: 404, body: '' }));
  const r = await f.get({ url: 'u' });
  assert.deepEqual([r.code, r.stale], [404, false]);
  await f.get({ url: 'u' });
  assert.equal(calls.u, 1);
});

test('stale entry is served at once and refreshed behind it', async () => {
  let v = 'old';
  const { f, calls, tick } = setup(() => ({ code: 200, body: v }));
  await f.get({ url: 'u' });
  v = 'new';
  tick(2000);
  const during = await f.get({ url: 'u' });
  assert.equal(during.body, 'old', 'served without waiting');
  await settle();
  assert.equal((await f.get({ url: 'u' })).body, 'new');
  assert.equal(calls.u, 2);
});

test('a failed refresh keeps the old copy and flags it stale; failures back off', async () => {
  let fail = false;
  const { f, calls, tick } = setup(() => (fail ? { code: 503, body: '' } : { code: 200, body: 'good' }));
  await f.get({ url: 'u' });
  fail = true;
  tick(2000);
  await f.get({ url: 'u' }); // triggers the failing refresh
  await settle();
  const r = await f.get({ url: 'u' });
  assert.deepEqual([r.body, r.stale], ['good', true]);
  const before = calls.u;
  await f.get({ url: 'u' });
  assert.equal(calls.u, before, 'no new attempt inside the backoff window');
});

test('no copy and a failing source: code 0, remembered briefly', async () => {
  const { f, calls, tick } = setup(() => new Error('boom'));
  const r = await f.get({ url: 'u' });
  assert.deepEqual([r.code, r.stale], [0, true]);
  const n = calls.u;
  assert.equal(n, 2, 'one retry');
  await f.get({ url: 'u' });
  assert.equal(calls.u, n, 'negative result reused');
  tick(600);
  await f.get({ url: 'u' });
  assert.ok(calls.u > n, 'tried again after the backoff');
});

test('the reducer runs before caching; null counts as a failure', async () => {
  const { f } = setup(() => ({ code: 200, body: 'xxxxxxxx' }));
  assert.equal((await f.get({ url: 'a', reduce: (b) => String(b.length) })).body, '8');
  assert.equal((await f.get({ url: 'b', reduce: () => null })).code, 0);
});

test('has() is true only for usable copies', async () => {
  const { f } = setup((url) => (url === 'bad' ? new Error('x') : { code: 200, body: '1' }));
  await f.get({ url: 'ok' });
  await f.get({ url: 'bad' });
  assert.deepEqual([f.has('ok'), f.has('bad'), f.has('never')], [true, false, false]);
});
