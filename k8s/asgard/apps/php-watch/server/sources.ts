// One section per source. Two questions are answered:
//   "Is there a fix for CVE-X, and where?"   php.net changelog, Debian, Ubuntu, Red Hat, Alpine
//   "Which PHP does each repo ship today?"   php.net, Debian, Sury (Debian repo + Ubuntu PPA), Docker official image
// Every source is a public read-only URL on a fixed host. The browser cannot ask them
// directly (no CORS headers), which is why this runs server-side.

import { gunzipSync } from 'node:zlib';
import { Fetcher, type Result, type Src } from './fetcher.ts';
import { clean, firstTable, tableAfter } from './html.ts';
import { Budget } from './ratelimit.ts';
import type { Cve, CveSource, CveState, DebianPackage, Level, MatrixRow, Row, SourceData, Status, Upstream } from './types.ts';
import { cmpNumeric, debCompare, upstreamOf } from './versions.ts';

export const MAX_CVES = 8;
export const DEFAULT_CVES = ['CVE-2026-109618', 'CVE-2026-109619'];
export const CVE_RE = /^CVE-\d{4}-\d{4,7}$/;
const BRANCHES = ['8.2', '8.3', '8.4', '8.5'];

const PHPNET_ACTIVE = 'https://www.php.net/releases/active.php';
const PHPNET_CHANGELOG = 'https://www.php.net/ChangeLog-8.php';
const DEBIAN_TRACKER = 'https://security-tracker.debian.org/tracker/';
const DEBIAN_SOURCES = 'https://sources.debian.org/api/src/';
const DOCKER_TAGS = 'https://hub.docker.com/v2/repositories/library/php/tags?name=cli-alpine&page_size=100&ordering=last_updated';
const ubuntuUrl = (id: string) => `https://ubuntu.com/security/cves/${id}.json`;
const redhatUrl = (id: string) => `https://access.redhat.com/hydra/rest/securitydata/cve/${id}.json`;
const alpineUrl = (branch: string) => `https://secdb.alpinelinux.org/${branch}/community.json`;
const suryUrl = (suite: string) => `https://packages.sury.org/php/dists/${suite}/main/binary-amd64/Packages.gz`;
const suryPpaUrl = (suite: string) => `https://ppa.launchpadcontent.net/ondrej/php/ubuntu/dists/${suite}/main/binary-amd64/Packages.gz`;

// Debian source package => the Debian release that ships it and the PHP branch it is
const DEBIAN_PACKAGES: Record<string, [string, string]> = { 'php8.2': ['bookworm', '8.2'], 'php8.4': ['trixie', '8.4'], 'php8.5': ['experimental', '8.5'] };
const DEBIAN_STABLE = ['bookworm', 'trixie'];
const SURY_SUITES = ['bookworm', 'trixie'];
const SURY_PPA_SUITES = ['jammy', 'noble'];
const ALPINE_BRANCHES = ['v3.23', 'v3.24'];
// Ubuntu releases that still get PHP fixes; older ones (ESM) would keep a CVE "vulnerable" forever.
const UBUNTU_RELEASES = ['jammy', 'noble', 'resolute', 'stonking'];
const REDHAT_RELEASE_RE = /Enterprise Linux (8|9|10)$/;

// ---------- Reducers: boil a source down before it is cached ----------

type Json = Record<string, any>; // third-party JSON, checked field by field where it is read

const parseJson = (b: Buffer): Json | null => {
  try {
    const v = JSON.parse(b.toString('utf8'));
    return v && typeof v === 'object' ? v : null;
  } catch {
    return null;
  }
};

export interface ChangelogIndex {
  /** Versions whose notes name CVEs. */
  cves: Record<string, { date: string; cves: string[] }>;
  /** Newest version per branch, a fallback for when releases/active.php is down. */
  latest: Record<string, { version: string; date: string }>;
}

/** php.net ChangeLog (750 KB of HTML) -> the CVEs each release names, and the newest release per branch. */
export function reduceChangelog(b: Buffer): string | null {
  const html = b.toString('utf8');
  const out: ChangelogIndex = { cves: {}, latest: {} };
  let sections = 0;
  for (const m of html.matchAll(/<section class="version" id="(\d+\.\d+\.\d+)">([\s\S]*?)<\/section>/g)) {
    sections++;
    const date = /datetime='([\d-]+)'/.exec(m[2])?.[1] ?? '';
    const branch = m[1].split('.').slice(0, 2).join('.');
    if (!out.latest[branch] || cmpNumeric(m[1], out.latest[branch].version) > 0) out.latest[branch] = { version: m[1], date };
    const cves = [...new Set(m[2].match(/CVE-\d{4}-\d{4,7}/g) ?? [])];
    if (cves.length) out.cves[m[1]] = { date, cves };
  }
  return sections ? JSON.stringify(out) : null;
}

/** Sury Packages.gz (Debian repo or Ubuntu PPA) -> {"php8.4": "8.4.27-1+…"}. Field order differs between the two, so read each paragraph's own fields. */
export function reduceSury(b: Buffer): string | null {
  let text: string;
  try {
    text = gunzipSync(b).toString('utf8');
  } catch {
    return null;
  }
  const out: Record<string, string> = {};
  for (const para of text.split('\n\n')) {
    const name = /^Package: (php8\.\d)$/m.exec(para)?.[1];
    const version = /^Version: (\S+)$/m.exec(para)?.[1];
    if (name && version) out[name] ??= version;
  }
  return Object.keys(out).length ? JSON.stringify(out) : null;
}

/** Docker Hub tag list -> {"8.4": {version, date}}: the newest plain X.Y.Z-cli-alpine tag per branch. */
export function reduceDocker(b: Buffer): string | null {
  const d = parseJson(b);
  if (!Array.isArray(d?.results)) return null;
  const out: Record<string, { version: string; date: string }> = {};
  for (const t of d.results) {
    const m = /^(\d+\.\d+)\.(\d+)-cli-alpine$/.exec(String(t.name));
    if (!m) continue;
    const version = `${m[1]}.${m[2]}`;
    if (!out[m[1]] || cmpNumeric(version, out[m[1]].version) > 0) out[m[1]] = { version, date: String(t.last_updated ?? '').slice(0, 10) };
  }
  return JSON.stringify(out);
}

/** Ubuntu CVE JSON -> only the php packages, on the releases that matter. */
export function reduceUbuntu(b: Buffer): string | null {
  const d = parseJson(b);
  if (!Array.isArray(d?.packages)) return null;
  const rows: { package: string; release: string; status: string; version: string }[] = [];
  for (const p of d.packages) {
    if (!String(p.name).startsWith('php')) continue;
    for (const s of p.statuses ?? []) {
      if (UBUNTU_RELEASES.includes(s.release_codename) && s.status !== 'DNE') rows.push({ package: p.name, release: s.release_codename, status: s.status, version: String(s.description ?? '') });
    }
  }
  return JSON.stringify({ description: String(d.description ?? ''), rows });
}

/** Red Hat CVE JSON -> rows for RHEL 8 to 10 only. */
export function reduceRedhat(b: Buffer): string | null {
  const d = parseJson(b);
  if (!d) return null;
  const rows: Row[] = [];
  for (const a of d.affected_release ?? []) {
    if (REDHAT_RELEASE_RE.test(String(a.product_name))) rows.push({ release: a.product_name, package: String(a.package ?? ''), version: '', status: `fixed ${a.advisory ?? ''}`.trim(), level: 'ok' });
  }
  for (const s of d.package_state ?? []) {
    if (!REDHAT_RELEASE_RE.test(String(s.product_name))) continue;
    const state = String(s.fix_state);
    const level: Level = state === 'Affected' ? 'bad' : state === 'Not affected' || state === 'Out of support scope' ? 'none' : 'warn';
    rows.push({ release: s.product_name, package: String(s.package_name), version: '', status: state.toLowerCase(), level });
  }
  return JSON.stringify({ description: clean(String(d.bugzilla?.description ?? '')), rows });
}

/** Alpine secdb -> {"php84": {"8.4.16-r0": ["CVE-…"]}} */
export function reduceAlpine(b: Buffer): string | null {
  const d = parseJson(b);
  if (!Array.isArray(d?.packages)) return null;
  const out: Record<string, Record<string, string[]>> = {};
  for (const p of d.packages) if (String(p.pkg?.name).startsWith('php')) out[p.pkg.name] = p.pkg.secfixes ?? {};
  return JSON.stringify(out);
}

// ---------- Parsers: one per source ----------

/** A tracker status word -> ok / bad / none / warn. */
export function levelOf(status: string): Level {
  const s = status.toLowerCase();
  if (s.includes('vulnerable') || s === 'needed' || s === 'pending') return 'bad';
  if (s.includes('fixed') || s === 'released') return 'ok';
  if (/not.affected|unimportant|end-of-life|ignored|no-dsa|deferred/.test(s)) return 'none';
  return 'warn';
}

const asJson = (r: Result): Json | null => {
  if (r.code !== 200) return null;
  try {
    return JSON.parse(r.body) as Json;
  } catch {
    return null;
  }
};

/** Debian tracker CVE page -> description + one row per package and release; null on 404 or error. */
export function parseDebianCve(r: Result): SourceData | null {
  if (r.code !== 200) return null;
  let description = '';
  for (const c of firstTable(r.body)) if (c.length === 2 && c[0].text === 'Description') description = c[1].text;
  let pkg = '';
  const per = new Map<string, Row>();
  for (const c of tableAfter(r.body, 'Vulnerable and fixed')) {
    if (c.length < 4) continue;
    pkg = c[0].link !== '' ? c[0].link : pkg;
    if (!pkg.startsWith('php')) continue;
    const status = c[c.length - 1].text;
    // One row per package and release: the "(security)" upload supersedes the plain row.
    const row: Row = { package: pkg, release: c[1].text.replace(/\s*\(security\)/, '').trim(), version: c[2].text, status, level: levelOf(status) };
    const key = `${row.package}|${row.release}`;
    const have = per.get(key);
    if (!have || (row.level === 'ok' && have.level !== 'ok')) per.set(key, row);
  }
  return { description, rows: [...per.values()] };
}

export function parseUbuntuCve(r: Result): SourceData | null {
  const d = asJson(r);
  if (!d || !Array.isArray(d.rows) || d.rows.length === 0) return null;
  return { description: clean(String(d.description ?? '')), rows: d.rows.map((x: Json) => ({ package: x.package, release: x.release, version: x.version, status: x.status, level: levelOf(x.status) })) };
}

export function parseRedhatCve(r: Result): SourceData | null {
  const d = asJson(r);
  if (!d || !Array.isArray(d.rows) || d.rows.length === 0) return null;
  // One row per release: a fix beats the state of some other package.
  const rank: Record<Level, number> = { ok: 4, bad: 3, warn: 2, none: 1 };
  const per = new Map<string, Row>();
  for (const row of d.rows as Row[]) {
    const have = per.get(row.release);
    if (!have || rank[row.level] > rank[have.level]) per.set(row.release, row);
  }
  return { description: String(d.description ?? ''), rows: [...per.values()] };
}

export function parseAlpineCve(id: string, byBranch: Record<string, Result>): SourceData | null {
  const rows: Row[] = [];
  for (const [branch, r] of Object.entries(byBranch)) {
    const d = asJson(r) as Record<string, Record<string, string[]>> | null;
    for (const [pkg, fixes] of Object.entries(d ?? {})) {
      for (const [version, cves] of Object.entries(fixes)) {
        if (cves.includes(id)) rows.push({ package: pkg, release: branch, version, status: 'fixed', level: 'ok' });
      }
    }
  }
  return rows.length ? { description: '', rows } : null;
}

/** Debian source-package page + sources.debian.org -> {suite label: version}. */
export function parseDebianPackage(tracker: Result, sources: Result, release: string): Record<string, string> {
  const have: Record<string, string> = {};
  if (tracker.code === 200) for (const c of tableAfter(tracker.body, 'Available versions')) if (c.length >= 2) have[c[0].text] = c[1].text;
  const d = asJson(sources);
  for (const v of d?.versions ?? []) {
    for (const suite of v.suites ?? []) if (suite === `${release}-proposed-updates` || suite === 'experimental') have[suite] = v.version;
  }
  return have;
}

export function openIssues(tracker: Result): { id: string; description: string }[] {
  if (tracker.code !== 200) return [];
  return tableAfter(tracker.body, 'Open issues')
    .filter((c) => c.length > 0 && c[0].link.startsWith('CVE-'))
    .map((c) => ({ id: c[0].link, description: c[1]?.text ?? '' }));
}

// ---------- The version matrix ----------

/** One matrix cell: the version, and whether it is as new as php.net's latest for that branch. */
export function cell(version: string | null | undefined, branch: string, upstream: Record<string, Upstream>) {
  if (!version) return null;
  const u = upstreamOf(version);
  const latest = upstream[branch]?.version;
  const state = u === null || latest === undefined ? 'none' : cmpNumeric(u, latest) < 0 ? 'behind' : 'current';
  return { version, short: u ?? version, state } as const;
}

export function matrixRow(repo: string, label: string, versions: Record<string, string | null | undefined>, upstream: Record<string, Upstream>): MatrixRow {
  const cells: MatrixRow['cells'] = {};
  for (const b of BRANCHES) cells[b] = cell(versions[b], b, upstream);
  return { repo, label, cells };
}

// ---------- The whole picture ----------

export interface Context {
  fetcher: Fetcher;
  /** Limits how many CVEs outside the default list may cost the other servers a request. */
  budget: Budget;
}

function sourcesFor(ids: string[]): { src: Src; host: string }[] {
  const list: Src[] = [
    { url: PHPNET_ACTIVE },
    { url: PHPNET_CHANGELOG, reduce: reduceChangelog },
    { url: DOCKER_TAGS, reduce: reduceDocker },
  ];
  for (const p of Object.keys(DEBIAN_PACKAGES)) list.push({ url: `${DEBIAN_TRACKER}source-package/${p}` }, { url: `${DEBIAN_SOURCES}${p}/` });
  for (const s of SURY_SUITES) list.push({ url: suryUrl(s), reduce: reduceSury });
  for (const s of SURY_PPA_SUITES) list.push({ url: suryPpaUrl(s), reduce: reduceSury, slow: true });
  for (const a of ALPINE_BRANCHES) list.push({ url: alpineUrl(a), reduce: reduceAlpine });
  for (const id of ids) list.push({ url: DEBIAN_TRACKER + id }, { url: ubuntuUrl(id), reduce: reduceUbuntu }, { url: redhatUrl(id), reduce: reduceRedhat });
  return list.map((src) => ({ src, host: new URL(src.url).host }));
}

export async function build(requested: string[], ctx: Context): Promise<Status> {
  const { fetcher } = ctx;
  // CVEs outside the default list cost other people's servers a request each. Ones already
  // cached are free; new ones draw on a small shared budget, and are refused when it is spent.
  const limited = new Set<string>();
  const ids = [...new Set(requested)].slice(0, MAX_CVES);
  for (const id of ids) {
    if (!DEFAULT_CVES.includes(id) && !fetcher.has(DEBIAN_TRACKER + id) && !ctx.budget.take()) limited.add(id);
  }
  const live = ids.filter((id) => !limited.has(id));

  const hostOf = new Map<string, string>();
  const results = new Map<string, Result>();
  await Promise.all(
    sourcesFor(live).map(async ({ src, host }) => {
      hostOf.set(src.url, host);
      results.set(src.url, await fetcher.get(src));
    }),
  );
  const res = (url: string): Result => results.get(url) as Result;

  let age = 0;
  let stale = false;
  const down = new Set<string>();
  for (const [url, r] of results) {
    age = Math.max(age, r.age);
    stale ||= r.stale;
    if (r.code === 0) down.add(hostOf.get(url) as string);
  }

  // php.net: the latest release per active branch, and which releases name which CVE.
  const upstream: Record<string, Upstream> = {};
  const active = asJson(res(PHPNET_ACTIVE))?.['8'] as Record<string, Json> | undefined;
  for (const [branch, rel] of Object.entries(active ?? {})) {
    const m = /PHP (\d+\.\d+\.\d+)/.exec(String(rel.source?.[0]?.name ?? ''));
    if (m) upstream[branch] = { version: m[1], date: String(rel.date ?? ''), security: (rel.tags ?? []).includes('security') };
  }
  const index = asJson(res(PHPNET_CHANGELOG)) as ChangelogIndex | null;
  const changelog = index?.cves ?? {};
  // releases/active.php is the authority, but php.net answers 5xx now and then; the changelog knows the same versions.
  for (const b of BRANCHES) if (!upstream[b] && index?.latest[b]) upstream[b] = { version: index.latest[b].version, date: index.latest[b].date, security: false };

  // The version matrix.
  const matrix: MatrixRow[] = [];
  matrix.push(matrixRow('php.net', 'upstream', Object.fromEntries(Object.entries(upstream).map(([b, u]) => [b, u.version])), upstream));
  const docker = (asJson(res(DOCKER_TAGS)) ?? {}) as Record<string, { version: string }>;
  matrix.push(matrixRow('Docker', 'official image, cli-alpine', Object.fromEntries(Object.entries(docker).map(([b, d]) => [b, d.version])), upstream));

  const dsuites: Record<string, Record<string, string | undefined>> = {};
  const packages: DebianPackage[] = [];
  for (const [name, [release, branch]] of Object.entries(DEBIAN_PACKAGES)) {
    const tracker = res(`${DEBIAN_TRACKER}source-package/${name}`);
    const have = parseDebianPackage(tracker, res(`${DEBIAN_SOURCES}${name}/`), release);
    if (DEBIAN_STABLE.includes(release)) (dsuites[release] ??= {})[branch] = have[`${release} (security)`] ?? have[release];
    if (have.sid) (dsuites.sid ??= {})[branch] = have.sid;
    if (have.experimental) (dsuites.experimental ??= {})[branch] = have.experimental;
    const queued = have[`${release}-proposed-updates`];
    const current = have[`${release} (security)`] ?? have[release] ?? have.experimental ?? null;
    packages.push({
      name,
      release,
      tracked: tracker.code === 200,
      current,
      pending: queued && current && debCompare(queued, current) > 0 ? queued : null,
      open: openIssues(tracker),
      versions: Object.entries(have).map(([suite, version]) => ({ suite, version })),
    });
  }
  for (const label of ['bookworm', 'trixie', 'sid', 'experimental']) if (dsuites[label]) matrix.push(matrixRow('Debian', label, dsuites[label], upstream));
  for (const s of SURY_SUITES) matrix.push(matrixRow('Sury (Debian)', s, versionsOf(asJson(res(suryUrl(s)))), upstream));
  for (const s of SURY_PPA_SUITES) {
    const v = asJson(res(suryPpaUrl(s)));
    if (v) matrix.push(matrixRow('Sury PPA (Ubuntu)', s, versionsOf(v), upstream));
  }

  // The CVEs.
  const cves: Cve[] = [];
  for (const id of ids) {
    if (limited.has(id)) {
      cves.push({ id, state: 'limited', description: '', upstream: [], sources: [] });
      continue;
    }
    const alpine = Object.fromEntries(ALPINE_BRANCHES.map((a) => [a, res(alpineUrl(a))]));
    const sources = [
      { name: 'Debian', url: DEBIAN_TRACKER + id, got: res(DEBIAN_TRACKER + id), data: parseDebianCve(res(DEBIAN_TRACKER + id)) },
      { name: 'Ubuntu', url: `https://ubuntu.com/security/${id}`, got: res(ubuntuUrl(id)), data: parseUbuntuCve(res(ubuntuUrl(id))) },
      { name: 'Red Hat', url: `https://access.redhat.com/security/cve/${id}`, got: res(redhatUrl(id)), data: parseRedhatCve(res(redhatUrl(id))) },
      { name: 'Alpine', url: `https://security.alpinelinux.org/vuln/${id}`, got: { code: Math.min(...Object.values(alpine).map((r) => r.code)) } as Result, data: parseAlpineCve(id, alpine) },
    ];
    let description = '';
    const levels: Level[] = [];
    let any = false;
    const out: CveSource[] = sources.map((s) => {
      const rows = s.data?.rows ?? [];
      description ||= s.data?.description ?? '';
      for (const r of rows) {
        any = true;
        // Debian also lists forky/sid rows; only its stable releases decide "vulnerable".
        if (s.name !== 'Debian' || DEBIAN_STABLE.includes(r.release)) levels.push(r.level);
      }
      return { name: s.name, url: s.url, status: s.data ? 'found' : s.got.code === 200 || s.got.code === 404 ? 'none' : 'error', rows };
    });
    let state: CveState = levels.includes('bad') ? 'vulnerable' : levels.includes('ok') ? 'fixed' : any ? 'tracked' : 'unknown';
    if (state === 'unknown' && out.every((o) => o.status === 'error')) state = 'error';
    const mentions = Object.entries(changelog)
      .filter(([, c]) => c.cves.includes(id))
      .map(([version, c]) => ({ version, date: c.date }));
    cves.push({ id, state, description, upstream: mentions, sources: out });
  }

  return { generated: Math.floor(Date.now() / 1000), age, stale, ttl: fetcher.ttlSeconds, unreachable: [...down].sort(), upstream, matrix, cves, packages };
}

function versionsOf(index: Json | null): Record<string, string | undefined> {
  return Object.fromEntries(BRANCHES.map((b) => [b, index?.[`php${b}`] as string | undefined]));
}
