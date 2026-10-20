// The "cloud" dashboard every store gets -- and the all-stores view, which is the pooled
// warehouse rendered the same way. Everything below is computed client-side from one
// /api/stores/<id>/dashboard payload, so switching period never refetches.
import { useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Card, MonthlyBars, Spark, TrendChart, storeColor, useColors, type BarSeries } from '../charts';
import {
  addM, dlong, fmtUnit, ml, mlong, mname, monthRange, pctTxt, rel, relCls, usd, usdFull, MONTHS,
} from '../format';
import { tileFigures, withoutOutliers, yoyRows, type YoYTotals } from '../business';
import { useExcludeOutliers } from '../outliers';
import { periodMonths, periodsFor, qMonths, type Period } from '../periods';
import { renderMethod } from '../richtext';
import type { Dashboard, KpiMeta, StoreInfo } from '../types';

export default function StoreDashboard({ d: raw, stores }: { d: Dashboard; stores: StoreInfo[] }) {
  const [params, setParams] = useSearchParams();
  // the page-wide "Exclude outliers" setting: every tile, table and chart below renders this copy
  const [exOn] = useExcludeOutliers();
  const d = useMemo(() => (exOn ? withoutOutliers(raw) : raw), [raw, exOn]);
  const O = d.business.outlier;
  const periods = useMemo(() => periodsFor(d.current_month, d.store.chart_from), [d.current_month, d.store.chart_from]);
  const p = periods.find((x) => x.id === params.get('p')) || periods[0];
  const snapshot = d.store.tier === 'on-prem' || d.store.tier === 'all';
  const openWord = snapshot ? 'latest' : 'live';

  const pick = (id: string) => {
    const next = new URLSearchParams(params);
    if (id === periods[0].id) next.delete('p'); else next.set('p', id);
    setParams(next, { replace: true });
  };

  return (
    <>
      <div className="toolbar">
        <label className="lbl" htmlFor="period">Report period</label>
        <select id="period" value={p.id} onChange={(e) => pick(e.target.value)}>
          {periods.map((x) => <option key={x.id} value={x.id}>{x.label}{x.open ? `  —  ${openWord}` : ''}</option>)}
        </select>
        <span className="muted">
          {p.open
            ? snapshot
              ? <>Data through {dlong(d.store.data_through)}</>
              : <><span className="badge-live"><span className="dot" />Live</span> · data through {dlong(d.store.data_through)}</>
            : <>Closed report · figures frozen at {mname(p.kind === 'quarter' ? qMonths(p.year, p.q!)[2] : p.asOf)} {p.year}</>}
        </span>
      </div>
      {d.business.excluded && (
        <p className="ex-note">
          <b>Outliers excluded:</b> the {O.label} ({usdFull(O.total_rev)}, {O.months.map(ml).join(' & ')}) is left out of
          every figure and chart below. Customer counts are unchanged — the customer still visited.
        </p>
      )}

      <div className="section-label">The business</div>
      <BusinessTiles d={d} p={p} />
      <div className="grid" style={{ marginTop: 12 }}>
        <YoYTable d={d} />
        <RevenueChart d={d} p={p} stores={stores} />
      </div>

      <div className="section-label">Customer retention</div>
      <RetentionTiles d={d} p={p} openWord={openWord} />
      <div className="grid" style={{ marginTop: 12 }}>
        {d.meta.map((meta) => <KpiChart key={meta.key} d={d} meta={meta} p={p} />)}
        <NewCustomers d={d} p={p} />
      </div>

      <section className="card method" style={{ marginTop: 22 }}>
        <div className="card-head"><h2>How these are built</h2></div>
        <ul>{d.store.method.map((m, i) => <li key={i}>{renderMethod(m)}</li>)}</ul>
        <details className="more">
          <summary>Full monthly KPI data</summary>
          <KpiTable d={d} />
        </details>
      </section>
      <p className="colophon">
        {d.summary.customers.toLocaleString()} customers · {d.summary.visits.toLocaleString()} visits
        {d.summary.work_orders ? ` · ${d.summary.work_orders.toLocaleString()} work orders` : ''} ·
        {d.summary.first_day && d.summary.last_day
          ? <>history {mlong(d.summary.first_day.slice(0, 7))} – {mlong(d.summary.last_day.slice(0, 7))} · </> : null}calc v{d.calc_version}
      </p>
    </>
  );
}

// ---- business tiles ---------------------------------------------------------------------

function BusinessTiles({ d, p }: { d: Dashboard; p: Period }) {
  const B = d.business;
  const f = tileFigures(d, p);
  if (!f.months.length || !B.first_day) return <p className="muted">No sales on record yet.</p>;
  const prevMonth = addM(p.asOf, -1), lastYear = addM(p.asOf, -12);
  const yr = +f.months[0].slice(0, 4);
  const lab = p.kind === 'month'
    ? { title: `${mname(p.asOf)} ${p.asOf.slice(0, 4)}`, mom: 'vs ' + mlong(prevMonth), yoy: 'vs ' + mlong(lastYear) }
    : { title: `Q${p.q} ${yr}`, mom: `vs Q${p.q! > 1 ? p.q! - 1 : 4} ${p.q! > 1 ? yr : yr - 1}`, yoy: `vs Q${p.q} ${yr - 1}` };
  const mDay = `${MONTHS[+B.partial_month.slice(5, 7) - 1].slice(0, 3)} ${B.partial_day}`;
  const cur = d.current_month;
  const tracked = (+cur.slice(0, 4) - +B.first_day.slice(0, 4)) * 12 + (+cur.slice(5, 7) - +B.first_day.slice(5, 7)) + 1;
  const { rev, orders, lyRev, lyOrders, prevRev, ttm, ttmPrev, projected, hasPartial } = f;

  return (
    <div className="tiles">
      <div className="tile">
        <p className="eyebrow">{lab.title}{hasPartial ? ' so far' : ''}</p>
        <p className="value">{usd(rev)}</p>
        <p className="delta">
          {hasPartial
            ? <span>through {mDay}{projected != null ? ` · on pace for ~${usd(projected)}` : ''}</span>
            : <span className={relCls(rel(rev, prevRev))}>{pctTxt(rel(rev, prevRev))} {lab.mom}</span>}
          <span className={relCls(rel(rev, lyRev))}>{pctTxt(rel(rev, lyRev))} {lab.yoy}</span>
        </p>
      </div>
      <div className="tile">
        <p className="eyebrow">Trailing 12 months</p>
        <p className="value">{usd(ttm)}</p>
        <p className="delta"><span>to {mlong(p.asOf)}</span>
          <span className={relCls(rel(ttm, ttmPrev))}>{pctTxt(rel(ttm, ttmPrev))} vs prior 12 mo</span></p>
      </div>
      <div className="tile">
        <p className="eyebrow">Orders {hasPartial ? 'so far' : 'in period'}</p>
        <p className="value">{orders.toLocaleString()}</p>
        <p className="delta"><span>{usd(orders ? rev / orders : null)} avg ticket</span>
          <span className={relCls(rel(orders, lyOrders))}>{pctTxt(rel(orders, lyOrders))} {lab.yoy}</span></p>
      </div>
      <div className="tile">
        <p className="eyebrow">Revenue to date</p>
        <p className="value">{usd(B.all_time_revenue)}</p>
        <p className="delta"><span>since {mlong(B.first_day.slice(0, 7))}</span><span>{tracked} months on record</span></p>
      </div>
    </div>
  );
}

// ---- monthly revenue ---------------------------------------------------------------------

function RevenueChart({ d, p, stores }: { d: Dashboard; p: Period; stores: StoreInfo[] }) {
  const c = useColors();
  const B = d.business;
  const all = monthRange(d.store.chart_from, d.current_month);
  const long = all.length > 30;
  const [span, setSpan] = useState<'2y' | '5y' | 'all'>('2y');
  const months = !long || span === 'all' ? all : all.slice(-(span === '2y' ? 25 : 61));
  const series: BarSeries[] = B.by_store
    ? stores.filter((s) => B.by_store![s.id]).map((s) => ({ key: s.id, label: s.name, color: storeColor(c, s.slot), values: B.by_store![s.id] }))
    : [{ key: 'rev', label: 'Revenue', color: c.accent, values: Object.fromEntries(Object.entries(B.monthly).map(([m, x]) => [m, x.rev])) }];
  return (
    <Card title="Monthly revenue" wide
      blurb={<>Pre-tax, after discounts, voids removed. The dashed line is the 3-month average; the faded bar is the open month{B.by_store ? '; stacked by store' : ''}.</>}
      right={long && (
        <div className="seg" role="group" aria-label="Range">
          {(['2y', '5y', 'all'] as const).map((s) => (
            <button key={s} type="button" aria-pressed={span === s} onClick={() => setSpan(s)}>{s === 'all' ? 'All' : s.toUpperCase()}</button>
          ))}
        </div>
      )}>
      <MonthlyBars months={months} series={series} partial={B.partial_month} avg legend
        muteOutside={p.kind === 'quarter' ? periodMonths(p, d.current_month) : undefined} />
    </Card>
  );
}

// ---- year over year table ------------------------------------------------------------------

function YoYTable({ d }: { d: Dashboard }) {
  const [mode, setMode] = useState<'calendar' | 'trailing'>('trailing');
  const B = d.business, OM = B.outlier.monthly, CUR = d.current_month, PARTIAL = B.partial_month;
  const ex = !!B.excluded;
  const exMonths = B.outlier.months;
  const { rows, totals: tot, totFrom } = yoyRows(d, mode);
  const anyAff = rows.some((x) => x.aff);
  const avg = (rev: number, n: number) => (n > 0 ? rev / n : null);

  const dcell = (x: number | null) => <td className={'num ' + relCls(x)}>{pctTxt(x)}</td>;
  const line = (key: string, label: ReactNode, x: Omit<YoYTotals, 'part'> & { part?: boolean }, cls: string) => {
    const pn = x.part ? <span className="pypart"> (1–{B.partial_day})</span> : null;
    return (
      <tr key={key} className={cls}>
        <th scope="row">{label}</th>
        <td className="num">{usdFull(x.pyR)}{pn}</td><td className="num">{usdFull(x.cyR)}</td>{dcell(rel(x.cyR, x.pyR))}
        <td className="num">{x.pyV.toLocaleString()}{pn}</td><td className="num">{x.cyV.toLocaleString()}</td>{dcell(rel(x.cyV, x.pyV))}
        <td className="num">{usdFull(avg(x.cyR, x.cyV))}</td>{dcell(rel(avg(x.cyR, x.cyV), avg(x.pyR, x.pyV)))}
      </tr>
    );
  };

  const notes: string[] = [];
  if (rows.some((x) => x.ref)) notes.push(`${ml(rows[0].cm)} is shown for reference (the same month a year back); the total covers ${ml(totFrom)}–${ml(CUR)}.`);
  if (rows.some((x) => x.part)) notes.push(`MTD: through ${mname(PARTIAL).slice(0, 3)} ${B.partial_day}, against ${mname(PARTIAL).slice(0, 3)} 1–${B.partial_day}, ${addM(PARTIAL, -12).slice(0, 4)}.`);
  if (ex && anyAff) notes.push(exMonths.map((m) => `−${usdFull(OM[m]?.rev ?? 0)} (${ml(m)})`).join(', ') + ' removed.');
  else if (ex) notes.push(`The ${B.outlier.label} falls in ${exMonths.map(ml).join(' & ')}, outside this view.`);

  return (
    <Card title="Monthly performance vs. last year" wide
      blurb="Each month against the same month a year earlier. The open month is month-to-date against the same run of days last year."
      right={
        <div className="seg" role="group" aria-label="Range">
          <button type="button" aria-pressed={mode === 'calendar'} onClick={() => setMode('calendar')}>Calendar year</button>
          <button type="button" aria-pressed={mode === 'trailing'} onClick={() => setMode('trailing')}>Trailing 12 months</button>
        </div>
      }
      foot={notes.length ? notes.join(' ') : undefined}>
      <div className="tablewrap">
        <table className="data">
          <thead>
            <tr><th /><th colSpan={3} className="group">Revenue</th><th colSpan={3} className="group">Orders</th><th colSpan={2} className="group">Avg ticket</th></tr>
            <tr><th>Month</th><th className="num">Last yr</th><th className="num">This yr</th><th className="num">Δ</th>
              <th className="num">Last yr</th><th className="num">This yr</th><th className="num">Δ</th><th className="num">This yr</th><th className="num">Δ</th></tr>
          </thead>
          <tbody>
            {rows.map((x) => line(x.cm, <>
              {MONTHS[+x.cm.slice(5, 7) - 1].slice(0, 3)}{mode === 'trailing' ? ' ’' + x.cm.slice(2, 4) : ''}
              {x.part && <span className="mtd">MTD</span>}
              {x.aff && <span className="exmark">outlier removed</span>}
            </>, x, [x.part && 'live', x.aff && 'aff', x.ref && 'ref'].filter(Boolean).join(' ')))}
            {line('tot', mode === 'calendar' ? `${CUR.slice(0, 4)} YTD` : 'Trailing 12 mo', tot, 'tot')}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ---- retention -------------------------------------------------------------------------------

function kpiPoints(d: Dashboard, meta: KpiMeta, asOf: string) {
  const ser = d.series[meta.key] || {};
  return Object.keys(ser).filter((m) => m >= meta.plot_from && m <= asOf && ser[m]?.v != null).sort()
    .map((m) => ({ m, v: ser[m].v as number, final: ser[m].final }));
}

function RetentionTiles({ d, p, openWord }: { d: Dashboard; p: Period; openWord: string }) {
  const c = useColors();
  const cmp = addM(p.asOf, -12);
  return (
    <div className="tiles">
      {d.meta.map((meta) => {
        const ser = d.series[meta.key] || {};
        const head = ser[p.asOf];
        const cur = head?.v ?? null;
        let delta: ReactNode = ' ', cls = '';
        const base = ser[cmp]?.v;
        if (cur != null && base != null) {
          const dv = cur - base;
          const tiny = meta.unit === 'pct' ? 0.0005 : 0.5;
          if (Math.abs(dv) < tiny) delta = <>no change <span className="muted">vs {ml(cmp)}</span></>;
          else {
            const up = dv > 0, good = (up && meta.good === 'up') || (!up && meta.good === 'down');
            cls = good ? 'good' : 'watch';
            delta = <>{up ? '▲' : '▼'} {fmtUnit(Math.abs(dv), meta.unit)} <span className="muted">vs {ml(cmp)}</span></>;
          }
        }
        return (
          <div className="tile" key={meta.key}>
            <p className="eyebrow">{meta.tile}</p>
            <p className="value">{fmtUnit(cur, meta.unit)}</p>
            <p className={'delta ' + cls}><span>{delta}</span></p>
            <Spark points={kpiPoints(d, meta, p.asOf)} color={c.accent} />
            <p className="asof">{head ? `${ml(p.asOf)} · ${head.final ? 'final' : openWord}` : 'no data for this month'}</p>
          </div>
        );
      })}
    </div>
  );
}

function KpiChart({ d, meta, p }: { d: Dashboard; meta: KpiMeta; p: Period }) {
  const pts = kpiPoints(d, meta, p.asOf);
  const head = d.series[meta.key]?.[p.asOf];
  const band = p.kind === 'quarter' ? qMonths(p.year, p.q!).filter((m) => pts.some((x) => x.m === m)) : null;
  const pool = meta.key === 'reactivation_rate' && head?.d != null ? Math.round(head.d) : null;
  return (
    <Card title={meta.label} blurb={meta.short} info={meta.long}
      foot={<>
        {meta.baseline != null ? `Dashed line: first-pass baseline, ${fmtUnit(meta.baseline, meta.unit)}. ` : ''}
        {pool != null ? `Lapsed pool this month: ${pool.toLocaleString()} customers. ` : ''}
        {head && !head.final ? `${mname(p.asOf)} is still open.` : ''}
      </>}>
      {pts.length ? <TrendChart meta={meta} points={pts} band={band} /> : <p className="muted">Not enough history yet.</p>}
    </Card>
  );
}

function NewCustomers({ d, p }: { d: Dashboard; p: Period }) {
  const c = useColors();
  const data = d.cohorts.filter((x) => x.m >= d.store.chart_from && x.m <= p.asOf && x.m < d.current_month);
  const months = data.map((x) => x.m).slice(-61);
  return (
    <Card title="New customers per month" wide blurb="First-ever visits — the cohort sizes behind the rates above.">
      <MonthlyBars months={months} money={false} height={190}
        series={[{ key: 'n', label: 'New customers', color: c.accent, values: Object.fromEntries(data.map((x) => [x.m, x.n])) }]} />
    </Card>
  );
}

function KpiTable({ d }: { d: Dashboard }) {
  const months = [...new Set(Object.values(d.series).flatMap((s) => Object.keys(s)))].sort().reverse();
  return (
    <div className="tablewrap" style={{ maxHeight: 420, overflowY: 'auto', marginTop: 8 }}>
      <table className="data">
        <thead><tr><th>Month</th>{d.meta.map((m) => <th key={m.key} className="num">{m.label}</th>)}</tr></thead>
        <tbody>
          {months.map((mo) => (
            <tr key={mo}>
              <th scope="row">{ml(mo)}</th>
              {d.meta.map((m) => {
                const r = d.series[m.key]?.[mo];
                return <td key={m.key} className="num">{r?.v != null ? fmtUnit(r.v, m.unit) + (r.final ? '' : ' *') : '—'}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="foot muted">* month not yet final</p>
    </div>
  );
}
