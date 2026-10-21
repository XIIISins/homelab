'use strict';
// PHP watch: renders /api.php (the pod digests the Debian security tracker). All text goes in
// with textContent, never innerHTML: CVE descriptions come from a third party.

const POLL_MS = 5 * 60 * 1000;
const KEY = 'phpwatch.cves';
const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

let watched = null;   // null: use the server's default list until the visitor edits it
try { watched = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch { watched = null; }
let last = null;

function saveWatched(list) {
  watched = list;
  try { localStorage.setItem(KEY, JSON.stringify(list)); } catch { /* private mode */ }
  load();
}

const STATE_TEXT = {
  fixed: 'Fixed in Debian',
  vulnerable: 'Vulnerable in Debian',
  tracked: 'Tracked, no stable impact',
  unknown: 'No Debian record yet',
  error: 'Tracker unreachable',
};
const STATE_LEVEL = { fixed: 'ok', vulnerable: 'bad', tracked: 'none', unknown: 'warn', error: 'none' };

function verdictOf(d) {
  const items = [];
  for (const c of d.cves) {
    if (c.state === 'vulnerable') {
      const where = c.rows.filter((r) => r.level === 'bad').map((r) => `${r.release} (${r.package})`).join(', ');
      items.push({ level: 'bad', area: c.id, text: `vulnerable in ${where}` });
    } else if (c.state === 'unknown') {
      items.push({ level: 'warn', area: c.id, text: 'no Debian record yet' });
    } else if (c.state === 'error') {
      items.push({ level: 'warn', area: c.id, text: 'tracker did not answer' });
    }
  }
  for (const p of d.packages) {
    if (p.open.length) items.push({ level: 'warn', area: p.name, text: `${p.open.length} open ${p.open.length === 1 ? 'issue' : 'issues'}` });
  }
  const bad = items.filter((i) => i.level === 'bad').length;
  let state = 'ok';
  let title = 'PHP Patched';
  if (bad) { state = 'bad'; title = `PHP: ${items.length} ${items.length === 1 ? 'Issue' : 'Issues'}`; }
  else if (items.length) { state = 'warn'; title = `PHP: ${items.length} to Watch`; }
  const fixed = d.cves.filter((c) => c.state === 'fixed').length;
  const summary = `${fixed} of ${d.cves.length} watched ${d.cves.length === 1 ? 'CVE is' : 'CVEs are'} fixed in Debian. ` +
    d.packages.filter((p) => p.tracked).map((p) => `${p.release} runs ${p.name} ${p.current}.`).join(' ');
  return { state, title, summary, items };
}

function renderVerdict(v) {
  $('verdict').dataset.state = v.state;
  $('verdict-title').textContent = v.title;
  const list = $('attention');
  const detail = $('verdict-detail');
  list.replaceChildren();
  if (v.items.length) {
    for (const i of v.items.slice(0, 4)) {
      const li = el('li');
      li.dataset.level = i.level;
      li.append(el('span', 'att-area', i.area), el('span', null, i.text));
      list.append(li);
    }
    if (v.items.length > 4) {
      const li = el('li', null, `+${v.items.length - 4} more`);
      li.dataset.level = 'more';
      list.append(li);
    }
    list.hidden = false;
    detail.hidden = true;
  } else {
    detail.textContent = v.summary;
    detail.hidden = false;
    list.hidden = true;
  }
  document.title = `PHP watch: ${v.state === 'ok' ? 'patched' : v.items.length + ' to watch'}`;
}

function renderCves(d) {
  const ul = $('cves');
  ul.replaceChildren();
  for (const c of d.cves) {
    const li = el('li', 'cve');
    li.dataset.level = STATE_LEVEL[c.state];
    const head = el('div', 'cve-head');
    const link = el('a', 'cve-id', c.id);
    link.href = c.url;
    link.rel = 'noopener noreferrer';
    head.append(link, el('span', 'badge', STATE_TEXT[c.state]));
    const rm = el('button', 'rm', '×');
    rm.type = 'button';
    rm.title = `Stop watching ${c.id}`;
    rm.setAttribute('aria-label', rm.title);
    rm.addEventListener('click', () => saveWatched(d.cves.map((x) => x.id).filter((id) => id !== c.id)));
    head.append(rm);
    li.append(head);
    li.append(el('p', 'cve-desc', c.description || (c.state === 'unknown'
      ? 'The Debian tracker has no entry for this id. It is either too new to be triaged or not a PHP CVE.'
      : '')));
    if (c.rows.length) {
      const rows = el('ul', 'rows');
      for (const r of c.rows) {
        const row = el('li', 'row');
        row.dataset.level = r.level;
        row.append(el('span', 'row-rel', r.release), el('span', 'row-pkg', r.package), el('span', 'row-ver', r.version), el('span', 'row-st', r.status));
        rows.append(row);
      }
      li.append(rows);
    }
    ul.append(li);
  }
}

function renderPackages(d) {
  const box = $('pkgs');
  box.replaceChildren();
  for (const p of d.packages) {
    const card = el('article', 'pkg');
    card.append(el('h3', 'pkg-name', p.name), el('p', 'card-note', `Debian ${p.release}`));
    card.append(el('p', 'pkg-ver', p.current || 'not packaged'));
    if (p.pending) card.append(el('p', 'chip', `queued in ${p.release}-proposed-updates: ${p.pending}`));
    if (!p.tracked) card.append(el('p', 'card-note', 'Not in the security tracker: no stable release ships it yet.'));
    if (p.open.length) {
      const ul = el('ul', 'open');
      for (const o of p.open) ul.append(el('li', null, `${o.id}: ${o.description}`));
      card.append(ul);
    } else if (p.tracked) {
      card.append(el('p', 'card-note ok-note', 'No open security issues.'));
    }
    const suites = el('ul', 'suites');
    for (const v of p.versions) {
      const li = el('li');
      li.append(el('span', 'suite', v.suite), el('span', 'ver', v.version));
      suites.append(li);
    }
    card.append(suites);
    box.append(card);
  }
}

function stamp(text, mode) {
  const s = $('stamp');
  delete s.dataset.stale;
  delete s.dataset.down;
  if (mode) s.dataset[mode] = '';
  $('stamp-text').textContent = text;
}

function ago(sec) {
  if (sec < 90) return 'just now';
  if (sec < 5400) return `${Math.round(sec / 60)} minutes ago`;
  return `${Math.round(sec / 3600)} hours ago`;
}

async function load() {
  const qs = (watched || []).map((id) => 'cve=' + encodeURIComponent(id)).join('&');
  try {
    const res = await fetch('api.php' + (qs ? '?' + qs : ''), { cache: 'no-store' });
    if (!res.ok) throw new Error(res.status);
    const d = await res.json();
    last = d;
    renderVerdict(verdictOf(d));
    renderCves(d);
    renderPackages(d);
    stamp(d.stale ? `Debian unreachable, showing data from ${ago(d.age)}` : `Debian data ${ago(d.age)}`, d.stale ? 'stale' : '');
  } catch {
    if (last) stamp('Could not refresh, showing the last reading', 'stale');
    else {
      stamp('No data', 'down');
      $('verdict-title').textContent = 'PHP: No Data';
      $('verdict').dataset.state = 'none';
      $('verdict-detail').textContent = 'The page could not reach its own backend. Trying again every five minutes.';
    }
  }
}

$('add').addEventListener('submit', (e) => {
  e.preventDefault();
  const id = $('cve-input').value.trim().toUpperCase();
  if (!/^CVE-\d{4}-\d{4,7}$/.test(id)) return;
  const ids = (last ? last.cves.map((c) => c.id) : []);
  if (!ids.includes(id)) ids.push(id);
  $('cve-input').value = '';
  saveWatched(ids.slice(0, 8));
});

$('theme').addEventListener('click', () => {
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  const cur = document.documentElement.dataset.theme || (dark ? 'dark' : 'light');
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('phpwatch.theme', next); } catch { /* private mode */ }
  for (const m of document.querySelectorAll('meta[name="theme-color"]')) m.content = next === 'light' ? '#e1e2e7' : '#16161e';
});

load();
setInterval(load, POLL_MS);
document.addEventListener('visibilitychange', () => { if (!document.hidden && last && Date.now() / 1000 - last.generated > 300) load(); });
