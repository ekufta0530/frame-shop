// Report periods for the dashboard's picker. Pure, so they can be tested without a DOM.
import { mname } from './format';

export interface Period {
  id: string; kind: 'month' | 'quarter'; asOf: string; q?: number; year: number; open?: boolean; label: string;
}

export const qOf = (m: string) => Math.ceil(+m.slice(5, 7) / 3);
export const qMonths = (year: number, q: number) =>
  [0, 1, 2].map((i) => `${year}-${String((q - 1) * 3 + 1 + i).padStart(2, '0')}`);

const monthId = (year: number, mo: number) => `${year}-${String(mo).padStart(2, '0')}`;

/**
 * The open month and quarter, then every closed quarter and month of the current year and of the
 * year before it. Periods that end before the store's first month (``firstMonth``) are left out.
 */
export function periodsFor(cur: string, firstMonth: string): Period[] {
  const year = +cur.slice(0, 4), cq = qOf(cur);
  const out: Period[] = [
    { id: 'm' + cur, kind: 'month', asOf: cur, year, open: true, label: `${mname(cur)} ${year}` },
    { id: `q${year}-${cq}`, kind: 'quarter', q: cq, asOf: cur, year, open: true, label: `Q${cq} ${year}` },
  ];
  const closed = (y: number, lastQ: number, lastMonth: number) => {
    for (let q = lastQ; q >= 1; q--) {
      out.push({ id: `q${y}-${q}`, kind: 'quarter', q, year: y, asOf: qMonths(y, q)[2], label: `Q${q} ${y}` });
    }
    for (let mo = lastMonth; mo >= 1; mo--) {
      const m = monthId(y, mo);
      out.push({ id: 'm' + m, kind: 'month', asOf: m, year: y, label: `${mname(m)} ${y}` });
    }
  };
  closed(year, cq - 1, +cur.slice(5, 7) - 1);
  closed(year - 1, 4, 12);
  return out.filter((p) => p.open || p.asOf >= firstMonth);
}

export function periodMonths(p: Period, cur: string): string[] {
  return p.kind === 'month' ? [p.asOf] : qMonths(p.year, p.q!).filter((m) => m <= cur);
}
