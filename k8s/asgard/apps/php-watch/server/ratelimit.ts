// Two small limiters that keep a public page from being turned into a way to hammer
// other people's servers or this pod.

/** At most `limit` hits per `windowMs` per key (a client address). */
export class WindowLimiter {
  private hits = new Map<string, number[]>();
  private limit: number;
  private windowMs: number;
  private now: () => number;

  constructor(limit: number, windowMs: number, now: () => number = Date.now) {
    this.limit = limit;
    this.windowMs = windowMs;
    this.now = now;
  }

  /** True if this hit is allowed (and records it). */
  allow(key: string): boolean {
    const t = this.now();
    const recent = (this.hits.get(key) ?? []).filter((x) => t - x < this.windowMs);
    if (recent.length >= this.limit) {
      this.hits.set(key, recent);
      return false;
    }
    recent.push(t);
    this.hits.set(key, recent);
    return true;
  }

  /** Forget keys with no recent hits, so the map cannot grow without bound. */
  prune(): void {
    const t = this.now();
    for (const [k, v] of this.hits) if (!v.some((x) => t - x < this.windowMs)) this.hits.delete(k);
  }
}

/** A token bucket: `capacity` tokens, refilled evenly over `perMs`. */
export class Budget {
  private tokens: number;
  private last: number;
  private capacity: number;
  private perMs: number;
  private now: () => number;

  constructor(capacity: number, perMs: number, now: () => number = Date.now) {
    this.capacity = capacity;
    this.perMs = perMs;
    this.now = now;
    this.tokens = capacity;
    this.last = now();
  }

  take(): boolean {
    const t = this.now();
    this.tokens = Math.min(this.capacity, this.tokens + ((t - this.last) / this.perMs) * this.capacity);
    this.last = t;
    if (this.tokens < 1) return false;
    this.tokens -= 1;
    return true;
  }
}
