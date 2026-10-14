// Number, money and month formatting shared by every page.

export const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
  'September', 'October', 'November', 'December'];

/** "2026-09" -> "Sep ’26" */
export const ml = (m: string) => MONTHS[+m.slice(5, 7) - 1].slice(0, 3) + ' ’' + m.slice(2, 4);
/** "2026-09" -> "Sep 2026" */
export const mlong = (m: string) => MONTHS[+m.slice(5, 7) - 1].slice(0, 3) + ' ' + m.slice(0, 4);
export const mname = (m: string) => MONTHS[+m.slice(5, 7) - 1];
/** "2026-09-14" -> "Sep 14, 2026" */
export const dlong = (d: string) => MONTHS[+d.slice(5, 7) - 1].slice(0, 3) + ' ' + +d.slice(8, 10) + ', ' + d.slice(0, 4);

export function addM(m: string, n: number): string {
  const t = +m.slice(0, 4) * 12 + +m.slice(5, 7) - 1 + n;
  return Math.floor(t / 12) + '-' + String((t % 12) + 1).padStart(2, '0');
}

export function monthRange(from: string, to: string): string[] {
  const out: string[] = [];
  for (let m = from; m <= to; m = addM(m, 1)) out.push(m);
  return out;
}

export type Unit = 'pct' | 'days' | 'int';

export function fmtUnit(v: number | null | undefined, u: Unit): string {
  if (v == null || Number.isNaN(v)) return '—';
  if (u === 'pct') return (v * 100).toFixed(1) + '%';
  if (u === 'days') return Math.round(v) + 'd';
  return Math.round(v).toLocaleString();
}

/** Compact money: $1.24M, $412k, $8.3k, $640 */
export function usd(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  const a = Math.abs(v), sg = v < 0 ? '−' : '';
  if (a >= 1e6) return sg + '$' + (a / 1e6).toFixed(2) + 'M';
  if (a >= 1e5) return sg + '$' + Math.round(a / 1000) + 'k';
  if (a >= 1e3) return sg + '$' + (a / 1000).toFixed(1) + 'k';
  return sg + '$' + Math.round(a);
}

export function usdFull(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return (v < 0 ? '−$' : '$') + Math.round(Math.abs(v)).toLocaleString();
}

export const rel = (cur: number | null | undefined, base: number | null | undefined) =>
  cur != null && base != null && base > 0 ? cur / base - 1 : null;

export function pctTxt(d: number | null): string {
  if (d == null) return '—';
  const p = Math.abs(d * 100);
  return p < 0.5 ? 'flat' : (d > 0 ? '▲ ' : '▼ ') + p.toFixed(0) + '%';
}

/** Class for a change where up is good. */
export const relCls = (d: number | null) => (d == null || Math.abs(d) < 0.005 ? '' : d > 0 ? 'good' : 'watch');

// Report-builder formats (semantic.py metric formats).
export function fmtValue(v: unknown, format?: string): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v !== 'number') return String(v);
  switch (format) {
    case 'money': return (v < 0 ? '−$' : '$') + Math.abs(v).toLocaleString(undefined,
      Math.abs(v) >= 1000 ? { maximumFractionDigits: 0 } : { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    case 'pct': return v.toFixed(1) + '%';
    case 'num1': return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
    case 'int': return Math.round(v).toLocaleString();
    default: return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
}

export function fmtShort(v: unknown, format?: string): string {
  if (typeof v !== 'number') return String(v ?? '');
  if (format === 'pct') return v.toFixed(0) + '%';
  const a = Math.abs(v);
  const s = a >= 1e6 ? (a / 1e6).toFixed(1) + 'M' : a >= 1e3 ? (a / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'k' : String(Math.round(a * 10) / 10);
  return (v < 0 ? '−' : '') + (format === 'money' ? '$' : '') + s;
}

/** Ordinal/cyclic dimension values carry a sort prefix ("01 Jan", "2 $100-249") hidden for display. */
export function dimText(v: unknown, kind?: string): string {
  if (v === null || v === undefined || v === '') return '(blank)';
  if ((kind === 'cyclic' || kind === 'ordinal') && typeof v === 'string') return v.replace(/^\d+ /, '');
  return String(v);
}
