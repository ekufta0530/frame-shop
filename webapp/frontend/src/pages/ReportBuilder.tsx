// Report builder + SQL console over an on-prem store's full LifeSaver database (lsreports).
// A report is a plain spec; the URL hash carries it (#r=...), so "Copy link" shares the exact report.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, storage } from '../api';
import { ReportChart, type ReportChartOptions } from '../charts';
import { dimText, fmtValue } from '../format';
import type { Catalog, Column, Filter, ReportResult, ReportSpec } from '../types';

type Range = 'all' | 'ytd' | 'thisyear' | 'lastyear' | 'last12' | 'custom';
interface State {
  title: string; description: string;
  spec: ReportSpec & { range: Range };
  chart: { type: ReportChartOptions['type']; metric: string | null };
}
interface Saved { id: string; title: string; state: State }

const blank = (): State => ({
  title: 'Untitled report', description: '',
  spec: { dataset: 'work_orders', dimensions: ['year'], metrics: ['jobs', 'sales'], filters: [],
    date_from: null, date_to: null, include_void: false, limit: 500, range: 'all' },
  chart: { type: 'auto', metric: null },
});

const encode = (s: State) => btoa(unescape(encodeURIComponent(JSON.stringify(s)))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const decode = (str: string): State => JSON.parse(decodeURIComponent(escape(atob(str.replace(/-/g, '+').replace(/_/g, '/')))));

const OPS: [string, string][] = [['in', 'is any of'], ['not_in', 'is not'], ['contains', 'contains'], ['gte', '≥'], ['lte', '≤'], ['gt', '>'], ['lt', '<']];
const multi = (op: string) => op === 'in' || op === 'not_in';

/** "2026-09-14" as a local date (new Date('2026-09-14') is UTC midnight, a day early in the US). */
function snapshotDate(iso: string | null | undefined): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? '');
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date();
}

function rangeDates(range: Range, today: Date): [string | null, string | null] | null {
  const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const y = today.getFullYear();
  switch (range) {
    case 'all': return [null, null];
    case 'ytd': return [`${y}-01-01`, null];
    case 'thisyear': return [`${y}-01-01`, `${y}-12-31`];
    case 'lastyear': return [`${y - 1}-01-01`, `${y - 1}-12-31`];
    case 'last12': return [iso(new Date(y - 1, today.getMonth(), today.getDate() + 1)), null];
    default: return null;
  }
}

function useToast() {
  const [msg, setMsg] = useState('');
  const t = useRef<number>(0);
  const show = useCallback((m: string) => { setMsg(m); clearTimeout(t.current); t.current = window.setTimeout(() => setMsg(''), 1800); }, []);
  return { el: <div className={'toast' + (msg ? ' show' : '')}>{msg}</div>, show };
}

function downloadCsv(name: string, header: string[], rows: unknown[][]) {
  const esc = (v: unknown) => {
    if (v === null || v === undefined) return '';
    const s = String(v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const text = [header, ...rows].map((r) => r.map(esc).join(',')).join('\r\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv' }));
  a.download = (name || 'report').replace(/[^\w-]+/g, '_') + '.csv';
  document.body.append(a);
  a.click();
  a.remove();
}

export default function ReportBuilder({ storeId }: { storeId: string }) {
  const [tab, setTab] = useState<'reports' | 'sql'>('reports');
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    setCatalog(null);
    api<Catalog>(`/api/stores/${storeId}/catalog`).then(setCatalog, (e: Error) => setError(e.message));
  }, [storeId]);
  return (
    <>
      <div className="toolbar">
        <div className="seg" role="group" aria-label="Mode">
          <button type="button" aria-pressed={tab === 'reports'} onClick={() => setTab('reports')}>Report builder</button>
          <button type="button" aria-pressed={tab === 'sql'} onClick={() => setTab('sql')}>SQL</button>
        </div>
        {catalog && <span className="muted">{catalog.data.work_orders.toLocaleString()} work orders · newest {catalog.data.latest_order_date}</span>}
      </div>
      {error && <div className="error">Could not load the report catalog: {error}</div>}
      {catalog && (tab === 'reports' ? <Builder storeId={storeId} catalog={catalog} /> : <SqlConsole storeId={storeId} />)}
    </>
  );
}

// ---- builder -------------------------------------------------------------------------

function Builder({ storeId, catalog }: { storeId: string; catalog: Catalog }) {
  const savedKey = `fs.saved.${storeId}`;
  const [saved, setSaved] = useState<Saved[]>(() => storage.get(savedKey, []));
  const [activeId, setActiveId] = useState<string | null>(null);
  const [state, setState] = useState<State>(() => {
    const h = new URLSearchParams(location.hash.slice(1));
    if (h.get('r')) { try { return decode(h.get('r')!); } catch { /* fall through */ } }
    const p = catalog.presets.find((x) => x.id === (h.get('preset') || 'yearly_summary'));
    return p ? fromPreset(p) : blank();
  });
  const [result, setResult] = useState<ReportResult | null>(null);
  const [runError, setRunError] = useState('');
  const [note, setNote] = useState('');
  const [sort, setSort] = useState<{ i: number; dir: number } | null>(null);
  const toast = useToast();
  const ds = catalog.datasets[state.spec.dataset];

  function fromPreset(p: Catalog['presets'][number]): State {
    const b = blank();
    return {
      title: p.title, description: p.description,
      spec: { ...b.spec, filters: [], ...structuredClone(p.spec), range: p.spec.date_from ? 'custom' : 'all' },
      chart: { type: (p.chart as ReportChartOptions['type']) || 'auto', metric: null },
    };
  }

  const update = (fn: (s: State) => void, keepActive = false) => {
    setState((prev) => { const next = structuredClone(prev); fn(next); return next; });
    if (!keepActive && activeId && catalog.presets.some((p) => p.id === activeId)) setActiveId(null);
  };

  // run (debounced) whenever the spec changes; keep the URL in sync
  const seq = useRef(0);
  useEffect(() => {
    history.replaceState(null, '', '#r=' + encode(state));
    const n = ++seq.current;
    const timer = window.setTimeout(async () => {
      const { range: _range, ...spec } = state.spec;
      void _range;
      try {
        const res = await api<ReportResult>(`/api/stores/${storeId}/report`, spec);
        if (n !== seq.current) return;
        setResult(res); setRunError(''); setSort(null);
      } catch (e) {
        if (n !== seq.current) return;
        setResult(null); setRunError((e as Error).message);
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [state, storeId]);

  const save = () => {
    const next = [...saved];
    const existing = next.find((r) => r.id === activeId);
    if (existing) { existing.title = state.title; existing.state = structuredClone(state); }
    else {
      const id = 's' + Date.now();
      next.push({ id, title: state.title, state: structuredClone(state) });
      setActiveId(id);
    }
    setSaved(next); storage.set(savedKey, next); toast.show('Saved to this browser');
  };

  const groups = useMemo(() => {
    const g: Record<string, Catalog['presets']> = {};
    for (const p of catalog.presets) (g[p.group] ||= []).push(p);
    return g;
  }, [catalog]);

  const chartOpts = useMemo<ReportChartOptions>(() => ({ type: state.chart.type, metric: state.chart.metric }), [state.chart]);
  const metCols = result?.columns.filter((c) => c.role === 'metric') ?? [];
  const dimCount = result?.columns.filter((c) => c.role === 'dimension').length ?? 0;
  const chartable = result && state.chart.type !== 'table' && dimCount >= 1 && dimCount <= 2 && result.rows.length > 0;

  return (
    <div className="rb">
      <aside className="rb-side">
        <button className="btn primary block" type="button" onClick={() => { setActiveId(null); setState(blank()); }}>+ New report</button>
        {saved.length > 0 && <div className="side-title">My reports</div>}
        {saved.map((r) => (
          <div key={r.id} className={'side-item' + (activeId === r.id ? ' active' : '')}>
            <button type="button" className="grow" onClick={() => { setActiveId(r.id); setState(structuredClone(r.state)); }}>{r.title}</button>
            <button type="button" className="x" aria-label={`Delete ${r.title}`} title="Delete" onClick={() => {
              const next = saved.filter((x) => x.id !== r.id);
              setSaved(next); storage.set(savedKey, next);
            }}>×</button>
          </div>
        ))}
        {Object.entries(groups).map(([g, items]) => (
          <div key={g}>
            <div className="side-title">{g}</div>
            {items.map((p) => (
              <button key={p.id} type="button" title={p.description} className={'side-item' + (activeId === p.id ? ' active' : '')}
                onClick={() => { setActiveId(p.id); setState(fromPreset(p)); }}>{p.title}</button>
            ))}
          </div>
        ))}
      </aside>

      <main style={{ minWidth: 0 }}>
        <div className="rb-head">
          <input className="title-input" aria-label="Report title" value={state.title}
            onChange={(e) => update((s) => { s.title = e.target.value; }, true)} />
          <button className="btn" type="button" onClick={save}>Save</button>
          <button className="btn" type="button" onClick={async () => {
            try { await navigator.clipboard.writeText(location.href); toast.show('Link copied'); } catch { toast.show('Copy the address bar to share'); }
          }}>Copy link</button>
          <button className="btn" type="button" disabled={!result} onClick={() => result && downloadCsv(state.title,
            result.columns.map((c) => c.label),
            result.rows.map((r) => r.map((v, i) => (result.columns[i].role === 'dimension' ? dimText(v, result.columns[i].kind) : v))))}>
            Download CSV</button>
        </div>
        {state.description && <p className="muted" style={{ margin: '0 0 10px 4px' }}>{state.description}</p>}

        <section className="card">
          <div className="builder">
            <div className="field">
              <label htmlFor="dataset">Data</label>
              <select id="dataset" title={ds.description} value={state.spec.dataset} onChange={(e) => update((s) => {
                const next = catalog.datasets[e.target.value];
                s.spec.dataset = e.target.value;
                s.spec.dimensions = s.spec.dimensions.filter((d) => d in next.dimensions);
                s.spec.metrics = s.spec.metrics.filter((m) => m in next.metrics);
                if (!s.spec.metrics.length) s.spec.metrics = [Object.keys(next.metrics)[0]];
                s.spec.filters = (s.spec.filters || []).filter((f) => f.field in next.dimensions);
              })}>
                {Object.entries(catalog.datasets).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
              </select>
            </div>
            <div className="field grow">
              <label>Group by</label>
              <Chips selected={state.spec.dimensions} options={ds.dimensions} reorder
                onChange={(v) => update((s) => { s.spec.dimensions = v; })} />
            </div>
            <div className="field grow">
              <label>Measures</label>
              <Chips selected={state.spec.metrics} options={ds.metrics} onChange={(v) => update((s) => { s.spec.metrics = v; })} />
            </div>
            <div className="field">
              <label htmlFor="range">Order dates</label>
              <div className="row">
                <select id="range" value={state.spec.range} onChange={(e) => update((s) => {
                  s.spec.range = e.target.value as Range;
                  const d = rangeDates(s.spec.range, snapshotDate(catalog.data.latest_order_date));
                  if (d) [s.spec.date_from, s.spec.date_to] = d;
                })}>
                  <option value="all">All time</option><option value="ytd">Year to date</option>
                  <option value="thisyear">This year</option><option value="lastyear">Last year</option>
                  <option value="last12">Last 12 months</option><option value="custom">Custom</option>
                </select>
                <input type="date" aria-label="From" value={state.spec.date_from || ''}
                  onChange={(e) => update((s) => { s.spec.date_from = e.target.value || null; s.spec.range = 'custom'; })} />
                <span className="muted">to</span>
                <input type="date" aria-label="To" value={state.spec.date_to || ''}
                  onChange={(e) => update((s) => { s.spec.date_to = e.target.value || null; s.spec.range = 'custom'; })} />
              </div>
            </div>
            <div className="field">
              <label htmlFor="limit">Max rows</label>
              <input id="limit" type="number" min={1} max={5000} style={{ width: 90 }} value={state.spec.limit || 500}
                onChange={(e) => update((s) => { s.spec.limit = Math.max(1, Math.min(5000, +e.target.value || 500)); })} />
            </div>
            <div className="field">
              <label>&nbsp;</label>
              <label className="chk"><input type="checkbox" checked={!!state.spec.include_void}
                onChange={(e) => update((s) => { s.spec.include_void = e.target.checked; })} /> Include voided</label>
            </div>
          </div>
          <div style={{ marginTop: 10 }}>
            {(state.spec.filters || []).map((f, i) => (
              <FilterRow key={i} storeId={storeId} dataset={state.spec.dataset} f={f} dims={ds.dimensions}
                onChange={(nf) => update((s) => { s.spec.filters![i] = nf; })}
                onRemove={() => update((s) => { s.spec.filters!.splice(i, 1); })} />
            ))}
            <button className="btn small" type="button" style={{ marginTop: 8 }} onClick={() => update((s) => {
              s.spec.filters = [...(s.spec.filters || []), { field: s.spec.dimensions[0] || 'status', op: 'in', value: [] }];
            })}>+ Filter</button>
          </div>
        </section>

        {runError && <div className="error">{runError}</div>}
        {result?.totals && (
          <div className="kpis">
            {metCols.map((c) => (
              <div className="kpi" key={c.key}><div className="label">{c.label}</div><div className="value">{fmtValue(result.totals![c.key], c.format)}</div></div>
            ))}
          </div>
        )}

        {result && (
          <section className="card" style={{ marginBottom: 12 }}>
            <div className="card-head">
              <h2>{metCols.find((m) => m.key === state.chart.metric)?.label || metCols[0]?.label}</h2>
              <div className="right">
                {state.chart.type !== 'stacked' && metCols.length > 1 && (
                  <select aria-label="Chart measure" value={state.chart.metric || metCols[0]?.key}
                    onChange={(e) => update((s) => { s.chart.metric = e.target.value; }, true)}>
                    {metCols.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
                  </select>
                )}
                <select aria-label="Chart type" value={state.chart.type} onChange={(e) => update((s) => { s.chart.type = e.target.value as State['chart']['type']; }, true)}>
                  <option value="auto">Auto chart</option><option value="bar">Bar</option><option value="hbar">Horizontal bar</option>
                  <option value="line">Line</option><option value="stacked">Measures stacked</option><option value="table">Table only</option>
                </select>
              </div>
            </div>
            {chartable ? <ReportChart result={result} opts={chartOpts} onNote={setNote} />
              : <p className="muted">{state.chart.type === 'table' ? 'Table only.' : 'Group by one or two fields to chart this.'}</p>}
            {note && chartable && <p className="foot">{note}</p>}
          </section>
        )}

        {result && <ResultTable result={result} sort={sort} setSort={setSort} />}
        {result?.sql && <details className="more"><summary>SQL</summary>
          <pre className="sql-peek">{result.sql}{result.params?.length ? `\n-- params: ${JSON.stringify(result.params)}` : ''}</pre></details>}
      </main>
      {toast.el}
    </div>
  );
}

function Chips({ selected, options, onChange, reorder }: {
  selected: string[]; options: Record<string, { label: string }>; onChange: (v: string[]) => void; reorder?: boolean;
}) {
  return (
    <div className="chips">
      {selected.map((key, i) => (
        <span className="chip" key={key}>
          <span>{options[key]?.label ?? key}</span>
          {reorder && i > 0 && <button type="button" title="Move left" aria-label="Move left" onClick={() => {
            const a = [...selected]; [a[i - 1], a[i]] = [a[i], a[i - 1]]; onChange(a);
          }}>←</button>}
          <button type="button" aria-label={`Remove ${options[key]?.label ?? key}`} onClick={() => onChange(selected.filter((k) => k !== key))}>×</button>
        </span>
      ))}
      <select aria-label="Add" value="" onChange={(e) => e.target.value && onChange([...selected, e.target.value])}>
        <option value="">+ Add</option>
        {Object.entries(options).filter(([k]) => !selected.includes(k)).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
      </select>
    </div>
  );
}

function FilterRow({ storeId, dataset, f, dims, onChange, onRemove }: {
  storeId: string; dataset: string; f: Filter; dims: Record<string, { label: string; kind: string }>;
  onChange: (f: Filter) => void; onRemove: () => void;
}) {
  const dim = dims[f.field] || { label: f.field, kind: 'category' };
  const [suggest, setSuggest] = useState<{ value: unknown; count: number }[]>([]);
  const [text, setText] = useState('');
  const listId = useMemo(() => 'vals-' + Math.random().toString(36).slice(2), []);
  useEffect(() => {
    if (!multi(f.op)) return;
    const t = window.setTimeout(() => {
      api<{ value: unknown; count: number }[]>(`/api/stores/${storeId}/values?dataset=${dataset}&dim=${f.field}` + (text ? `&q=${encodeURIComponent(text)}` : ''))
        .then(setSuggest, () => {});
    }, 200);
    return () => clearTimeout(t);
  }, [storeId, dataset, f.field, f.op, text]);
  const values = Array.isArray(f.value) ? (f.value as unknown[]) : [];
  const commit = () => {
    const v = text.trim();
    if (!v) return;
    const hit = suggest.find((s) => String(s.value) === v || dimText(s.value, dim.kind) === v);
    onChange({ ...f, value: [...values, hit ? hit.value : v] });
    setText('');
  };
  return (
    <div className="filter-row">
      <select aria-label="Filter field" value={f.field} onChange={(e) => onChange({ field: e.target.value, op: f.op, value: multi(f.op) ? [] : '' })}>
        {Object.entries(dims).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
      </select>
      <select aria-label="Filter operator" value={f.op} onChange={(e) => {
        const op = e.target.value;
        onChange({ ...f, op, value: multi(op) === multi(f.op) ? f.value : multi(op) ? [] : '' });
      }}>
        {OPS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
      <div className="vals">
        {multi(f.op) ? (
          <>
            {values.map((v) => (
              <span className="chip" key={String(v)}>{dimText(v, dim.kind)}
                <button type="button" aria-label={`Remove ${String(v)}`} onClick={() => onChange({ ...f, value: values.filter((x) => x !== v) })}>×</button>
              </span>
            ))}
            <input type="text" list={listId} placeholder="type or pick…" aria-label="Filter value" value={text}
              onChange={(e) => {
                setText(e.target.value);
                const it = (e.nativeEvent as InputEvent).inputType;
                const native = it === undefined || it === 'insertReplacementText'; // picked from the list
                if (native && suggest.some((s) => String(s.value) === e.target.value)) {
                  onChange({ ...f, value: [...values, suggest.find((s) => String(s.value) === e.target.value)!.value] });
                  setText('');
                }
              }}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commit(); } }} />
            <datalist id={listId}>
              {suggest.map((s) => <option key={String(s.value)} value={String(s.value)}>{`${dimText(s.value, dim.kind)} (${s.count})`}</option>)}
            </datalist>
          </>
        ) : (
          <input type={dim.kind === 'date' && f.value !== 'today' ? 'date' : 'text'} aria-label="Filter value"
            placeholder={dim.kind === 'date' ? 'YYYY-MM-DD or today' : 'value'} value={String(f.value ?? '')}
            onChange={(e) => onChange({ ...f, value: e.target.value })} />
        )}
      </div>
      <button className="btn small" type="button" aria-label="Remove filter" onClick={onRemove}>×</button>
    </div>
  );
}

function ResultTable({ result, sort, setSort }: {
  result: ReportResult; sort: { i: number; dir: number } | null; setSort: (s: { i: number; dir: number } | null) => void;
}) {
  const cols = result.columns;
  const isNum = (c: Column) => c.role === 'metric' || c.kind === 'id';
  const rows = useMemo(() => {
    if (!sort) return result.rows;
    const { i, dir } = sort;
    return [...result.rows].sort((a, b) => {
      const x = a[i] as never, y = b[i] as never;
      if (x === y) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return (x < y ? -1 : 1) * dir;
    });
  }, [result, sort]);
  return (
    <section className="card">
      <div className="card-head"><h2>Results <span className="muted" style={{ fontWeight: 400 }}>
        · {result.rows.length.toLocaleString()} rows{result.truncated ? ' (limit reached — raise Max rows)' : ''}</span></h2></div>
      <div className="tablewrap" style={{ maxHeight: 560, overflowY: 'auto' }}>
        <table className="data">
          <thead><tr>{cols.map((c, i) => (
            <th key={c.key} scope="col" className={'sortable ' + (isNum(c) ? 'num' : '')}
              aria-sort={sort?.i === i ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'}
              onClick={() => setSort({ i, dir: sort?.i === i ? -sort.dir : c.role === 'metric' ? -1 : 1 })}>
              {c.label}{sort?.i === i ? (sort.dir > 0 ? ' ↑' : ' ↓') : ''}</th>
          ))}</tr></thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri}>{r.map((v, i) => {
                const c = cols[i];
                return <td key={i} className={isNum(c) ? 'num' : c.kind === 'text' ? 'text' : ''}>
                  {c.role === 'metric' ? fmtValue(v, c.format) : dimText(v, c.kind)}</td>;
              })}</tr>
            ))}
          </tbody>
          {cols.some((c) => c.role === 'dimension') && result.totals && (
            <tfoot><tr>{cols.map((c, i) => (
              <td key={c.key} className={isNum(c) ? 'num' : ''}>{c.role === 'metric' ? fmtValue(result.totals![c.key], c.format) : i === 0 ? 'Total' : ''}</td>
            ))}</tr></tfoot>
          )}
        </table>
      </div>
    </section>
  );
}

// ---- SQL console ---------------------------------------------------------------------

const SQL_EXAMPLES: Record<string, string> = {
  'Repeat customers': `SELECT customer_name, COUNT(*) AS jobs, ROUND(SUM(subtotal)) AS sales,
  MIN(order_date) AS first_order, MAX(order_date) AS last_order
FROM wo_facts WHERE status != 'Void'
GROUP BY cust_no HAVING jobs >= 5 ORDER BY sales DESC LIMIT 100;`,
  'Lapsed customers (no order in 2 years before the newest order)': `SELECT customer_name, customer_city, COUNT(*) AS jobs, ROUND(SUM(subtotal)) AS lifetime_sales,
  MAX(order_date) AS last_order
FROM wo_facts WHERE status != 'Void'
GROUP BY cust_no HAVING last_order < (SELECT date(MAX(order_date), '-2 years') FROM wo_facts) AND jobs >= 3
ORDER BY lifetime_sales DESC LIMIT 100;`,
  'Moulding cost vs price per foot': `SELECT comp_name, comp_vendor, COUNT(*) AS uses, ROUND(SUM(comp_feet)) AS feet,
  ROUND(SUM(comp_cost) / SUM(comp_feet), 2) AS cost_per_ft,
  ROUND(SUM(comp_net_price) / SUM(comp_feet), 2) AS price_per_ft
FROM comp_facts WHERE comp_kind = 'Moulding' AND comp_feet > 0 AND status != 'Void'
GROUP BY 1, 2 HAVING uses >= 20 ORDER BY uses DESC;`,
  'Revision history of one work order': `SELECT fwkorderno, frevision, frevdate, fstatustxt, fsubt, fdesc
FROM work_order_revisions WHERE fwkorderno = 12433 ORDER BY frevision;`,
  'Payments by month and type': `SELECT strftime('%Y-%m', pydate) AS month, pypaid AS pay_type, COUNT(*) AS n, ROUND(SUM(pydeposit)) AS amount
FROM payments GROUP BY 1, 2 ORDER BY 1 DESC, 4 DESC LIMIT 200;`,
};

function SqlConsole({ storeId }: { storeId: string }) {
  const [sql, setSql] = useState(SQL_EXAMPLES['Repeat customers']);
  const [schema, setSchema] = useState<{ name: string; type: string; columns: string[] }[] | null>(null);
  const [res, setRes] = useState<{ columns: string[]; rows: unknown[][]; truncated: boolean } | null>(null);
  const [error, setError] = useState('');
  const ta = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { api<typeof schema>(`/api/stores/${storeId}/schema`).then(setSchema, (e: Error) => setError(e.message)); }, [storeId]);
  const run = async (text = sql) => {
    try { setRes(await api(`/api/stores/${storeId}/sql`, { sql: text })); setError(''); }
    catch (e) { setRes(null); setError((e as Error).message); }
  };
  const insert = (t: string) => {
    const el = ta.current;
    if (!el) return;
    const { selectionStart: s, selectionEnd: e } = el;
    setSql(sql.slice(0, s) + t + sql.slice(e));
    requestAnimationFrame(() => { el.focus(); el.selectionStart = el.selectionEnd = s + t.length; });
  };
  const numeric = res ? res.columns.map((_, i) => res.rows.every((r) => r[i] === null || typeof r[i] === 'number')) : [];
  return (
    <div className="sql-layout">
      <aside className="card" style={{ maxHeight: 640, overflowY: 'auto' }}>
        <div className="side-title" style={{ marginTop: 0 }}>Tables & views</div>
        {schema?.map((t) => (
          <details className="schema" key={t.name} open={['wo_facts', 'comp_facts'].includes(t.name)}>
            <summary>{t.name}<span className="tag">{t.type}</span></summary>
            <div className="cols">{t.columns.map((c) => <button key={c} type="button" title="Insert" onClick={() => insert(c)}>{c}</button>)}</div>
          </details>
        ))}
      </aside>
      <main style={{ minWidth: 0 }}>
        <div className="toolbar">
          <select value="" aria-label="Examples" onChange={(e) => { if (e.target.value) { setSql(SQL_EXAMPLES[e.target.value]); run(SQL_EXAMPLES[e.target.value]); } }}>
            <option value="">Examples…</option>
            {Object.keys(SQL_EXAMPLES).map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <button className="btn primary" type="button" onClick={() => run()}>Run (⌘↵)</button>
          <button className="btn" type="button" disabled={!res} onClick={() => res && downloadCsv('query', res.columns, res.rows)}>Download CSV</button>
          <span className="muted">Read-only; queries stop after 10 seconds.</span>
        </div>
        <textarea ref={ta} className="sql-input" spellCheck={false} value={sql} onChange={(e) => setSql(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); run(); } }} />
        {error && <div className="error">{error}</div>}
        {res && (
          <section className="card" style={{ marginTop: 10 }}>
            <div className="card-head"><h2>{res.rows.length.toLocaleString()} rows{res.truncated ? ' (first 5,000)' : ''}</h2></div>
            <div className="tablewrap" style={{ maxHeight: 560, overflowY: 'auto' }}>
              <table className="data">
                <thead><tr>{res.columns.map((c, i) => <th key={i} className={numeric[i] ? 'num' : ''}>{c}</th>)}</tr></thead>
                <tbody>{res.rows.map((r, ri) => (
                  <tr key={ri}>{r.map((v, i) => (
                    <td key={i} className={numeric[i] ? 'num' : typeof v === 'string' && v.length > 60 ? 'text' : ''}>
                      {v === null ? '—' : typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v)}</td>
                  ))}</tr>
                ))}</tbody>
              </table>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
