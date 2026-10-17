import type { Location } from "./types";

export function fmtNumber(n: number | null | undefined): string {
  return n == null ? "—" : n.toLocaleString("en-US");
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}

export function fmtLocation(loc: Location | null | undefined): string {
  if (!loc?.file_path) return "—";
  if (loc.start_line == null) return loc.file_path;
  return loc.end_line != null && loc.end_line !== loc.start_line
    ? `${loc.file_path}:${loc.start_line}–${loc.end_line}`
    : `${loc.file_path}:${loc.start_line}`;
}

/** Relative "3 h ago" for ISO timestamps; falls back to the raw value. */
export function fmtWhen(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const s = Math.round((now - t) / 1000);
  if (s < 0) return new Date(t).toLocaleString();
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return new Date(t).toLocaleDateString();
}

export function sum(values: Iterable<number>): number {
  let s = 0;
  for (const v of values) s += v;
  return s;
}

export function pct(part: number, total: number): number {
  return total > 0 ? Math.round((part / total) * 100) : 0;
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
