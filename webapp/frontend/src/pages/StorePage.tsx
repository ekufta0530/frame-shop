import { Link, useParams } from 'react-router-dom';
import { useApi } from '../api';
import { Card, ReportChart } from '../charts';
import { OutlierToggle, ViewTabs } from '../controls';
import { dlong, dimText, fmtValue } from '../format';
import type { Dashboard, Insight, Insights, StoreInfo } from '../types';
import ReportBuilder from './ReportBuilder';
import StoreDashboard from './StoreDashboard';

export default function StorePage({ stores, tab }: { stores: StoreInfo[]; tab: 'dashboard' | 'reports' }) {
  const { storeId } = useParams();
  const info = stores.find((s) => s.id === storeId);
  if (!info) return <div className="page"><div className="error">No store “{storeId}”.</div></div>;
  const onPrem = info.tier === 'on-prem';
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{info.name} <span className={'tier ' + info.tier}>{onPrem ? 'On-prem' : 'Cloud'}</span></h1>
          <div className="sub">
            {info.ok && info.data_through
              ? onPrem ? <>From the owner's latest upload · orders through {dlong(info.data_through)}</>
                : <>Lifesaver Cloud · synced daily · data through {dlong(info.data_through)}</>
              : 'Data unavailable'}
          </div>
        </div>
        {tab === 'dashboard' && <div className="actions"><OutlierToggle labels={info.outlier_label ? [info.outlier_label] : []} /></div>}
      </div>
      <ViewTabs base={`/stores/${info.id}`} reports={`/stores/${info.id}/reports`} />
      {!info.ok ? <div className="error">{info.name}: data unavailable — {info.error}</div>
        : tab === 'reports' ? <ReportBuilder storeId={info.id} />
          : <StoreBody info={info} stores={stores} />}
    </div>
  );
}

function StoreBody({ info, stores }: { info: StoreInfo; stores: StoreInfo[] }) {
  const dash = useApi<Dashboard>(`/api/stores/${info.id}/dashboard`);
  return (
    <>
      {dash.error && <div className="error">{dash.error}</div>}
      {dash.loading && !dash.data && <div className="loading">Loading…</div>}
      {dash.data && <StoreDashboard d={dash.data} stores={stores} />}
      {info.tier === 'on-prem' && dash.data && <OnPremSection storeId={info.id} />}
    </>
  );
}

function OnPremSection({ storeId }: { storeId: string }) {
  const ins = useApi<Insights>(`/api/stores/${storeId}/insights`);
  const d = ins.data;
  return (
    <>
      <div className="section-label" style={{ marginTop: 34 }}>
        In-store detail <span className="hint">— from the full LifeSaver database ({d?.work_orders.toLocaleString() ?? '…'} work orders); build your own under <Link to={`/stores/${storeId}/reports`}>Reports</Link></span>
      </div>
      {ins.error && <div className="error">{ins.error}</div>}
      {!d && ins.loading && <div className="loading">Loading…</div>}
      {d && (
        <>
          <div className="grid">
            {d.reports.map((r) => <InsightCard key={r.id} r={r} />)}
          </div>
        </>
      )}
    </>
  );
}

function InsightCard({ r }: { r: Insight }) {
  if (r.error || !r.result) {
    return <Card title={r.title} blurb={r.blurb} wide={r.wide}><div className="error">{r.error}</div></Card>;
  }
  const res = r.result;
  const chart = r.chart === 'table' ? null
    : <ReportChart result={res} opts={{ type: r.chart as never, metric: r.metric ?? null }} />;
  // tables carry every column; charts show one metric, so list the rest beneath when there are few rows
  const showTable = r.chart === 'table' || (res.columns.filter((c) => c.role === 'metric').length > 1 && res.rows.length <= 12);
  return (
    <Card title={r.title} blurb={r.blurb} wide={r.wide}>
      {chart}
      {showTable && <MiniTable r={r} />}
    </Card>
  );
}

function MiniTable({ r }: { r: Insight }) {
  const res = r.result!;
  return (
    <div className="tablewrap" style={{ marginTop: 6, maxHeight: 360, overflowY: 'auto' }}>
      <table className="data">
        <thead><tr>{res.columns.map((c) => <th key={c.key} className={c.role === 'metric' ? 'num' : ''}>{c.label}</th>)}</tr></thead>
        <tbody>
          {res.rows.map((row, i) => (
            <tr key={i}>{row.map((v, j) => {
              const c = res.columns[j];
              return <td key={j} className={c.role === 'metric' ? 'num' : c.kind === 'text' ? 'text' : ''}>
                {c.role === 'metric' ? fmtValue(v, c.format) : dimText(v, c.kind)}</td>;
            })}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
