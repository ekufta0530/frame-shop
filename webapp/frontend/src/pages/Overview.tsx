// Home: every store side by side on the same closed period, then the pooled all-stores dashboard.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../api';
import { Card, MonthlyBars, MultiLine, storeColor, useColors, type BarSeries } from '../charts';
import { OutlierToggle, ViewTabs } from '../controls';
import { dlong, fmtUnit, mlong, monthRange, pctTxt, rel, relCls, usd, usdFull } from '../format';
import { useExcludeOutliers } from '../outliers';
import type { Dashboard, Overview as OverviewData, OverviewRow, PeriodStats, StoreInfo } from '../types';
import ReportBuilder from './ReportBuilder';
import StoreDashboard from './StoreDashboard';

type PeriodKey = 'month' | 'ytd' | 'ttm';

export default function Overview({ stores, tab }: { stores: StoreInfo[]; tab: 'dashboard' | 'reports' }) {
  const ov = useApi<OverviewData>(tab === 'dashboard' ? '/api/overview' : null);
  const o = ov.data;
  const outliers = stores.filter((s) => s.ok && s.outlier_label).map((s) => s.outlier_label);
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>All stores</h1>
          <div className="sub">
            {stores.length} store{stores.length === 1 ? '' : 's'}
            {tab === 'reports' ? <> · every store pooled into one</>
              : o?.common_through && <> · compared through {dlong(o.common_through)}, the oldest store's latest data</>}
          </div>
        </div>
        {tab === 'dashboard' && <div className="actions"><OutlierToggle labels={outliers} /></div>}
      </div>
      <ViewTabs base="/" reports="/reports" />
      {tab === 'reports' ? <ReportBuilder storeId="all" /> : <OverviewBody ov={ov} stores={stores} />}
    </div>
  );
}

function OverviewBody({ ov, stores }: { ov: ReturnType<typeof useApi<OverviewData>>; stores: StoreInfo[] }) {
  const dash = useApi<Dashboard>('/api/stores/all/dashboard');
  const o = ov.data;
  return (
    <>
      {o?.unavailable.map((u) => (
        <div className="banner" key={u.id}><b>{u.name}</b>: data unavailable, so it's left out of every figure below. ({u.error})</div>
      ))}
      {o?.stale.map((u) => (
        <div className="banner" key={u.id}><b>{u.name}</b>'s data is {u.days_behind} days older than the freshest store (through {dlong(u.data_through)}), so the combined figures stop there.</div>
      ))}
      {ov.error && <div className="error">{ov.error}</div>}
      {!o && ov.loading && <div className="loading">Loading…</div>}
      {o && o.report_month && o.combined && <Comparison o={o} stores={stores} />}

      <div className="section-label" style={{ marginTop: 34 }}>
        All stores combined <span className="hint">— every store pooled into one, the same dashboard each store gets</span>
      </div>
      {dash.error && <div className="error">{dash.error}</div>}
      {dash.loading && !dash.data && <div className="loading">Loading…</div>}
      {dash.data && <StoreDashboard d={dash.data} stores={stores} />}
    </>
  );
}

function Comparison({ o, stores }: { o: OverviewData; stores: StoreInfo[] }) {
  const c = useColors();
  const [period, setPeriod] = useState<PeriodKey>('ttm');
  const [ex] = useExcludeOutliers();
  const report = o.report_month!;
  const slot = (id: string) => stores.find((s) => s.id === id)?.slot ?? 99;
  const labels: Record<PeriodKey, string> = {
    month: mlong(report), ytd: `${report.slice(0, 4)} to ${mlong(report).slice(0, 3)}`, ttm: `12 months to ${mlong(report)}`,
  };
  const months = monthRange(o.history_from!, report);
  const revSeries: BarSeries[] = o.stores.map((s) => ({
    key: s.id, label: s.name, color: storeColor(c, slot(s.id)),
    values: Object.fromEntries(Object.entries(s.monthly).map(([m, x]) => [m, x.rev - (ex ? s.outlier_monthly[m]?.rev ?? 0 : 0)])),
  }));
  const lines = (metric: string) => o.stores.map((s) => ({
    key: s.id, label: s.name, color: storeColor(c, slot(s.id)),
    values: (ex && s.history_ex[metric]) || s.history[metric] || {},
  }));

  return (
    <>
      <div className="section-label">Stores side by side <span className="hint">— closed months only, so every store is complete</span></div>
      <div className="toolbar">
        <div className="seg" role="group" aria-label="Period">
          {(['month', 'ytd', 'ttm'] as const).map((k) => (
            <button key={k} type="button" aria-pressed={period === k} onClick={() => setPeriod(k)}>
              {k === 'month' ? 'Last month' : k === 'ytd' ? 'Year to date' : 'Trailing 12 months'}
            </button>
          ))}
        </div>
        <span className="muted">{labels[period]} vs the same months a year earlier{ex && o.stores.some((s) => s.outlier_label) ? ' · outliers excluded' : ''}</span>
      </div>
      <div className="card" style={{ padding: '4px 4px 6px' }}>
        <div className="tablewrap">
          <table className="data">
            <thead>
              <tr>
                <th />
                <th colSpan={2} className="group">Revenue</th><th colSpan={2} className="group">Orders</th>
                <th colSpan={2} className="group">Avg ticket</th><th colSpan={2} className="group">New customers</th>
                <th colSpan={3} className="group">Retention, {mlong(report)}</th>
              </tr>
              <tr>
                <th>Store</th>
                <th className="num">Amount</th><th className="num">vs LY</th>
                <th className="num">Count</th><th className="num">vs LY</th>
                <th className="num">Amount</th><th className="num">vs LY</th>
                <th className="num">Count</th><th className="num">vs LY</th>
                <th className="num">Active (12 mo)</th><th className="num">Repeat rev.</th><th className="num">1st → 2nd</th>
              </tr>
            </thead>
            <tbody>
              {o.stores.map((s) => <Row key={s.id} s={s} st={s.periods[period]} ex={ex}
                color={storeColor(c, slot(s.id))} info={stores.find((x) => x.id === s.id)} />)}
              <Row s={o.combined!} st={o.combined!.periods[period]} ex={ex} total />
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid two" style={{ marginTop: 12 }}>
        <Card title="Revenue by store" wide blurb={`Monthly, ${mlong(months[0])} – ${mlong(report)}, stacked: the bar's height is all stores together.`}>
          <MonthlyBars months={months} series={revSeries} legend />
        </Card>
        <Card title="Active customers by store" blurb="Distinct customers with a visit in the trailing 12 months."
          info="The engaged base of each store. A store's line starts once it has a year of history.">
          <MultiLine months={months} series={lines('active_customers_ttm')} unit="int" />
        </Card>
        <Card title="Repeat share of revenue by store" blurb="Revenue from return visits, trailing 12 months.">
          <MultiLine months={months} series={lines('repeat_revenue_share')} unit="pct" />
        </Card>
      </div>
    </>
  );
}

function Row({ s, st, ex, color, info, total }: {
  s: OverviewRow; st: PeriodStats; ex: boolean; color?: string; info?: StoreInfo; total?: boolean;
}) {
  const rev = ex ? st.rev_ex : st.rev, v = ex ? st.v_ex : st.v;
  const lyRev = ex ? st.ly_rev_ex : st.ly_rev, lyV = ex ? st.ly_v_ex : st.ly_v;
  const t = rev / (v || 1), lyT = lyV ? lyRev / lyV : null;
  const d = (x: number | null) => <td className={'num ' + relCls(x)}>{pctTxt(x)}</td>;
  const hadLy = lyV > 0;
  return (
    <tr className={total ? 'tot' : ''}>
      <th scope="row">
        <div className="store-cell">
          {color && <span className="dot" style={{ background: color }} />}
          <div>
            {total ? 'All stores' : <Link to={`/stores/${s.id}`}>{s.name}</Link>}
            {info && <div className="sub">{info.tier === 'on-prem' ? 'On-prem' : 'Cloud'} · data through {info.data_through && dlong(info.data_through)}</div>}
          </div>
        </div>
      </th>
      <td className="num">{usdFull(rev)}</td>{hadLy ? d(rel(rev, lyRev)) : <td className="num muted">new</td>}
      <td className="num">{v.toLocaleString()}</td>{hadLy ? d(rel(v, lyV)) : <td className="num muted">—</td>}
      <td className="num">{v ? usd(t) : '—'}</td>{hadLy ? d(rel(t, lyT)) : <td className="num muted">—</td>}
      <td className="num">{st.new.toLocaleString()}</td>{hadLy ? d(rel(st.new, st.ly_new)) : <td className="num muted">—</td>}
      <td className="num">{fmtUnit(s.kpis.active_customers_ttm, 'int')}</td>
      <td className="num">{fmtUnit(ex && 'repeat_revenue_share' in s.kpis_ex ? s.kpis_ex.repeat_revenue_share : s.kpis.repeat_revenue_share, 'pct')}</td>
      <td className="num">{fmtUnit(s.kpis.first_to_second_rate, 'pct')}</td>
    </tr>
  );
}
