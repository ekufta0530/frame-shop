import { describe, expect, it } from 'vitest';
import { tileFigures, withoutOutliers, yoyRows } from './business';
import { periodsFor } from './periods';
import type { Dashboard } from './types';

// Open month 2026-09 (through the 14th, a 30-day month). Last year's Sep 1-14 sold 400 of 1,000.
function dash(monthly: Record<string, { rev: number; v: number }>): Dashboard {
  return {
    current_month: '2026-09',
    business: {
      monthly, by_store: null, all_time_revenue: 0, first_day: '2024-08-01',
      partial_month: '2026-09', partial_day: 14, days_in_partial: 30,
      partial_ly_rev: 400, partial_ly_v: 4, partial_ly_rev_ex: 300, partial_ly_v_ex: 3,
      outlier: { label: 'x', monthly: { '2025-10': { rev: 500, v: 1 } }, months: ['2025-10'], total_rev: 500, total_v: 1, by_store: {} },
    },
    series: { repeat_revenue_share: { '2025-10': { v: 0.5, n: 1, d: 2, final: true } }, active_customers_ttm: {} },
    series_ex: { repeat_revenue_share: { '2025-10': { v: 0.25, n: 1, d: 4, final: true } } },
  } as unknown as Dashboard;
}
const months = (from: number, to: number, rev: number) =>
  Object.fromEntries(Array.from({ length: to - from + 1 }, (_, i) => {
    const t = 2025 * 12 + from - 1 + i;
    return [`${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, '0')}`, { rev, v: 1 }];
  }));

describe('periodsFor', () => {
  it('offers the open month and quarter first, then closed periods back into last year', () => {
    const ids = periodsFor('2026-02', '2024-08').map((p) => p.id);
    expect(ids.slice(0, 3)).toEqual(['m2026-02', 'q2026-1', 'm2026-01']);
    expect(ids).toContain('m2025-12');
    expect(ids).toContain('q2025-1');
  });
  it('leaves out periods that end before the first month of data', () => {
    const ids = periodsFor('2026-09', '2026-01').map((p) => p.id);
    expect(ids).not.toContain('m2025-12');
    expect(ids).toContain('m2026-01');
  });
});

describe('tileFigures', () => {
  const d = dash({ ...months(1, 12, 1000), '2026-08': { rev: 900, v: 1 }, '2026-09': { rev: 700, v: 7 } });
  const open = periodsFor('2026-09', '2024-08')[0];

  it('compares the open month with the same days last year and projects to month end', () => {
    const f = tileFigures(d, open);
    expect(f.rev).toBe(700);
    expect(f.lyRev).toBe(400); // Sep 1-14 last year, not the whole of last September
    expect(f.projected).toBeCloseTo((700 / 14) * 30);
  });
  it('trailing 12 months swaps the part-month in the prior window for the matching run of days', () => {
    const f = tileFigures(d, open);
    // the prior window (Oct '24 - Sep '25) holds nine data months of 1,000; Sep '25 becomes 400
    expect(f.ttmPrev).toBe(9 * 1000 - 1000 + 400);
  });
  it('a closed month has no projection', () => {
    const closed = periodsFor('2026-09', '2024-08').find((p) => p.id === 'm2026-08')!;
    expect(tileFigures(d, closed).projected).toBeNull();
  });
});

describe('yoyRows', () => {
  const d = dash({ ...months(1, 12, 1000), '2025-10': { rev: 1500, v: 2 }, '2026-09': { rev: 700, v: 7 } });
  it('uses last year\'s partial-month figure for the open month', () => {
    const { rows } = yoyRows(d, 'trailing');
    const open = rows.find((r) => r.cm === '2026-09')!;
    expect([open.pyR, open.pyV]).toEqual([400, 4]);
  });
  it('removing outliers subtracts them from both years and flags the affected rows', () => {
    const { rows } = yoyRows(withoutOutliers(d), 'trailing');
    const oct = rows.find((r) => r.cm === '2025-10')!;
    expect(oct.cyR).toBe(1000);
    expect(oct.aff).toBe(true);
    const open = rows.find((r) => r.cm === '2026-09')!;
    expect([open.pyR, open.pyV, open.aff]).toEqual([300, 3, false]);
  });
  it('the trailing total covers 12 months; the extra first row is reference only', () => {
    const { rows, totFrom } = yoyRows(d, 'trailing');
    expect(rows[0].ref).toBe(true);
    expect(rows.filter((r) => !r.ref)).toHaveLength(12);
    expect(totFrom).toBe('2025-10');
  });
});

describe('withoutOutliers', () => {
  const d = dash({ ...months(1, 12, 1000), '2025-10': { rev: 1500, v: 2 } });
  it('takes the outliers out of the tiles, the revenue series and the revenue-weighted KPI only', () => {
    const x = withoutOutliers(d);
    expect(x.business.monthly['2025-10']).toEqual({ rev: 1000, v: 1 });
    expect(x.series.repeat_revenue_share['2025-10'].v).toBe(0.25);
    expect(x.series.active_customers_ttm).toBe(d.series.active_customers_ttm);
    const open = periodsFor('2026-09', '2024-08')[0];
    expect(tileFigures(x, open).lyRev).toBe(300);
  });
  it('leaves a dashboard without outliers untouched', () => {
    const none = { ...d, business: { ...d.business, outlier: { ...d.business.outlier, total_v: 0 } } };
    expect(withoutOutliers(none)).toBe(none);
  });
});
