// The JSON the page reads from /api/status.

export type Level = 'ok' | 'bad' | 'warn' | 'none';
export type CveState = 'fixed' | 'vulnerable' | 'tracked' | 'unknown' | 'error' | 'limited';

export interface Row {
  package: string;
  release: string;
  version: string;
  status: string;
  level: Level;
}

export interface SourceData {
  description: string;
  rows: Row[];
}

export interface CveSource {
  name: string;
  url: string;
  status: 'found' | 'none' | 'error';
  rows: Row[];
}

export interface Cve {
  id: string;
  state: CveState;
  description: string;
  /** php.net releases whose changelog names this CVE. */
  upstream: { version: string; date: string }[];
  sources: CveSource[];
}

export interface Upstream {
  version: string;
  date: string;
  security: boolean;
}

export interface Cell {
  version: string;
  short: string;
  state: 'current' | 'behind' | 'none';
}

export interface MatrixRow {
  repo: string;
  label: string;
  cells: Record<string, Cell | null>;
}

export interface DebianPackage {
  name: string;
  release: string;
  tracked: boolean;
  current: string | null;
  pending: string | null;
  open: { id: string; description: string }[];
  versions: { suite: string; version: string }[];
}

export interface Status {
  generated: number;
  age: number;
  stale: boolean;
  ttl: number;
  unreachable: string[];
  upstream: Record<string, Upstream>;
  matrix: MatrixRow[];
  cves: Cve[];
  packages: DebianPackage[];
}
