// Chart building blocks on Recharts. Every chart follows the same marks: 2px lines, bars at most
// 24px with a 4px rounded data end, hairline solid gridlines, a ~10% area wash, a crosshair
// tooltip that lists every series. Colours come from the CSS tokens, resolved per theme.
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Area, Bar, CartesianGrid, Cell, ComposedChart, Line, LineChart, ReferenceArea, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { dimText, fmtShort, fmtUnit, fmtValue, ml, type Unit } from './format';
import type { Column, KpiMeta, ReportResult } from './types';

// ---- theme ----------------------------------------------------------------------

export interface Colors {
  s: string[]; other: string; ink: string; ink2: string; muted: string; grid: string; axis: string;
  surface: string; accent: string; good: string; bad: string;
}

function readColors(): Colors {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim();
  return {
    s: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v(`--s${i}`)), other: v('--other'), ink: v('--ink'),
    ink2: v('--ink-2'), muted: v('--muted'), grid: v('--grid'), axis: v('--axis'), surface: v('--surface'),
    accent: v('--accent'), good: v('--good'), bad: v('--bad'),
  };
}

export const THEME_EVENT = 'fs-theme';

export function useColors(): Colors {
  const [c, setC] = useState(readColors);
  useEffect(() => {
    const update = () => setC(readColors());
    const mq = matchMedia('(prefers-color-scheme: dark)');
    mq.addEventListener('change', update);
    window.addEventListener(THEME_EVENT, update);
    return () => { mq.removeEventListener('change', update); window.removeEventListener(THEME_EVENT, update); };
  }, []);
  return c;
}

/** A store's colour: its slot in stores.toml, never its rank on a chart. */
export const storeColor = (c: Colors, slot: number) => (slot < c.s.length ? c.s[slot] : c.other);

const AXIS_TICK = { fontSize: 11 };

function axisProps(c: Colors) {
  return {
    tick: { ...AXIS_TICK, fill: c.muted }, tickLine: false, axisLine: { stroke: c.axis },
  } as const;
}

/** Ticks for a monthly x-axis: every Jan (as the year) on a long span, else every Nth month. */
export function monthTicks(ms: string[]): { ticks: string[]; fmt: (m: string) => string } {
  if (ms.length > 40) {
    return { ticks: ms.filter((m) => m.endsWith('-01')), fmt: (m) => m.slice(0, 4) };
  }
  const every = ms.length > 24 ? 4 : ms.length > 12 ? 3 : ms.length > 6 ? 2 : 1;
  return { ticks: ms.filter((_, i) => (ms.length - 1 - i) % every === 0), fmt: ml };
}

// ---- tooltip ----------------------------------------------------------------------

interface TipRow { color: string; name: string; value: string; line?: boolean }

function TipBox({ title, rows }: { title: string; rows: TipRow[] }) {
  return (
    <div className="chart-tip">
      <div className="t">{title}</div>
      {rows.map((r, i) => (
        <div className="row" key={i}>
          <span className="k" style={{ background: r.color, height: r.line ? 2 : 8, width: r.line ? 12 : 8 }} />
          <span className="v">{r.value}</span>
          <span className="n">{r.name}</span>
        </div>
      ))}
    </div>
  );
}

// ---- KPI trend -----------------------------------------------------------------------

export interface TrendPoint { m: string; v: number; final: boolean }

/** One KPI over time: solid line + wash for closed months, dashed for the open month. */
export function TrendChart({ meta, points, band, height = 190 }: {
  meta: KpiMeta; points: TrendPoint[]; band?: string[] | null; height?: number;
}) {
  const c = useColors();
  const data = useMemo(() => {
    // closed months are solid; the dashed tail starts at the last closed month so it connects
    const lastFinal = points.reduce((a, p, i) => (p.final ? i : a), -1);
    return points.map((p, i) => ({
      m: p.m, v: p.v, final: p.final,
      solid: p.final ? p.v : null,
      open: i >= Math.max(lastFinal, 0) ? p.v : null,
    }));
  }, [points]);
  const { ticks, fmt } = monthTicks(points.map((p) => p.m));
  const max = Math.max(...points.map((p) => p.v), meta.baseline ?? 0);
  const yfmt = (v: number) => (meta.unit === 'pct' ? (v * 100).toFixed(0) + '%' : Math.round(v).toLocaleString());
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={c.grid} vertical={false} />
        <XAxis dataKey="m" ticks={ticks} tickFormatter={fmt} {...axisProps(c)} minTickGap={8} />
        <YAxis domain={[0, max > 0 ? 'auto' : 1]} tickFormatter={yfmt} width={44} {...axisProps(c)} axisLine={false} tickCount={4} />
        {band && band.length > 0 && (
          <ReferenceArea x1={band[0]} x2={band[band.length - 1]} fill={c.accent} fillOpacity={0.08} ifOverflow="extendDomain" />
        )}
        {meta.baseline != null && (
          <ReferenceLine y={meta.baseline} stroke={c.muted} strokeDasharray="4 4"
            label={{ value: 'baseline', position: 'insideTopRight', fill: c.muted, fontSize: 10 }} />
        )}
        <Tooltip
          cursor={{ stroke: c.axis, strokeWidth: 1 }}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const p = payload[0].payload as { m: string; v: number; final: boolean };
            return <TipBox title={ml(p.m) + (p.final ? '' : ' · open')}
              rows={[{ color: c.accent, name: meta.tile, value: fmtUnit(p.v, meta.unit), line: true }]} />;
          }}
        />
        <Area dataKey="solid" stroke="none" fill={c.accent} fillOpacity={0.1} isAnimationActive={false} connectNulls={false} />
        <Line dataKey="solid" stroke={c.accent} strokeWidth={2} dot={false} isAnimationActive={false}
          activeDot={{ r: 4, stroke: c.surface, strokeWidth: 2 }} />
        <Line dataKey="open" stroke={c.accent} strokeWidth={2} strokeDasharray="4 3" dot={false} isAnimationActive={false}
          activeDot={{ r: 4, stroke: c.surface, strokeWidth: 2 }} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function Spark({ points, color }: { points: { v: number }[]; color: string }) {
  if (points.length < 2) return <div className="spark" />;
  return (
    <div className="spark">
      <ResponsiveContainer width="100%" height={30}>
        <LineChart data={points} margin={{ top: 3, right: 3, bottom: 3, left: 3 }}>
          <YAxis hide domain={['auto', 'auto']} />
          <Line dataKey="v" stroke={color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---- monthly bars (single or stacked) -------------------------------------------------

export interface BarSeries { key: string; label: string; color: string; values: Record<string, number> }

export function MonthlyBars({
  months, series, partial, muteOutside, avg, money = true, height = 230, legend,
}: {
  months: string[]; series: BarSeries[]; partial?: string; muteOutside?: string[]; avg?: boolean;
  money?: boolean; height?: number; legend?: boolean;
}) {
  const c = useColors();
  const data = useMemo(() => months.map((m, i) => {
    const row: Record<string, number | string | null> = { m };
    let total = 0;
    for (const s of series) { const v = s.values[m] ?? 0; row[s.key] = v; total += v; }
    row.__total = total;
    if (avg && i >= 2) {
      const t = (k: string) => series.reduce((a, s) => a + (s.values[k] ?? 0), 0);
      row.__avg = (t(months[i]) + t(months[i - 1]) + t(months[i - 2])) / 3;
    } else row.__avg = null;
    return row;
  }), [months, series, avg]);
  const { ticks, fmt } = monthTicks(months);
  const vf = (v: number) => (money ? fmtShort(v, 'money') : fmtShort(v));
  const stacked = series.length > 1;
  return (
    <>
      {legend && stacked && (
        <div className="legend">
          {series.map((s) => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}
          {avg && <span><i className="line" style={{ background: c.ink2 }} />3-month average</span>}
        </div>
      )}
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }} barCategoryGap="22%">
          <CartesianGrid stroke={c.grid} vertical={false} />
          <XAxis dataKey="m" ticks={ticks} tickFormatter={fmt} {...axisProps(c)} minTickGap={8} />
          <YAxis tickFormatter={vf} width={48} {...axisProps(c)} axisLine={false} tickCount={4} />
          <Tooltip
            cursor={{ fill: c.grid, fillOpacity: 0.5 }}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const row = payload[0].payload as Record<string, number>;
              const rows: TipRow[] = series.map((s) => ({ color: s.color, name: s.label, value: money ? fmtValue(row[s.key], 'money') : fmtValue(row[s.key], 'int') }));
              if (stacked) rows.unshift({ color: 'transparent', name: 'total', value: money ? fmtValue(row.__total, 'money') : fmtValue(row.__total, 'int') });
              if (avg && row.__avg != null) rows.push({ color: c.ink2, name: '3-mo avg', value: fmtValue(row.__avg, money ? 'money' : 'int'), line: true });
              return <TipBox title={ml(String(label)) + (label === partial ? ' · so far' : '')} rows={rows} />;
            }}
          />
          {series.map((s, si) => (
            <Bar key={s.key} dataKey={s.key} stackId={stacked ? 'a' : undefined} fill={s.color} maxBarSize={24}
              radius={si === series.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]} isAnimationActive={false}
              stroke={stacked ? c.surface : undefined} strokeWidth={stacked ? 1 : 0}>
              {months.map((m) => (
                <Cell key={m} fillOpacity={m === partial ? 0.45 : muteOutside && muteOutside.length && !muteOutside.includes(m) ? 0.35 : 1} />
              ))}
            </Bar>
          ))}
          {avg && <Line dataKey="__avg" stroke={c.ink2} strokeWidth={1.5} strokeDasharray="5 3" dot={false} isAnimationActive={false} connectNulls />}
        </ComposedChart>
      </ResponsiveContainer>
    </>
  );
}

// ---- several series over months (one line each) ----------------------------------------

export function MultiLine({ months, series, unit, height = 220 }: {
  months: string[]; series: { key: string; label: string; color: string; values: Record<string, number | null> }[];
  unit: Unit; height?: number;
}) {
  const c = useColors();
  const data = months.map((m) => {
    const row: Record<string, number | string | null> = { m };
    for (const s of series) row[s.key] = s.values[m] ?? null;
    return row;
  });
  const { ticks, fmt } = monthTicks(months);
  const yfmt = (v: number) => (unit === 'pct' ? (v * 100).toFixed(0) + '%' : Math.round(v).toLocaleString());
  return (
    <>
      <div className="legend">{series.map((s) => <span key={s.key}><i className="line" style={{ background: s.color }} />{s.label}</span>)}</div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={c.grid} vertical={false} />
          <XAxis dataKey="m" ticks={ticks} tickFormatter={fmt} {...axisProps(c)} minTickGap={8} />
          <YAxis tickFormatter={yfmt} width={44} {...axisProps(c)} axisLine={false} tickCount={4} />
          <Tooltip cursor={{ stroke: c.axis }} content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            const row = payload[0].payload as Record<string, number | null>;
            return <TipBox title={ml(String(label))} rows={series.map((s) => ({ color: s.color, name: s.label, value: fmtUnit(row[s.key], unit), line: true }))} />;
          }} />
          {series.map((s) => (
            <Line key={s.key} dataKey={s.key} stroke={s.color} strokeWidth={2} dot={false} isAnimationActive={false}
              connectNulls={false} activeDot={{ r: 4, stroke: c.surface, strokeWidth: 2 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </>
  );
}

// ---- generic report chart (insights + report builder) -----------------------------------

const MAX_SERIES = 7; // + "Other"

export interface ReportChartOptions {
  type: 'auto' | 'bar' | 'hbar' | 'line' | 'stacked' | 'stacked_dim' | 'table';
  metric?: string | null;
}

/** Draws a semantic.run_report result. Returns null when the shape can't be charted. */
export function ReportChart({ result, opts, onNote }: {
  result: ReportResult; opts: ReportChartOptions; onNote?: (note: string) => void;
}) {
  const c = useColors();
  const dims = result.columns.filter((x) => x.role === 'dimension');
  const mets = result.columns.filter((x) => x.role === 'metric');
  const built = useMemo(() => buildReportChart(result, opts, c), [result, opts, c]);
  useEffect(() => { onNote?.(built?.note ?? ''); }, [built, onNote]);
  if (!built || !dims.length || !mets.length) return null;
  const { data, series, horizontal, line, valueFormat } = built;
  const vf = (v: number) => fmtShort(v, valueFormat);
  const height = horizontal ? Math.max(180, data.length * 26 + 40) : 260;
  const multi = series.length > 1;
  const legendEl = multi ? (
    <div className="legend">{series.map((s) => <span key={s.key}><i className={line ? 'line' : ''} style={{ background: s.color }} />{s.label}</span>)}</div>
  ) : null;
  const tip = (
    <Tooltip cursor={line ? { stroke: c.axis } : { fill: c.grid, fillOpacity: 0.5 }} content={({ active, payload, label }) => {
      if (!active || !payload?.length) return null;
      const row = payload[0].payload as Record<string, number | null>;
      const rows: TipRow[] = series.filter((s) => row[s.key] != null)
        .map((s) => ({ color: s.color, name: s.label, value: fmtValue(row[s.key], s.format), line }));
      return <TipBox title={String(label)} rows={rows} />;
    }} />
  );
  return (
    <>
      {legendEl}
      <ResponsiveContainer width="100%" height={height}>
        {line ? (
          <LineChart data={data} margin={{ top: 8, right: 14, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={c.grid} vertical={false} />
            <XAxis dataKey="__x" {...axisProps(c)} minTickGap={12} />
            <YAxis tickFormatter={vf} width={52} {...axisProps(c)} axisLine={false} tickCount={5} />
            {tip}
            {series.map((s) => (
              <Line key={s.key} dataKey={s.key} stroke={s.color} strokeWidth={2} isAnimationActive={false}
                dot={data.length > 40 ? false : { r: 3, fill: s.color, stroke: c.surface, strokeWidth: 2 }}
                activeDot={{ r: 4, stroke: c.surface, strokeWidth: 2 }} connectNulls />
            ))}
          </LineChart>
        ) : (
          <ComposedChart data={data} layout={horizontal ? 'vertical' : 'horizontal'}
            margin={{ top: 8, right: 14, bottom: 0, left: 0 }} barCategoryGap="22%">
            <CartesianGrid stroke={c.grid} horizontal={!horizontal} vertical={horizontal} />
            {horizontal ? (
              <>
                <XAxis type="number" tickFormatter={vf} {...axisProps(c)} tickCount={5} />
                <YAxis type="category" dataKey="__x" width={150} {...axisProps(c)} interval={0} />
              </>
            ) : (
              <>
                <XAxis dataKey="__x" {...axisProps(c)} minTickGap={8} />
                <YAxis tickFormatter={vf} width={52} {...axisProps(c)} axisLine={false} tickCount={5} />
              </>
            )}
            {tip}
            {series.map((s, si) => (
              <Bar key={s.key} dataKey={s.key} fill={s.color} stackId={built.stacked ? 'a' : undefined}
                maxBarSize={24} isAnimationActive={false}
                stroke={built.stacked ? c.surface : undefined} strokeWidth={built.stacked ? 1 : 0}
                radius={built.stacked && si < series.length - 1 ? [0, 0, 0, 0] : horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} />
            ))}
          </ComposedChart>
        )}
      </ResponsiveContainer>
    </>
  );
}

interface Built {
  data: Record<string, unknown>[]; series: { key: string; label: string; color: string; format?: string }[];
  horizontal: boolean; line: boolean; stacked: boolean; valueFormat?: string; note: string;
}

function buildReportChart(result: ReportResult, opts: ReportChartOptions, c: Colors): Built | null {
  const cols = result.columns;
  const dims = cols.filter((x) => x.role === 'dimension');
  const mets = cols.filter((x) => x.role === 'metric');
  if (opts.type === 'table' || !dims.length || dims.length > 2 || !mets.length || !result.rows.length) return null;
  const d0 = dims[0];
  const d0i = cols.indexOf(d0);
  const timeish = d0.kind === 'time' || d0.kind === 'date';
  let note = '';

  // several metrics side by side as series (e.g. moulding/mat/glazing/labor sales by year)
  if (opts.type === 'stacked' && dims.length === 1) {
    const data = result.rows.map((r) => {
      const row: Record<string, unknown> = { __x: dimText(r[d0i], d0.kind) };
      mets.forEach((m) => { row[m.key] = r[cols.indexOf(m)]; });
      return row;
    });
    return {
      data, series: mets.slice(0, 8).map((m, i) => ({ key: m.key, label: m.label, color: c.s[i], format: m.format })),
      horizontal: false, line: false, stacked: true, valueFormat: mets[0].format, note,
    };
  }

  const met: Column = mets.find((m) => m.key === opts.metric) || mets[0];
  const mi = cols.indexOf(met);
  const type = opts.type === 'auto' ? (timeish ? 'line' : 'bar') : opts.type;
  const line = type === 'line';
  const horizontal = type === 'hbar' || (type === 'bar' && ['category', 'text', 'id'].includes(d0.kind ?? ''));
  const additive = met.format !== 'pct' && !met.key.startsWith('avg');
  const cap = horizontal ? 40 : 400;

  if (dims.length === 1) {
    let rows = result.rows;
    if (rows.length > cap) { rows = rows.slice(0, cap); note = `Chart shows the first ${cap} rows; the table has all ${result.rows.length}.`; }
    return {
      data: rows.map((r) => ({ __x: dimText(r[d0i], d0.kind), [met.key]: r[mi] })),
      series: [{ key: met.key, label: met.label, color: c.s[0], format: met.format }],
      horizontal, line, stacked: false, valueFormat: met.format, note,
    };
  }

  // two dimensions: the second becomes the series, top MAX_SERIES + "Other"
  const d1 = dims[1];
  const d1i = cols.indexOf(d1);
  const xKeys = [...new Set(result.rows.map((r) => r[d0i]))].slice(0, cap);
  const totals = new Map<unknown, number>();
  for (const r of result.rows) totals.set(r[d1i], (totals.get(r[d1i]) || 0) + Math.abs(Number(r[mi]) || 0));
  const ranked = [...totals.keys()].sort((a, b) => (totals.get(b) || 0) - (totals.get(a) || 0));
  const keep = ranked.slice(0, MAX_SERIES);
  const keyOf = (v: unknown) => 's_' + String(v);
  const cells = new Map<string, number>();
  for (const r of result.rows) {
    const s = keep.includes(r[d1i]) ? keyOf(r[d1i]) : '__other';
    if (s === '__other' && !additive) continue;
    const k = s + '\u0000' + String(r[d0i]);
    cells.set(k, (cells.get(k) || 0) + (Number(r[mi]) || 0));
  }
  // series colour follows the value's position in the sorted value list, so it is stable
  const stableOrder = [...ranked].sort((a, b) => String(a).localeCompare(String(b)));
  const series = keep.map((v) => ({
    key: keyOf(v), label: dimText(v, d1.kind), color: c.s[stableOrder.indexOf(v) % 8], format: met.format,
  }));
  if (ranked.length > MAX_SERIES) {
    if (additive) series.push({ key: '__other', label: 'Other', color: c.other, format: met.format });
    note = additive
      ? `${ranked.length - MAX_SERIES} smaller ${d1.label.toLowerCase()} values are combined into "Other".`
      : `Showing the top ${MAX_SERIES} ${d1.label.toLowerCase()} values; see the table for the rest.`;
  }
  const data = xKeys.map((x) => {
    const row: Record<string, unknown> = { __x: dimText(x, d0.kind) };
    for (const s of series) row[s.key] = cells.get(s.key + '\u0000' + String(x)) ?? null;
    return row;
  });
  return { data, series, horizontal, line, stacked: !line && additive, valueFormat: met.format, note };
}

export function Card({ title, blurb, right, info, wide, children, foot }: {
  title: ReactNode; blurb?: ReactNode; right?: ReactNode; info?: string; wide?: boolean; children: ReactNode; foot?: ReactNode;
}) {
  return (
    <figure className={'card' + (wide ? ' wide' : '')} style={{ margin: 0 }}>
      <div className="card-head">
        <div>
          <h2>{title}{info && <InfoTip text={info} />}</h2>
          {blurb && <p className="blurb">{blurb}</p>}
        </div>
        {right && <div className="right">{right}</div>}
      </div>
      {children}
      {foot && <p className="foot">{foot}</p>}
    </figure>
  );
}

export function InfoTip({ text }: { text: string }) {
  return (
    <span className="info">
      <button type="button" aria-label="What this means">i</button>
      <span className="tip" role="tooltip">{text}</span>
    </span>
  );
}
