import assert from 'node:assert/strict';
import { gzipSync } from 'node:zlib';
import { test } from 'node:test';
import { Fetcher, type RawGet } from '../fetcher.ts';
import { Budget } from '../ratelimit.ts';
import { build, levelOf, reduceAlpine, reduceChangelog, reduceDocker, reduceRedhat, reduceSury, reduceUbuntu } from '../sources.ts';

const buf = (s: string) => Buffer.from(s);

test('levelOf maps tracker words', () => {
  assert.deepEqual(['vulnerable', 'needed', 'fixed', 'released', 'not-affected', 'needs-triage'].map(levelOf), ['bad', 'bad', 'ok', 'ok', 'none', 'warn']);
});

test('reduceSury keeps the first version of each php8.x package', () => {
  const idx = 'Package: php8.4\nVersion: 8.4.27-1+x\nArchitecture: all\n\nPackage: php8.4-cli\nVersion: 8.4.27-1\n\nPackage: php8.5\nVersion: 8.5.12-1\n\nPackage: php8.4\nVersion: 8.4.1-1\n';
  assert.deepEqual(JSON.parse(reduceSury(gzipSync(idx)) as string), { 'php8.4': '8.4.27-1+x', 'php8.5': '8.5.12-1' });
  assert.equal(reduceSury(buf('not gzip')), null);
});

test('reduceSury also reads the PPA layout, where Version is not next to Package', () => {
  const ppa = 'Package: php8.4\nPriority: optional\nSection: php\nInstalled-Size: 12\nMaintainer: x\nArchitecture: all\nVersion: 8.4.26-1+ubuntu24.04.1+deb.sury.org+1\nDepends: y\n\nPackage: php8.4-cli\nVersion: 1\n';
  assert.deepEqual(JSON.parse(reduceSury(gzipSync(ppa)) as string), { 'php8.4': '8.4.26-1+ubuntu24.04.1+deb.sury.org+1' });
});

test('reduceDocker picks the newest plain cli-alpine tag per branch', () => {
  const tags = ['8.4.26-cli-alpine', '8.4.9-cli-alpine', '8.4-cli-alpine', '8.4.27RC1-cli-alpine', '8.5.11-cli-alpine', '8.4.26-cli-alpine3.22'].map((name) => ({ name, last_updated: '2026-09-27T21:58:01Z' }));
  assert.deepEqual(JSON.parse(reduceDocker(buf(JSON.stringify({ results: tags }))) as string), {
    '8.4': { version: '8.4.26', date: '2026-09-27' },
    '8.5': { version: '8.5.11', date: '2026-09-27' },
  });
});

test('reduceChangelog indexes CVEs per release and the newest release per branch', () => {
  const html = `<section class="version" id="8.4.27"><h3>Version 8.4.27</h3><time datetime='2026-10-22'>x</time>Fixed CVE-2026-1111 and CVE-2026-1111</section>
<section class="version" id="8.4.26"><time datetime='2026-09-24'>x</time>nothing</section>
<section class="version" id="8.3.36"><time datetime='2026-10-22'>x</time>CVE-2026-2222</section>`;
  const idx = JSON.parse(reduceChangelog(buf(html)) as string);
  assert.deepEqual(idx.cves, { '8.4.27': { date: '2026-10-22', cves: ['CVE-2026-1111'] }, '8.3.36': { date: '2026-10-22', cves: ['CVE-2026-2222'] } });
  assert.equal(idx.latest['8.4'].version, '8.4.27');
  assert.equal(reduceChangelog(buf('<html></html>')), null);
});

test('reduceUbuntu keeps php rows on supported releases only', () => {
  const j = { description: 'd', packages: [{ name: 'php8.3', statuses: [{ release_codename: 'noble', status: 'needs-triage', description: '' }, { release_codename: 'xenial', status: 'needed', description: '' }, { release_codename: 'jammy', status: 'DNE', description: '' }] }, { name: 'curl', statuses: [{ release_codename: 'noble', status: 'needed', description: '' }] }] };
  assert.deepEqual(JSON.parse(reduceUbuntu(buf(JSON.stringify(j))) as string).rows, [{ package: 'php8.3', release: 'noble', status: 'needs-triage', version: '' }]);
});

test('reduceRedhat keeps RHEL 8 to 10 and maps fix states', () => {
  const j = { bugzilla: { description: 'php: bug' }, affected_release: [{ product_name: 'Red Hat Enterprise Linux 9', advisory: 'RHSA-1', package: 'php-8' }, { product_name: 'Red Hat Hardened Images', advisory: 'X' }], package_state: [{ product_name: 'Red Hat Enterprise Linux 10', fix_state: 'Affected', package_name: 'php' }, { product_name: 'Red Hat Enterprise Linux 6', fix_state: 'Affected', package_name: 'php' }] };
  const r = JSON.parse(reduceRedhat(buf(JSON.stringify(j))) as string);
  assert.deepEqual(r.rows.map((x: { release: string; level: string }) => [x.release, x.level]), [['Red Hat Enterprise Linux 9', 'ok'], ['Red Hat Enterprise Linux 10', 'bad']]);
});

test('reduceAlpine keeps php packages', () => {
  const j = { packages: [{ pkg: { name: 'php84', secfixes: { '8.4.16-r0': ['CVE-1'] } } }, { pkg: { name: 'curl', secfixes: {} } }] };
  assert.deepEqual(JSON.parse(reduceAlpine(buf(JSON.stringify(j))) as string), { php84: { '8.4.16-r0': ['CVE-1'] } });
});

// ---- build() end to end, against fixtures ----

const ACTIVE = { 8: { '8.2': { date: '21 Oct 2026', tags: ['security'], source: [{ name: 'PHP 8.2.35 (tar.gz)' }] }, '8.4': { date: '21 Oct 2026', tags: ['security'], source: [{ name: 'PHP 8.4.27 (tar.gz)' }] } } };
const CHANGELOG = '<section class="version" id="8.4.27"><time datetime=\'2026-10-22\'>x</time>CVE-2026-5000</section>';
const TRACKER = (rows: string) => `<h1>x</h1><table><tr><td><b>Description</b></td><td>desc</td></tr></table><h2>Vulnerable and fixed packages</h2><table><tr><th>a</th></tr>${rows}</table>`;
const row = (pkg: string, rel: string, ver: string, st: string) => `<tr><td>${pkg ? `<a href="/x">${pkg}</a>` : ''}</td><td>${rel}</td><td>${ver}</td><td>${st}</td></tr>`;

function world(pages: Record<string, string | number | Buffer>): { fetcher: Fetcher; budget: Budget } {
  const get: RawGet = async (url) => {
    const hit = Object.entries(pages).find(([k]) => url.includes(k));
    if (!hit) return { code: 404, body: Buffer.alloc(0) };
    return typeof hit[1] === 'number' ? { code: hit[1], body: Buffer.alloc(0) } : { code: 200, body: Buffer.from(hit[1]) };
  };
  return { fetcher: new Fetcher({ get, retryDelayMs: 0 }), budget: new Budget(2, 60_000) };
}

const BASE = {
  'releases/active.php': JSON.stringify(ACTIVE),
  'ChangeLog-8.php': CHANGELOG,
  'library/php/tags': JSON.stringify({ results: [{ name: '8.4.26-cli-alpine', last_updated: '2026-09-27' }] }),
  'source-package/php8.4': '<h2>Available versions</h2><table><tr><th>Release</th><th>Version</th></tr><tr><td>trixie</td><td>8.4.24-1~deb13u1</td></tr><tr><td>trixie (security)</td><td>8.4.26-1~deb13u1</td></tr></table>',
  'api/src/php8.4': JSON.stringify({ versions: [{ suites: ['trixie-proposed-updates'], version: '8.4.27-1~deb13u1' }] }),
  'dists/trixie/main': gzipSync('Package: php8.4\nVersion: 8.4.27-1+sury\n'),
};

test('build: matrix, upstream, pending and a CVE named upstream but not yet in any tracker', async () => {
  const ctx = world({ ...BASE });
  const s = await build(['CVE-2026-109618'], ctx);
  assert.equal(s.upstream['8.4'].version, '8.4.27');
  const docker = s.matrix.find((r) => r.repo === 'Docker')?.cells['8.4'];
  assert.deepEqual([docker?.short, docker?.state], ['8.4.26', 'behind']);
  const sury = s.matrix.find((r) => r.repo === 'Sury (Debian)' && r.label === 'trixie')?.cells['8.4'];
  assert.equal(sury?.state, 'current');
  assert.equal(s.packages.find((p) => p.name === 'php8.4')?.pending, '8.4.27-1~deb13u1');
  assert.equal(s.cves[0].state, 'unknown');
  assert.ok(s.unreachable.length === 0 || s.unreachable.every((h) => typeof h === 'string'));
});

test('build: state is vulnerable when a stable release still is, fixed when the security upload is', async () => {
  const vuln = TRACKER(row('php8.4', 'trixie', '8.4.24-1~deb13u1', 'vulnerable'));
  const fixed = TRACKER(row('php8.4', 'trixie', '8.4.24-1~deb13u1', 'vulnerable') + row('', 'trixie (security)', '8.4.26-1~deb13u1', 'fixed'));
  const a = await build(['CVE-2026-5000'], world({ ...BASE, 'tracker/CVE-2026-5000': vuln }));
  assert.equal(a.cves[0].state, 'vulnerable');
  assert.deepEqual(a.cves[0].upstream, [{ version: '8.4.27', date: '2026-10-22' }]);
  const b = await build(['CVE-2026-5000'], world({ ...BASE, 'tracker/CVE-2026-5000': fixed }));
  assert.equal(b.cves[0].state, 'fixed');
});

test('build: a source that is down is reported, the rest still works', async () => {
  const s = await build(['CVE-2026-5000'], world({ ...BASE, 'packages.sury.org': 503, 'ubuntu.com': 503 }));
  assert.ok(s.unreachable.includes('packages.sury.org'));
  assert.equal(s.cves[0].sources.find((x) => x.name === 'Ubuntu')?.status, 'error');
});

test('build: CVEs outside the default list draw on the budget, then are refused', async () => {
  const ctx = world({ ...BASE });
  const s = await build(['CVE-2026-7001', 'CVE-2026-7002', 'CVE-2026-7003'], ctx); // budget of 2
  assert.deepEqual(s.cves.map((c) => c.state === 'limited'), [false, false, true]);
  assert.equal(s.cves[2].sources.length, 0);
});

test('build: php.net active.php down falls back to the changelog for the latest versions', async () => {
  const s = await build(['CVE-2026-5000'], world({ ...BASE, 'releases/active.php': 504 }));
  assert.equal(s.upstream['8.4'].version, '8.4.27');
  assert.ok(s.unreachable.includes('www.php.net'));
});
