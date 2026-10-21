// A cached, retrying HTTP fetch with stale-while-revalidate.
//   fresh entry            -> served from memory
//   stale entry            -> served at once, refreshed in the background
//   no entry               -> waits for the fetch (retried once)
//   fetch fails            -> the old copy keeps being served and is flagged stale;
//                             with no copy, the failure is remembered for FAIL_TTL so a
//                             dead source is not hammered by every visitor
// Concurrent callers for the same URL share one request. Big or binary bodies are
// reduced BEFORE they are cached, so memory holds only the small form.

export interface Src {
  url: string;
  /** Boils the raw body down; null = unusable (treated like a failed fetch). */
  reduce?: (body: Buffer) => string | null;
  /** Slow but rarely-changing host: longer timeout, longer reuse. */
  slow?: boolean;
}

export interface Result {
  /** 200, 404, or 0 when the source could not be read. */
  code: number;
  body: string;
  /** Seconds since the data was read. */
  age: number;
  /** True when the last refresh failed and this is an older copy (or nothing). */
  stale: boolean;
}

interface Entry {
  code: number;
  body: string;
  at: number;
  failedAt?: number;
}

export type RawGet = (url: string, timeoutMs: number) => Promise<{ code: number; body: Buffer }>;

export interface FetcherOptions {
  ttlMs?: number;
  slowTtlMs?: number;
  maxStaleMs?: number;
  failTtlMs?: number;
  maxEntries?: number;
  retryDelayMs?: number;
  get?: RawGet;
  now?: () => number;
}

const MAX_BODY = 4_000_000;
const UA = 'homelab-php-watch (read-only status page)';

/** GET over https only, following at most 3 redirects, capped body size. */
export const httpGet: RawGet = async (url, timeoutMs) => {
  let target = url;
  for (let hop = 0; hop < 4; hop++) {
    if (!target.startsWith('https://')) throw new Error(`refusing non-https URL ${target}`);
    const res = await fetch(target, { redirect: 'manual', signal: AbortSignal.timeout(timeoutMs), headers: { 'user-agent': UA, accept: '*/*' } });
    if (res.status >= 300 && res.status < 400 && res.headers.get('location')) {
      target = new URL(res.headers.get('location') as string, target).toString();
      await res.body?.cancel();
      continue;
    }
    const chunks: Buffer[] = [];
    let size = 0;
    for await (const chunk of res.body ?? []) {
      size += (chunk as Uint8Array).length;
      if (size > MAX_BODY) throw new Error(`${url}: body over ${MAX_BODY} bytes`);
      chunks.push(Buffer.from(chunk as Uint8Array));
    }
    return { code: res.status, body: Buffer.concat(chunks) };
  }
  throw new Error(`${url}: too many redirects`);
};

export class Fetcher {
  private cache = new Map<string, Entry>();
  private inflight = new Map<string, Promise<Entry | null>>();
  private o: Required<FetcherOptions>;

  constructor(opts: FetcherOptions = {}) {
    this.o = {
      ttlMs: 15 * 60_000,
      slowTtlMs: 60 * 60_000,
      maxStaleMs: 24 * 60 * 60_000,
      failTtlMs: 60_000,
      maxEntries: 600,
      retryDelayMs: 400,
      get: httpGet,
      now: Date.now,
      ...opts,
    };
  }

  get ttlSeconds(): number {
    return this.o.ttlMs / 1000;
  }

  /** Is there any usable copy (so asking again costs the source nothing)? */
  has(url: string): boolean {
    const e = this.cache.get(url);
    return e !== undefined && e.code !== 0 && this.o.now() - e.at < this.o.maxStaleMs;
  }

  async get(src: Src): Promise<Result> {
    const now = this.o.now();
    const ttl = src.slow ? this.o.slowTtlMs : this.o.ttlMs;
    const e = this.cache.get(src.url);
    const usable = e !== undefined && e.code !== 0 && now - e.at < this.o.maxStaleMs;

    if (e && usable) {
      const backoff = e.failedAt !== undefined && now - e.failedAt < this.o.failTtlMs;
      if (now - e.at >= ttl && !backoff) void this.refresh(src); // serve now, refresh behind it
      return this.result(e, e.failedAt !== undefined);
    }
    if (e && e.code === 0 && now - e.at < this.o.failTtlMs) return this.result(e, true);

    const fresh = await this.refresh(src);
    return fresh ? this.result(fresh, fresh.code === 0) : { code: 0, body: '', age: 0, stale: true };
  }

  private result(e: Entry, stale: boolean): Result {
    return { code: e.code, body: e.body, age: Math.max(0, Math.round((this.o.now() - e.at) / 1000)), stale };
  }

  private refresh(src: Src): Promise<Entry | null> {
    const running = this.inflight.get(src.url);
    if (running) return running;
    const p = this.load(src).finally(() => this.inflight.delete(src.url));
    this.inflight.set(src.url, p);
    return p;
  }

  private async load(src: Src): Promise<Entry | null> {
    const timeout = src.slow ? 25_000 : 12_000;
    let reason = '';
    for (let attempt = 0; attempt < 2; attempt++) {
      if (attempt > 0) await new Promise((r) => setTimeout(r, this.o.retryDelayMs));
      try {
        const res = await this.o.get(src.url, timeout);
        if (res.code === 404) return this.store(src.url, { code: 404, body: '', at: this.o.now() });
        if (res.code === 200) {
          const body = src.reduce ? src.reduce(res.body) : res.body.toString('utf8');
          if (body !== null) return this.store(src.url, { code: 200, body, at: this.o.now() });
          reason = 'unusable body';
        } else {
          reason = `HTTP ${res.code}`;
        }
      } catch (err) {
        reason = err instanceof Error ? err.message : String(err);
      }
    }
    console.error(`php-watch: ${src.url} -> ${reason}`);
    const old = this.cache.get(src.url);
    if (old && old.code !== 0) {
      old.failedAt = this.o.now();
      return old;
    }
    return this.store(src.url, { code: 0, body: '', at: this.o.now() });
  }

  private store(url: string, e: Entry): Entry {
    this.cache.delete(url);
    this.cache.set(url, e);
    while (this.cache.size > this.o.maxEntries) {
      const oldest = this.cache.keys().next().value as string;
      this.cache.delete(oldest);
    }
    return e;
  }
}
