// Version helpers. Debian, Sury and Docker all carry the PHP version in different
// wrappers ("8.4.27-1+0~2026…+debian13~1.gbp7636b9", "8.2.34-1~deb12u1", "8.4.27"),
// so comparison between repos is done on the upstream part.

/** "8.4.27-1+deb13u1", "1:8.4.27-r0", "8.4.27" -> "8.4.27". */
export function upstreamOf(v: string): string | null {
  const m = /^(?:\d+:)?(\d+\.\d+\.\d+)/.exec(v);
  return m ? m[1] : null;
}

/** Plain dotted-number comparison: 8.4.9 < 8.4.27. */
export function cmpNumeric(a: string, b: string): number {
  const pa = a.split('.').map(Number);
  const pb = b.split('.').map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d !== 0) return d < 0 ? -1 : 1;
  }
  return 0;
}

const isDigit = (c: string | undefined): boolean => c !== undefined && c >= '0' && c <= '9';

function order(c: string | undefined): number {
  if (c === undefined || c === '' || isDigit(c)) return 0;
  if (c === '~') return -1;
  if (/[A-Za-z]/.test(c)) return c.charCodeAt(0);
  return c.charCodeAt(0) + 256;
}

function verrevcmp(a: string, b: string): number {
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    while ((i < a.length && !isDigit(a[i])) || (j < b.length && !isDigit(b[j]))) {
      const d = order(a[i]) - order(b[j]);
      if (d !== 0) return d;
      i++;
      j++;
    }
    while (a[i] === '0') i++;
    while (b[j] === '0') j++;
    let firstDiff = 0;
    while (isDigit(a[i]) && isDigit(b[j])) {
      if (firstDiff === 0) firstDiff = a.charCodeAt(i) - b.charCodeAt(j);
      i++;
      j++;
    }
    if (isDigit(a[i])) return 1;
    if (isDigit(b[j])) return -1;
    if (firstDiff !== 0) return firstDiff;
  }
  return 0;
}

/** Debian version order (dpkg): epoch, then upstream, then revision, with "~" sorting before everything. */
export function debCompare(a: string, b: string): number {
  const split = (v: string) => {
    const e = /^(\d+):(.*)$/.exec(v);
    const rest = e ? e[2] : v;
    const dash = rest.lastIndexOf('-');
    return { epoch: e ? Number(e[1]) : 0, up: dash < 0 ? rest : rest.slice(0, dash), rev: dash < 0 ? '' : rest.slice(dash + 1) };
  };
  const x = split(a);
  const y = split(b);
  if (x.epoch !== y.epoch) return x.epoch < y.epoch ? -1 : 1;
  const u = verrevcmp(x.up, y.up);
  if (u !== 0) return u < 0 ? -1 : 1;
  const r = verrevcmp(x.rev, y.rev);
  return r === 0 ? 0 : r < 0 ? -1 : 1;
}
