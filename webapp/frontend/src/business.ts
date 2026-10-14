// The dashboard's revenue arithmetic: period totals, year-over-year and trailing 12 months.
// Kept free of React so it can be tested; the components only format what this returns.
import { addM, monthRange } from './format';
import { periodMonths, type Period } from './periods';
import type { Dashboard } from './types';

export interface TileFigures {
  months: string[]; hasPartial: boolean;
  rev: number; orders: number; lyRev: number; lyOrders: number; prevRev: number;
  ttm: number; ttmPrev: number; projected: number | null;
}

/** Totals for the picked period. In the open month, "last year" is the same run of days only. */
export function tileFigures(d: Dashboard, p: Period): TileFigures {
  const B = d.business, M = B.monthly, PARTIAL = B.partial_month;
  const rev = (m: string) => M[m]?.rev ?? 0;
  const vis = (m: string) => M[m]?.v ?? 0;
  const months = periodMonths(p, d.current_month);
  const hasPartial = months.includes(PARTIAL);
  const step = p.kind === 'month' ? 1 : 3;
  const sum = (f: (m: string) => number) => months.reduce((a, m) => a + f(m), 0);
  const total = sum(rev);
  let ttm = 0, ttmPrev = 0;
  for (let i = 0; i < 12; i++) ttm += rev(addM(p.asOf, -i));
  for (let i = 12; i < 24; i++) ttmPrev += rev(addM(p.asOf, -i));
  if (hasPartial) ttmPrev = ttmPrev - rev(addM(p.asOf, -12)) + B.partial_ly_rev;
  return {
    months, hasPartial,
    rev: total, orders: sum(vis),
    lyRev: sum((m) => (m === PARTIAL ? B.partial_ly_rev : rev(addM(m, -12)))),
    lyOrders: sum((m) => (m === PARTIAL ? B.partial_ly_v : vis(addM(m, -12)))),
    prevRev: sum((m) => rev(addM(m, -step))),
    ttm, ttmPrev,
    projected: hasPartial && B.partial_day > 0
      ? total - rev(PARTIAL) + (rev(PARTIAL) / B.partial_day) * B.days_in_partial : null,
  };
}

/** The dashboard with the outliers taken out of every figure, so each chart can render it as is.
 *  Revenue and order counts lose them; the revenue-weighted retention KPI is swapped for the
 *  server's outlier-free series; customer counts stay (the customer still visited). */
export function withoutOutliers(d: Dashboard): Dashboard {
  const B = d.business, O = B.outlier;
  if (!O.total_v) return d;
  const monthly = Object.fromEntries(Object.entries(B.monthly).map(([m, x]) => [m, {
    rev: x.rev - (O.monthly[m]?.rev ?? 0), v: x.v - (O.monthly[m]?.v ?? 0),
  }]));
  const by_store = B.by_store && Object.fromEntries(Object.entries(B.by_store).map(([sid, row]) => [sid,
    Object.fromEntries(Object.entries(row).map(([m, rev]) => [m, rev - (O.by_store[sid]?.[m] ?? 0)]))]));
  return {
    ...d,
    series: { ...d.series, ...d.series_ex },
    business: {
      ...B, monthly, by_store, excluded: true,
      all_time_revenue: B.all_time_revenue == null ? null : B.all_time_revenue - O.total_rev,
      partial_ly_rev: B.partial_ly_rev_ex, partial_ly_v: B.partial_ly_v_ex,
    },
  };
}

export interface YoYRow {
  cm: string; part: boolean; cyR: number; cyV: number; pyR: number; pyV: number; aff: boolean; ref: boolean;
}
export interface YoYTotals { cyR: number; cyV: number; pyR: number; pyV: number; part: boolean }

/** One row per month against the same month a year earlier, plus the total over the window.
 *  Months whose figures lost an outlier (``withoutOutliers``) are flagged ``aff``. */
export function yoyRows(d: Dashboard, mode: 'calendar' | 'trailing') {
  const B = d.business, M = B.monthly, CUR = d.current_month, PARTIAL = B.partial_month;
  const isOut = (m: string) => !!B.excluded && B.outlier.months.includes(m);
  const r = (m: string) => M[m]?.rev ?? 0;
  const v = (m: string) => M[m]?.v ?? 0;
  const start = mode === 'calendar' ? CUR.slice(0, 4) + '-01' : addM(CUR, -12);
  const totFrom = mode === 'calendar' ? start : addM(CUR, -11);
  const totals: YoYTotals = { cyR: 0, cyV: 0, pyR: 0, pyV: 0, part: false };
  const rows: YoYRow[] = monthRange(start, CUR).map((cm) => {
    const pm = addM(cm, -12), part = cm === PARTIAL;
    const row = {
      cm, part, cyR: r(cm), cyV: v(cm),
      pyR: part ? B.partial_ly_rev : r(pm),
      pyV: part ? B.partial_ly_v : v(pm),
      aff: isOut(cm) || isOut(pm), ref: cm < totFrom,
    };
    if (cm >= totFrom) { totals.cyR += row.cyR; totals.cyV += row.cyV; totals.pyR += row.pyR; totals.pyV += row.pyV; }
    return row;
  });
  return { rows, totals, totFrom };
}
