import assert from 'node:assert/strict';
import { test } from 'node:test';
import { clean, decode, firstTable, tableAfter } from '../html.ts';
import { parseDebianCve } from '../sources.ts';

const CVE_PAGE = `<!DOCTYPE html><html><body><header><h1>CVE-2026-91768</h1></header>
<table><tr><td><b>Name</b></td><td>CVE-2026-91768</td></tr>
<tr><td><b>Description</b></td><td>The IPv6 branch &amp; the <a href="x">FastCGI</a> check</td></tr></table>
<h2>Vulnerable and fixed packages</h2><p>The table below lists information on source packages.</p>
<table><tr><th>Source Package</th><th>Release</th><th>Version</th><th>Status</th></tr>
<tr><td><a href="/tracker/source-package/php8.2">php8.2</a> (<a href="https://tracker.debian.org/pkg/php8.2">PTS</a>)</td><td>bookworm</td><td><span class="red">8.2.32-1~deb12u1</span></td><td><span class="red">vulnerable</span></td></tr>
<tr><td></td><td>bookworm (security)</td><td>8.2.34-1~deb12u1</td><td>fixed</td></tr>
<tr><td><a href="/tracker/source-package/php8.4">php8.4</a></td><td>trixie</td><td>8.4.24-1~deb13u1</td><td>vulnerable</td></tr>
<tr><td></td><td>forky, sid</td><td>8.4.26-1</td><td>fixed</td></tr>
<tr><td><a href="/tracker/source-package/libxml2">libxml2</a></td><td>trixie</td><td>2.12</td><td>fixed</td></tr>
</table></body></html>`;

test('decode and clean', () => {
  assert.equal(decode('a &amp; b &lt;c&gt; &#39;d&#39; &#x41; &bogus;'), "a & b <c> 'd' A &bogus;");
  assert.equal(clean('<b> x </b>\n  y'), 'x y');
});

test('tableAfter finds the table under a heading, td rows only', () => {
  const rows = tableAfter(CVE_PAGE, 'Vulnerable and fixed');
  assert.equal(rows.length, 5);
  assert.equal(rows[0][0].link, 'php8.2');
  assert.equal(rows[0][3].text, 'vulnerable');
  assert.deepEqual(tableAfter(CVE_PAGE, 'No such heading'), []);
});

test('firstTable reads the name/description table', () => {
  assert.equal(firstTable(CVE_PAGE)[1][1].text, 'The IPv6 branch & the FastCGI check');
});

test('parseDebianCve: security upload supersedes, non-php dropped, carry-forward of the package', () => {
  const d = parseDebianCve({ code: 200, body: CVE_PAGE, age: 0, stale: false });
  assert.ok(d);
  assert.match(d.description, /IPv6 branch/);
  assert.deepEqual(
    d.rows.map((r) => [r.package, r.release, r.level]),
    [['php8.2', 'bookworm', 'ok'], ['php8.4', 'trixie', 'bad'], ['php8.4', 'forky, sid', 'ok']],
  );
});

test('parseDebianCve: 404 and errors give null', () => {
  assert.equal(parseDebianCve({ code: 404, body: '', age: 0, stale: false }), null);
  assert.equal(parseDebianCve({ code: 0, body: '', age: 0, stale: true }), null);
});
