'use strict';
// PHP watch: renders /api.php (the pod digests php.net, Debian, Ubuntu, Red Hat, Alpine, Sury and Docker Hub).
// All text goes in with textContent, never innerHTML: CVE descriptions come from third parties.

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
  fixed: 'Fix recorded',
  vulnerable: 'Vulnerable',
  tracked: 'Listed, no fix recorded',
  unknown: 'No record yet',
  error: 'Sources unreachable',
};
const STATE_LEVEL = { fixed: 'ok', vulnerable: 'bad', tracked: 'warn', unknown: 'warn', error: 'none' };

const upstreamNote = (c) => (c.upstream.length ? `named in php.net changelog for ${c.upstream.map((u) => u.version).join(', ')}` : '');

function verdictOf(d) {
  const items = [];
  for (const c of d.cves) {
    const up = c.upstream.length ? `; fixed upstream in ${c.upstream[0].version}` : '';
    if (c.state === 'vulnerable') {
      const where = [];
      for (const s of c.sources) for (const r of s.rows) if (r.level === 'bad') where.push(`${s.name} ${r.release}`);
      items.push({ level: 'bad', area: c.id, text: `vulnerable in ${where.slice(0, 3).join(', ')}${where.length > 3 ? '…' : ''}${up}` });
    } else if (c.state === 'tracked') {
      const names = c.sources.filter((s) => s.rows.length).map((s) => s.name);
      items.push({ level: 'warn', area: c.id, text: `listed by ${names.join(', ')}, no fix recorded${up}` });
    } else if (c.state === 'unknown') {
      items.push({ level: 'warn', area: c.id, text: `no record in any tracker yet${up}` });
    } else if (c.state === 'error') {
      items.push({ level: 'warn', area: c.id, text: 'trackers did not answer' });
    }
  }
  const behind = d.matrix.filter((r) => r.repo !== 'php.net' && Object.values(r.cells).some((x) => x && x.state === 'behind'));
  if (behind.length) {
    const names = [...new Set(behind.map((r) => (r.repo === 'Debian' ? `Debian ${r.label}` : r.repo.startsWith('Sury') ? `${r.repo} ${r.label}` : r.repo)))];
    items.push({ level: 'warn', area: 'Behind php.net', text: `${names.slice(0, 3).join(', ')}${names.length > 3 ? ` +${names.length - 3}` : ''}` });
  }
  for (const p of d.packages) {
    if (p.open.length) items.push({ level: 'warn', area: p.name, text: `${p.open.length} open ${p.open.length === 1 ? 'issue' : 'issues'} in Debian` });
  }
  const bad = items.some((i) => i.level === 'bad');
  let state = 'ok';
  let title = 'PHP Patched';
  if (bad) { state = 'bad'; title = `PHP: ${items.length} ${items.length === 1 ? 'Issue' : 'Issues'}`; }
  else if (items.length) { state = 'warn'; title = `PHP: ${items.length} to Watch`; }
  const fixed = d.cves.filter((c) => c.state === 'fixed').length;
  const latest = Object.entries(d.upstream).map(([b, u]) => u.version).join(', ');
  const summary = `${fixed} of ${d.cves.length} watched ${d.cves.length === 1 ? 'CVE has' : 'CVEs have'} a recorded fix. php.net's latest: ${latest}.`;
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

function sourceBlock(s) {
  const box = el('div', 'src');
  const head = el('div', 'src-head');
  const a = el('a', 'src-name', s.name);
  a.href = s.url;
  a.rel = 'noopener noreferrer';
  head.append(a);
  box.append(head);
  const rows = el('ul', 'rows');
  for (const r of s.rows) {
    const row = el('li', 'row');
    row.dataset.level = r.level;
    row.append(el('span', 'row-rel', r.release), el('span', 'row-pkg', r.package), el('span', 'row-ver', r.version), el('span', 'row-st', r.status));
    rows.append(row);
  }
  box.append(rows);
  return box;
}

function renderCves(d) {
  const ul = $('cves');
  ul.replaceChildren();
  for (const c of d.cves) {
    const li = el('li', 'cve');
    li.dataset.level = STATE_LEVEL[c.state];
    const head = el('div', 'cve-head');
    const link = el('a', 'cve-id', c.id);
    link.href = `https://www.cve.org/CVERecord?id=${encodeURIComponent(c.id)}`;
    link.rel = 'noopener noreferrer';
    head.append(link, el('span', 'badge', STATE_TEXT[c.state]));
    const rm = el('button', 'rm', '×');
    rm.type = 'button';
    rm.title = `Stop watching ${c.id}`;
    rm.setAttribute('aria-label', rm.title);
    rm.addEventListener('click', () => saveWatched(d.cves.map((x) => x.id).filter((id) => id !== c.id)));
    head.append(rm);
    li.append(head);
    li.append(el('p', 'cve-desc', c.description || 'No description published yet.'));
    const up = el('p', c.upstream.length ? 'chip chip-ok' : 'chip');
    up.textContent = c.upstream.length ? `php.net: ${upstreamNote(c)}` : 'php.net: not named in the changelog yet (it may cite a GHSA id instead)';
    li.append(up);
    const srcs = el('div', 'srcs');
    for (const s of c.sources.filter((x) => x.rows.length)) srcs.append(sourceBlock(s));
    if (srcs.children.length) li.append(srcs);
    // Sources with nothing to show share one line instead of one block each.
    const quiet = c.sources.filter((x) => !x.rows.length);
    if (quiet.length) {
      const line = el('p', 'quiet');
      line.append(document.createTextNode('No record at '));
      quiet.forEach((s, i) => {
        const a = el('a', 'src-name', s.name + (s.status === 'error' ? ' (unreachable)' : ''));
        a.href = s.url;
        a.rel = 'noopener noreferrer';
        line.append(a, document.createTextNode(i < quiet.length - 1 ? ', ' : ''));
      });
      li.append(line);
    }
    ul.append(li);
  }
}

function renderMatrix(d) {
  const box = $('matrix');
  box.replaceChildren();
  const branches = Object.keys(d.matrix[0].cells);
  const head = el('div', 'mx-row mx-head');
  head.setAttribute('role', 'row');
  head.append(el('span', 'mx-name', 'Repo'));
  for (const b of branches) head.append(el('span', 'mx-cell', `PHP ${b}`));
  box.append(head);
  for (const r of d.matrix) {
    const row = el('div', 'mx-row');
    row.setAttribute('role', 'row');
    const name = el('span', 'mx-name');
    name.append(el('strong', null, r.repo), el('small', null, r.label));
    row.append(name);
    for (const b of branches) {
      const c = r.cells[b];
      const cell = el('span', 'mx-cell', c ? c.short : '–');
      if (c) {
        cell.dataset.state = c.state;
        cell.title = c.version;
      }
      row.append(cell);
    }
    box.append(row);
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
    renderMatrix(d);
    renderPackages(d);
    if (d.unreachable.length) stamp(`Unreachable: ${d.unreachable.join(', ')}`, 'stale');
    else stamp(`Sources read ${ago(d.age)}`, '');
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
