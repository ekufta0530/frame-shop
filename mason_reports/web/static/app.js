'use strict';

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k != null) n.append(k);
  return n;
};

const store = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch { /* storage unavailable */ } },
};

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (res.status === 401) {
    // session expired or signed out elsewhere: come back to the same report after signing in
    try { sessionStorage.setItem('lsr.next', location.hash); } catch { /* storage unavailable */ }
    location.replace('/login.html');
    throw new Error('Not signed in');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${res.status})`);
  return data;
}

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove('show'), 1800);
}

// ---------- formatting ----------

function fmt(v, format) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v !== 'number') return String(v);
  switch (format) {
    case 'money': return (v < 0 ? '−$' : '$') + Math.abs(v).toLocaleString(undefined,
      Math.abs(v) >= 1000 ? { maximumFractionDigits: 0 } : { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    case 'pct': return v.toFixed(1) + '%';
    case 'num1': return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
    case 'int': return Math.round(v).toLocaleString();
    default: return v.toLocaleString();
  }
}

function fmtShort(v, format) {
  if (typeof v !== 'number') return v;
  if (format === 'pct') return v.toFixed(0) + '%';
  const a = Math.abs(v);
  const s = a >= 1e6 ? (a / 1e6).toFixed(1) + 'M' : a >= 1e3 ? (a / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'k' : String(Math.round(a * 10) / 10);
  return (v < 0 ? '−' : '') + (format === 'money' ? '$' : '') + s;
}

// Ordinal/cyclic dims carry a sort prefix ("01 Jan", "2 $100-249") that we hide for display.
function dimText(v, kind) {
  if (v === null || v === undefined || v === '') return '(blank)';
  if ((kind === 'cyclic' || kind === 'ordinal') && typeof v === 'string') return v.replace(/^\d+ /, '');
  return String(v);
}

// ---------- state ----------

let catalog = null;
let result = null;
let chart = null;
let tableSort = null;
let activeId = null;

const blankState = () => ({
  title: 'Untitled report', description: '',
  spec: { dataset: 'work_orders', dimensions: ['year'], metrics: ['jobs', 'sales'], filters: [],
          date_from: null, date_to: null, include_void: false, limit: 500, range: 'all' },
  chart: { type: 'auto', metric: null },
});
let state = blankState();

const ds = () => catalog.datasets[state.spec.dataset];

function encodeState(s) {
  return btoa(unescape(encodeURIComponent(JSON.stringify(s)))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function decodeState(str) {
  return JSON.parse(decodeURIComponent(escape(atob(str.replace(/-/g, '+').replace(/_/g, '/')))));
}

// ---------- sidebar ----------

function renderSidebar() {
  const saved = store.get('lsr.saved', []);
  const my = $('#myReports');
  my.replaceChildren();
  if (saved.length) {
    my.append(el('div', { class: 'side-title' }, 'My reports'));
    for (const r of saved) {
      my.append(el('button', {
        class: 'side-item' + (activeId === r.id ? ' active' : ''),
        onclick: () => { activeId = r.id; loadState(structuredClone(r.state)); },
      }, el('span', { class: 'grow' }, r.title), el('span', {
        class: 'x', title: 'Delete', role: 'button', 'aria-label': `Delete ${r.title}`,
        onclick: (e) => {
          e.stopPropagation();
          if (!confirm(`Delete "${r.title}"?`)) return;
          store.set('lsr.saved', store.get('lsr.saved', []).filter((x) => x.id !== r.id));
          renderSidebar();
        },
      }, '×')));
    }
  }
  const list = $('#presetList');
  list.replaceChildren();
  const groups = {};
  for (const p of catalog.presets) (groups[p.group] ||= []).push(p);
  for (const [g, items] of Object.entries(groups)) {
    list.append(el('div', { class: 'side-title' }, g));
    for (const p of items) {
      list.append(el('button', {
        class: 'side-item' + (activeId === p.id ? ' active' : ''), title: p.description,
        onclick: () => loadPreset(p.id),
      }, p.title));
    }
  }
}

function loadPreset(id) {
  const p = catalog.presets.find((x) => x.id === id);
  if (!p) return;
  activeId = id;
  const base = blankState();
  loadState({
    title: p.title, description: p.description,
    spec: { ...base.spec, filters: [], ...structuredClone(p.spec), range: p.spec.date_from ? 'custom' : 'all' },
    chart: { type: p.chart || 'auto', metric: null },
  });
}

function loadState(s) {
  state = s;
  state.spec.filters ||= [];
  tableSort = null;
  renderBuilder();
  renderSidebar();
  run();
}

// ---------- builder ----------

function chipList(container, selected, options, onChange, { reorder = false } = {}) {
  container.replaceChildren();
  selected.forEach((key, i) => {
    const kids = [el('span', {}, options[key]?.label ?? key)];
    if (reorder && i > 0) {
      kids.push(el('button', {
        class: 'move', title: 'Move left', 'aria-label': 'Move left',
        onclick: () => { const a = [...selected]; [a[i - 1], a[i]] = [a[i], a[i - 1]]; onChange(a); },
      }, '←'));
    }
    kids.push(el('button', {
      title: 'Remove', 'aria-label': `Remove ${options[key]?.label ?? key}`,
      onclick: () => onChange(selected.filter((k) => k !== key)),
    }, '×'));
    container.append(el('span', { class: 'chip' }, kids));
  });
  const add = el('select', { 'aria-label': 'Add' }, el('option', { value: '' }, '+ Add'));
  for (const [k, v] of Object.entries(options)) {
    if (!selected.includes(k)) add.append(el('option', { value: k }, v.label));
  }
  add.addEventListener('change', () => add.value && onChange([...selected, add.value]));
  container.append(add);
}

function renderBuilder() {
  const d = ds();
  $('#reportTitle').value = state.title;
  $('#reportDesc').textContent = state.description || '';

  const dsSel = $('#dataset');
  dsSel.replaceChildren(...Object.entries(catalog.datasets).map(([k, v]) =>
    el('option', { value: k, selected: k === state.spec.dataset }, v.label)));
  dsSel.title = d.description;

  chipList($('#dimChips'), state.spec.dimensions, d.dimensions, (v) => { state.spec.dimensions = v; changed(); },
    { reorder: true });
  chipList($('#metChips'), state.spec.metrics, d.metrics, (v) => { state.spec.metrics = v; changed(); });

  $('#range').value = state.spec.range || (state.spec.date_from || state.spec.date_to ? 'custom' : 'all');
  $('#dateFrom').value = state.spec.date_from || '';
  $('#dateTo').value = state.spec.date_to || '';
  $('#limit').value = state.spec.limit || 500;
  $('#includeVoid').checked = !!state.spec.include_void;
  renderFilters();
}

const OPS = [['in', 'is any of'], ['not_in', 'is not'], ['contains', 'contains'], ['gte', '≥'], ['lte', '≤'],
             ['gt', '>'], ['lt', '<']];

function renderFilters() {
  const box = $('#filters');
  box.replaceChildren();
  const d = ds();
  state.spec.filters.forEach((f, i) => {
    const dim = d.dimensions[f.field] || {};
    const field = el('select', { 'aria-label': 'Filter field' },
      Object.entries(d.dimensions).map(([k, v]) => el('option', { value: k, selected: k === f.field }, v.label)));
    field.addEventListener('change', () => { f.field = field.value; f.value = f.op === 'in' || f.op === 'not_in' ? [] : ''; renderFilters(); changed(); });
    const op = el('select', { 'aria-label': 'Filter operator' },
      OPS.map(([k, label]) => el('option', { value: k, selected: k === f.op }, label)));
    op.addEventListener('change', () => {
      const multi = (o) => o === 'in' || o === 'not_in';
      if (multi(op.value) !== multi(f.op)) f.value = multi(op.value) ? [] : '';
      f.op = op.value; renderFilters(); changed();
    });

    const vals = el('div', { class: 'vals' });
    const listId = `vals-${i}`;
    const datalist = el('datalist', { id: listId });
    const loadSuggestions = async (q) => {
      try {
        const vs = await api(`/api/values?dataset=${state.spec.dataset}&dim=${f.field}` + (q ? `&q=${encodeURIComponent(q)}` : ''));
        datalist.replaceChildren(...vs.map((v) => el('option', { value: v.value }, `${dimText(v.value, dim.kind)} (${v.count})`)));
      } catch { /* suggestions are optional */ }
    };
    if (f.op === 'in' || f.op === 'not_in') {
      for (const v of f.value || []) {
        vals.append(el('span', { class: 'chip' }, dimText(v, dim.kind), el('button', {
          'aria-label': `Remove ${v}`, onclick: () => { f.value = f.value.filter((x) => x !== v); renderFilters(); changed(); },
        }, '×')));
      }
      const input = el('input', { type: 'text', list: listId, placeholder: 'type or pick…', 'aria-label': 'Filter value' });
      const commit = () => {
        const v = input.value.trim();
        if (!v) return;
        const opt = [...datalist.options].find((o) => o.value === v || o.textContent.startsWith(v + ' ('));
        f.value = [...(f.value || []), opt ? opt.value : v];
        renderFilters(); changed();
      };
      input.addEventListener('change', commit);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); commit(); } });
      input.addEventListener('input', () => { clearTimeout(input.t); input.t = setTimeout(() => loadSuggestions(input.value), 200); });
      vals.append(input, datalist);
      loadSuggestions();
    } else {
      const isDate = dim.kind === 'date';
      const input = el('input', {
        type: isDate && f.value !== 'today' ? 'date' : 'text', value: f.value ?? '', 'aria-label': 'Filter value',
        placeholder: isDate ? 'YYYY-MM-DD or today' : 'value',
      });
      input.addEventListener('change', () => { f.value = input.value; changed(); });
      vals.append(input);
    }
    const remove = el('button', {
      class: 'btn small', 'aria-label': 'Remove filter',
      onclick: () => { state.spec.filters.splice(i, 1); renderFilters(); changed(); },
    }, '×');
    box.append(el('div', { class: 'filter-row' }, field, op, vals, remove));
  });
}

function applyRange(range) {
  const today = new Date();
  const iso = (d) => d.toISOString().slice(0, 10);
  const y = today.getFullYear();
  const map = {
    all: [null, null],
    ytd: [`${y}-01-01`, null],
    thisyear: [`${y}-01-01`, `${y}-12-31`],
    lastyear: [`${y - 1}-01-01`, `${y - 1}-12-31`],
    last12: [iso(new Date(y - 1, today.getMonth(), today.getDate() + 1)), null],
  };
  state.spec.range = range;
  if (map[range]) [state.spec.date_from, state.spec.date_to] = map[range];
  $('#dateFrom').value = state.spec.date_from || '';
  $('#dateTo').value = state.spec.date_to || '';
}

let runTimer;
function changed() {
  activeId = activeId && catalog.presets.some((p) => p.id === activeId) ? null : activeId;
  renderBuilder();
  renderSidebar();
  clearTimeout(runTimer);
  runTimer = setTimeout(run, 250);
}

function wireBuilder() {
  $('#dataset').addEventListener('change', (e) => {
    const next = catalog.datasets[e.target.value];
    state.spec.dataset = e.target.value;
    state.spec.dimensions = state.spec.dimensions.filter((d) => d in next.dimensions);
    state.spec.metrics = state.spec.metrics.filter((m) => m in next.metrics);
    if (!state.spec.metrics.length) state.spec.metrics = [Object.keys(next.metrics)[0]];
    state.spec.filters = state.spec.filters.filter((f) => f.field in next.dimensions);
    changed();
  });
  $('#range').addEventListener('change', (e) => { applyRange(e.target.value); changed(); });
  $('#dateFrom').addEventListener('change', (e) => { state.spec.date_from = e.target.value || null; state.spec.range = 'custom'; changed(); });
  $('#dateTo').addEventListener('change', (e) => { state.spec.date_to = e.target.value || null; state.spec.range = 'custom'; changed(); });
  $('#limit').addEventListener('change', (e) => { state.spec.limit = Math.max(1, Math.min(5000, +e.target.value || 500)); changed(); });
  $('#includeVoid').addEventListener('change', (e) => { state.spec.include_void = e.target.checked; changed(); });
  $('#addFilter').addEventListener('click', () => {
    const first = state.spec.dimensions[0] || 'status';
    state.spec.filters.push({ field: first, op: 'in', value: [] });
    renderFilters();
  });
  $('#reportTitle').addEventListener('input', (e) => { state.title = e.target.value; syncHash(); });
  $('#chartType').addEventListener('change', (e) => { state.chart.type = e.target.value; renderChart(); syncHash(); });
  $('#chartMetric').addEventListener('change', (e) => { state.chart.metric = e.target.value; renderChart(); syncHash(); });
  $('#newReport').addEventListener('click', () => { activeId = null; loadState(blankState()); });

  $('#saveBtn').addEventListener('click', () => {
    const saved = store.get('lsr.saved', []);
    const existing = saved.find((r) => r.id === activeId);
    if (existing) { existing.title = state.title; existing.state = structuredClone(state); }
    else {
      activeId = 's' + Date.now();
      saved.push({ id: activeId, title: state.title, state: structuredClone(state) });
    }
    store.set('lsr.saved', saved);
    renderSidebar();
    toast('Saved to this browser');
  });
  $('#shareBtn').addEventListener('click', async () => {
    syncHash();
    try { await navigator.clipboard.writeText(location.href); toast('Link copied'); }
    catch { toast('Copy the address bar to share'); }
  });
  $('#csvBtn').addEventListener('click', () => {
    if (!result) return;
    const header = result.columns.map((c) => c.label);
    const rows = result.rows.map((r) => r.map((v, i) => {
      const c = result.columns[i];
      return c.role === 'dimension' ? dimText(v, c.kind) : v;
    }));
    downloadCsv(state.title, header, rows);
  });
}

function syncHash() {
  history.replaceState(null, '', '#r=' + encodeState(state));
}

// ---------- running ----------

let runSeq = 0;
async function run() {
  syncHash();
  const seq = ++runSeq;
  const { range, ...spec } = state.spec;
  try {
    const res = await api('/api/report', spec);
    if (seq !== runSeq) return;
    result = res;
    $('#error').hidden = true;
  } catch (e) {
    if (seq !== runSeq) return;
    result = null;
    $('#error').textContent = e.message;
    $('#error').hidden = false;
  }
  renderKpis();
  renderChart();
  renderTable();
}

function renderKpis() {
  const box = $('#kpis');
  box.replaceChildren();
  if (!result || !result.totals) return;
  for (const c of result.columns.filter((c) => c.role === 'metric')) {
    box.append(el('div', { class: 'kpi' },
      el('div', { class: 'label' }, c.label),
      el('div', { class: 'value' }, fmt(result.totals[c.key], c.format))));
  }
}

// ---------- chart ----------

const SERIES = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6', '--s7', '--s8'];
const MAX_SERIES = 7; // + "Other"
const seriesSlots = new Map(); // color follows the entity across re-runs, never its rank

function slotFor(key) {
  if (!seriesSlots.has(key)) {
    const used = new Set(seriesSlots.values());
    const free = SERIES.findIndex((_, i) => !used.has(i));
    seriesSlots.set(key, free === -1 ? seriesSlots.size % SERIES.length : free);
  }
  return seriesSlots.get(key);
}

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function renderChart() {
  const card = $('#chartCard');
  const note = $('#chartNote');
  note.textContent = '';
  if (chart) { chart.destroy(); chart = null; }
  if (!result) { card.hidden = true; return; }
  const dims = result.columns.filter((c) => c.role === 'dimension');
  const mets = result.columns.filter((c) => c.role === 'metric');

  const metSel = $('#chartMetric');
  metSel.replaceChildren(...mets.map((m) => el('option', { value: m.key }, m.label)));
  const met = mets.find((m) => m.key === state.chart.metric) || mets[0];
  metSel.value = met.key;
  $('#chartType').value = state.chart.type;

  if (state.chart.type === 'table' || dims.length === 0 || dims.length > 2 || !result.rows.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;

  const d0 = dims[0];
  const d0i = result.columns.indexOf(d0);
  const mi = result.columns.indexOf(met);
  const timeish = d0.kind === 'time' || d0.kind === 'date';
  const type = state.chart.type === 'auto' ? (timeish ? 'line' : 'bar') : state.chart.type;
  const additive = met.format !== 'pct' && !/^avg/.test(met.key);
  const horizontal = type === 'bar' && ['category', 'text', 'id'].includes(d0.kind);
  const cap = horizontal ? 40 : 400;

  let labels = [];
  let datasets = [];
  if (dims.length === 1) {
    let rows = result.rows;
    if (rows.length > cap) { rows = rows.slice(0, cap); note.textContent = `Chart shows the first ${cap} rows; the table has all ${result.rows.length}.`; }
    labels = rows.map((r) => dimText(r[d0i], d0.kind));
    datasets = [{ key: '__single', label: met.label, data: rows.map((r) => r[mi]) }];
  } else {
    const d1 = dims[1];
    const d1i = result.columns.indexOf(d1);
    const allX = [...new Set(result.rows.map((r) => r[d0i]))];
    const xKeys = allX.slice(0, cap);
    labels = xKeys.map((k) => dimText(k, d0.kind));
    const totals = new Map();
    for (const r of result.rows) totals.set(r[d1i], (totals.get(r[d1i]) || 0) + Math.abs(r[mi] || 0));
    const ranked = [...totals.keys()].sort((a, b) => totals.get(b) - totals.get(a));
    const keep = new Set(ranked.slice(0, MAX_SERIES));
    const cells = new Map();
    for (const r of result.rows) {
      const s = keep.has(r[d1i]) ? r[d1i] : '__other';
      if (s === '__other' && !additive) continue;
      const k = s + '\u0000' + r[d0i];
      cells.set(k, (cells.get(k) || 0) + (r[mi] || 0));
    }
    const seriesKeys = [...keep];
    if (ranked.length > MAX_SERIES) {
      if (additive) seriesKeys.push('__other');
      note.textContent = additive
        ? `${ranked.length - MAX_SERIES} smaller ${d1.label.toLowerCase()} values are combined into "Other".`
        : `Showing the top ${MAX_SERIES} ${d1.label.toLowerCase()} values; see the table for the rest.`;
    }
    if (allX.length > cap) note.textContent += ` Chart shows the first ${cap} ${d0.label.toLowerCase()} values.`;
    datasets = seriesKeys.map((s) => ({
      key: s, label: s === '__other' ? 'Other' : dimText(s, d1.kind),
      data: xKeys.map((x) => cells.get(s + '\u0000' + x) ?? null),
    }));
  }

  const surface = css('--surface');
  const single = datasets.length === 1;
  const colorOf = (key) => key === '__other' ? css('--other') : css(SERIES[single ? 0 : slotFor(`${state.spec.dimensions[1]}:${key}`)]);
  const stacked = type === 'bar' && !single && additive;

  const wrap = $('#chartWrap');
  wrap.style.height = horizontal ? `${Math.max(240, labels.length * 26 + 60)}px` : '360px';
  $('#chartTitle').textContent = `${met.label} by ${d0.label.toLowerCase()}` + (dims[1] ? ` and ${dims[1].label.toLowerCase()}` : '');

  const grid = css('--grid');
  const muted = css('--muted');
  const valueAxis = {
    beginAtZero: true, stacked, grid: { color: grid }, border: { display: false },
    ticks: { color: muted, callback: (v) => fmtShort(v, met.format), maxTicksLimit: 6 },
  };
  const catAxis = {
    stacked, grid: { display: false }, border: { color: css('--axis') },
    ticks: { color: muted, autoSkip: !horizontal, maxRotation: 0, autoSkipPadding: 12 },
  };

  chart = new Chart($('#chart'), {
    type,
    data: {
      labels,
      datasets: datasets.map((d) => {
        const c = colorOf(d.key);
        return type === 'line'
          ? { label: d.label, data: d.data, borderColor: c, backgroundColor: c, borderWidth: 2, tension: 0,
              pointRadius: labels.length > 40 ? 0 : 3, pointHoverRadius: 5, pointBorderColor: surface,
              pointBorderWidth: 2, spanGaps: true }
          : { label: d.label, data: d.data, backgroundColor: c, borderColor: surface, borderWidth: stacked ? 1 : 0,
              borderRadius: 4, borderSkipped: 'start', maxBarThickness: 36, categoryPercentage: 0.8, barPercentage: 0.9 };
      }),
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      indexAxis: horizontal ? 'y' : 'x',
      interaction: { mode: 'index', intersect: false, axis: horizontal ? 'y' : 'x' },
      scales: horizontal ? { x: valueAxis, y: catAxis } : { x: catAxis, y: valueAxis },
      plugins: {
        legend: { display: !single, position: 'top', align: 'start',
                  labels: { color: css('--ink-2'), boxWidth: 12, boxHeight: 12, usePointStyle: true, pointStyle: 'rectRounded' } },
        tooltip: {
          backgroundColor: css('--ink'), titleColor: css('--page'), bodyColor: css('--page'), padding: 10,
          cornerRadius: 8, boxPadding: 4, filter: (item) => item.raw !== null,
          callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmt(ctx.raw, met.format)}` },
        },
      },
    },
  });
}

// ---------- table ----------

function renderTable() {
  const table = $('#table');
  table.replaceChildren();
  $('#rowCount').textContent = '';
  $('#sqlPeek').textContent = result ? result.sql + (result.params.length ? `\n-- params: ${JSON.stringify(result.params)}` : '') : '';
  if (!result) return;
  const cols = result.columns;
  let rows = result.rows;
  if (tableSort) {
    const i = tableSort.i;
    rows = [...rows].sort((a, b) => {
      const x = a[i], y = b[i];
      if (x === y) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return (x < y ? -1 : 1) * tableSort.dir;
    });
  }
  $('#rowCount').textContent = `· ${result.rows.length.toLocaleString()} rows` + (result.truncated ? ' (limit reached — raise Max rows)' : '');
  const isNum = (c) => c.role === 'metric' || c.kind === 'id';
  table.append(el('thead', {}, el('tr', {}, cols.map((c, i) => el('th', {
    class: isNum(c) ? 'num' : '', scope: 'col', 'aria-sort': tableSort?.i === i ? (tableSort.dir > 0 ? 'ascending' : 'descending') : 'none',
    onclick: () => { tableSort = { i, dir: tableSort?.i === i ? -tableSort.dir : (c.role === 'metric' ? -1 : 1) }; renderTable(); },
  }, c.label, tableSort?.i === i ? (tableSort.dir > 0 ? ' ↑' : ' ↓') : '')))));
  const shown = rows.slice(0, 2000);
  table.append(el('tbody', {}, shown.map((r) => el('tr', {}, r.map((v, i) => {
    const c = cols[i];
    return el('td', { class: isNum(c) ? 'num' : c.kind === 'text' ? 'text' : '' },
      c.role === 'metric' ? fmt(v, c.format) : dimText(v, c.kind));
  })))));
  if (cols.some((c) => c.role === 'dimension') && result.totals) {
    table.append(el('tfoot', {}, el('tr', {}, cols.map((c, i) => el('td', { class: isNum(c) ? 'num' : '' },
      c.role === 'metric' ? fmt(result.totals[c.key], c.format) : i === 0 ? 'Total' : '')))));
  }
}

function downloadCsv(name, header, rows) {
  const esc = (v) => {
    if (v === null || v === undefined) return '';
    const s = String(v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const text = [header, ...rows].map((r) => r.map(esc).join(',')).join('\r\n');
  const a = el('a', {
    href: URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv' })),
    download: (name || 'report').replace(/[^\w-]+/g, '_') + '.csv',
  });
  document.body.append(a);
  a.click();
  a.remove();
}

// ---------- SQL console ----------

const SQL_EXAMPLES = {
  'Repeat customers': `SELECT customer_name, COUNT(*) AS jobs, ROUND(SUM(subtotal)) AS sales,
  MIN(order_date) AS first_order, MAX(order_date) AS last_order
FROM wo_facts WHERE status != 'Void'
GROUP BY cust_no HAVING jobs >= 5 ORDER BY sales DESC LIMIT 100;`,
  'Lapsed customers (no order in 2 years)': `SELECT customer_name, customer_city, COUNT(*) AS jobs, ROUND(SUM(subtotal)) AS lifetime_sales,
  MAX(order_date) AS last_order
FROM wo_facts WHERE status != 'Void'
GROUP BY cust_no HAVING last_order < date('now', '-2 years') AND jobs >= 3
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

let sqlResult = null;

async function loadSchema() {
  const box = $('#schemaList');
  if (box.childElementCount) return;
  try {
    const schema = await api('/api/schema');
    for (const t of schema) {
      box.append(el('details', { class: 'schema', open: ['wo_facts', 'comp_facts'].includes(t.name) || undefined },
        el('summary', {}, t.name, el('span', { class: 'tag' }, t.type)),
        el('div', { class: 'cols' }, t.columns.map((c) => el('button', {
          title: 'Insert', onclick: () => insertAtCursor($('#sqlInput'), c),
        }, c)))));
    }
  } catch (e) { box.textContent = e.message; }
}

function insertAtCursor(ta, text) {
  const { selectionStart: s, selectionEnd: e, value } = ta;
  ta.value = value.slice(0, s) + text + value.slice(e);
  ta.focus();
  ta.selectionStart = ta.selectionEnd = s + text.length;
}

async function runSql() {
  const err = $('#sqlError');
  try {
    sqlResult = await api('/api/sql', { sql: $('#sqlInput').value });
    err.hidden = true;
  } catch (e) {
    sqlResult = null;
    err.textContent = e.message;
    err.hidden = false;
  }
  const table = $('#sqlTable');
  table.replaceChildren();
  $('#sqlCount').textContent = sqlResult ? `· ${sqlResult.rows.length.toLocaleString()} rows${sqlResult.truncated ? ' (first 5,000)' : ''}` : '';
  if (!sqlResult) return;
  const numeric = sqlResult.columns.map((_, i) => sqlResult.rows.every((r) => r[i] === null || typeof r[i] === 'number'));
  table.append(el('thead', {}, el('tr', {}, sqlResult.columns.map((c, i) => el('th', { class: numeric[i] ? 'num' : '' }, c)))));
  table.append(el('tbody', {}, sqlResult.rows.slice(0, 2000).map((r) => el('tr', {}, r.map((v, i) => el('td', {
    class: numeric[i] ? 'num' : (typeof v === 'string' && v.length > 60 ? 'text' : ''),
  }, v === null ? '—' : typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v)))))));
}

function wireSql() {
  const ex = $('#sqlExamples');
  for (const k of Object.keys(SQL_EXAMPLES)) ex.append(el('option', { value: k }, k));
  ex.addEventListener('change', () => { if (ex.value) { $('#sqlInput').value = SQL_EXAMPLES[ex.value]; ex.value = ''; runSql(); } });
  $('#runSql').addEventListener('click', runSql);
  $('#sqlInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); runSql(); }
  });
  $('#sqlCsv').addEventListener('click', () => sqlResult && downloadCsv('query', sqlResult.columns, sqlResult.rows));
}

// ---------- tabs, theme, boot ----------

function showTab(name) {
  document.querySelectorAll('.tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  $('#tab-reports').hidden = name !== 'reports';
  $('#tab-sql').hidden = name !== 'sql';
  if (name === 'sql') loadSchema();
}

function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
  if (result) renderChart();
}

async function boot() {
  applyTheme(store.get('lsr.theme', null));
  $('#themeBtn').addEventListener('click', () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === 'dark'
      : matchMedia('(prefers-color-scheme: dark)').matches;
    const next = dark ? 'light' : 'dark';
    store.set('lsr.theme', next);
    applyTheme(next);
  });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => result && renderChart());
  document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));
  wireBuilder();
  wireSql();

  api('/api/me').then((me) => {
    if (me.username === 'dev') return;
    $('#userMenu').replaceChildren(el('span', {}, me.name), el('button', {
      class: 'btn small', onclick: async () => {
        await fetch('/api/logout', { method: 'POST' });
        location.replace('/login.html');
      },
    }, 'Sign out'));
  }).catch(() => {});

  try {
    catalog = await api('/api/catalog');
  } catch (e) {
    $('#error').textContent = `Could not load the report catalog: ${e.message}`;
    $('#error').hidden = false;
    return;
  }
  const info = catalog.data;
  $('#dataInfo').textContent = `${info.work_orders.toLocaleString()} work orders · latest ${info.latest_order_date}`;

  const hash = location.hash.slice(1);
  const params = new URLSearchParams(hash);
  if (params.get('r')) {
    try { loadState(decodeState(params.get('r'))); return; } catch { /* fall through to default */ }
  }
  loadPreset(params.get('preset') || 'yearly_summary');
}

boot();
