// A deliberately small HTML table reader for the Debian security tracker pages. They are
// machine-generated and flat (h2 headings, plain tables), so a few regexes are enough and
// the pod needs no HTML-parser dependency.

export interface Cell {
  text: string;
  /** Text of the first link in the cell, if any. */
  link: string;
}

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };

export function decode(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, e: string) => {
    if (e[0] === '#') {
      const code = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code < 0x110000 ? String.fromCodePoint(code) : whole;
    }
    return ENTITIES[e.toLowerCase()] ?? whole;
  });
}

/** Tags removed, entities decoded, whitespace collapsed. */
export function clean(s: string): string {
  return decode(s.replace(/<[^>]*>/g, ' ')).replace(/\s+/g, ' ').trim();
}

function rowsOf(tableHtml: string): Cell[][] {
  const rows: Cell[][] = [];
  for (const tr of tableHtml.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)) {
    const cells: Cell[] = [];
    let hasTd = false;
    for (const c of tr[1].matchAll(/<(td|th)[^>]*>([\s\S]*?)<\/\1>/gi)) {
      if (c[1].toLowerCase() === 'td') hasTd = true;
      const a = /<a\b[^>]*>([\s\S]*?)<\/a>/i.exec(c[2]);
      cells.push({ text: clean(c[2]), link: a ? clean(a[1]) : '' });
    }
    if (hasTd) rows.push(cells);
  }
  return rows;
}

/** Rows (td rows only) of the first table after the first <h2> whose text contains `heading`. */
export function tableAfter(html: string, heading: string): Cell[][] {
  for (const h of html.matchAll(/<h2[^>]*>([\s\S]*?)<\/h2>/gi)) {
    if (!clean(h[1]).includes(heading)) continue;
    const from = (h.index ?? 0) + h[0].length;
    const t = /<table[^>]*>([\s\S]*?)<\/table>/i.exec(html.slice(from));
    return t ? rowsOf(t[1]) : [];
  }
  return [];
}

/** Rows of the first table on the page. */
export function firstTable(html: string): Cell[][] {
  const t = /<table[^>]*>([\s\S]*?)<\/table>/i.exec(html);
  return t ? rowsOf(t[1]) : [];
}
