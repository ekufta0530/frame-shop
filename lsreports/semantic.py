"""Semantic layer: named dimensions/metrics over wo_facts and comp_facts.

A report spec is plain JSON so the web UI and the MCP server can share it:
  {"dataset": "work_orders", "dimensions": ["year"], "metrics": ["jobs", "sales"],
   "date_from": "2020-01-01", "date_to": null, "include_void": false,
   "filters": [{"field": "salesperson", "op": "in", "value": ["ECP"]}],
   "sort": {"field": "sales", "dir": "desc"}, "limit": 100}
Only whitelisted names reach SQL; all values are bound parameters.

Not every database has every field. A report runs at one *level*:
  full    -- an on-prem store's lifesaver.sqlite: everything below
  basic   -- a cloud store's reports DB (lsreports.build): only what the Work Order List
             report carries -- no salesperson, components, cost, tax or turnaround
  pooled  -- all stores in one table: the basic fields plus a "store" dimension
Each dimension, metric and dataset lists the levels that have it; asking for one a level
lacks is a SpecError that says so.
"""
import datetime as dt

from . import db

D = 'order_date'

FULL, BASIC, POOLED = 'full', 'basic', 'pooled'
LEVELS = (FULL, BASIC, POOLED)
ANY = frozenset(LEVELS)
ONLY_FULL = frozenset({FULL})
LEVEL_NOTES = {
    BASIC: 'cloud stores only have the Work Order List report (no salesperson, components, cost, '
           'tax, customer address or turnaround)',
    POOLED: 'the all-stores view only has the fields every store has',
}
MONTHS = 'JanFebMarAprMayJunJulAugSepOctNovDec'


def _dim(label, sql, kind='category', levels=ONLY_FULL):
    return {'label': label, 'sql': sql, 'kind': kind, 'levels': levels}


def _met(label, sql, fmt, levels=ONLY_FULL):
    return {'label': label, 'sql': sql, 'format': fmt, 'levels': levels}


COMMON_DIMS = {
    'year': _dim('Year', f"strftime('%Y', {D})", 'time', levels=ANY),
    'quarter': _dim('Quarter', f"strftime('%Y', {D}) || '-Q' || ((CAST(strftime('%m', {D}) AS INT) + 2) / 3)", 'time', levels=ANY),
    'month': _dim('Month', f"strftime('%Y-%m', {D})", 'time', levels=ANY),
    'month_of_year': _dim('Month of year', f"strftime('%m', {D}) || ' ' || "
                          f"substr('{MONTHS}', CAST(strftime('%m', {D}) AS INT) * 3 - 2, 3)", 'cyclic', levels=ANY),
    'weekday': _dim('Weekday', f"strftime('%w', {D}) || ' ' || "
                    f"substr('SunMonTueWedThuFriSat', CAST(strftime('%w', {D}) AS INT) * 3 + 1, 3)", 'cyclic', levels=ANY),
    'order_date': _dim('Order date', D, 'date', levels=ANY),
    'promised_date': _dim('Promised date', 'promised_date', 'date', levels=ANY),
    'done_date': _dim('Done date', 'done_date', 'date'),
    'salesperson': _dim('Salesperson', 'salesperson'),
    'status': _dim('Status', 'status', levels=ANY),
    'customer_type': _dim('New vs returning', 'customer_type', levels=ANY),
    'customer_name': _dim('Customer', 'customer_name', 'text', levels=ANY),
    'company': _dim('Company', "coalesce(company, '')", 'text'),
    'customer_city': _dim('Customer city', "coalesce(customer_city, '')"),
    'customer_zip': _dim('Customer ZIP', "coalesce(customer_zip, '')"),
    'price_band': _dim('Ticket size', "CASE WHEN subtotal < 100 THEN '1 Under $100' "
                       "WHEN subtotal < 250 THEN '2 $100-249' WHEN subtotal < 500 THEN '3 $250-499' "
                       "WHEN subtotal < 1000 THEN '4 $500-999' ELSE '5 $1,000+' END", 'ordinal', levels=ANY),
    'turnaround_band': _dim('Turnaround', "CASE WHEN days_to_done IS NULL THEN '9 Not done' "
                            "WHEN days_to_done <= 7 THEN '1 0-7 days' WHEN days_to_done <= 14 THEN '2 8-14 days' "
                            "WHEN days_to_done <= 30 THEN '3 15-30 days' WHEN days_to_done <= 60 THEN '4 31-60 days' "
                            "ELSE '5 60+ days' END", 'ordinal'),
    'discount_code': _dim('Discount code', "coalesce(discount_code, '(none)')"),
    'wo_no': _dim('Work order #', 'wo_no', 'id', levels=ANY),
    'invoice_no': _dim('Invoice #', 'invoice_no', 'id', levels=ANY),
    'cust_no': _dim('Customer #', 'cust_no', 'id', levels=ANY),
    'description': _dim('Description', 'description', 'text', levels=ANY),
}

DATASETS = {
    'work_orders': {
        'label': 'Work orders',
        'table': 'wo_facts',
        'description': 'One row per work order (latest revision). Money is pre-tax unless noted. '
                       'Voided work orders are excluded unless include_void is true.',
        'levels': ANY,
        'dimensions': {'store': _dim('Store', 'store', levels=frozenset({POOLED})), **COMMON_DIMS},
        'metrics': {
            'jobs': _met('Jobs', 'COUNT(*)', 'int', ANY),
            'sales': _met('Sales', 'SUM(subtotal)', 'money', ANY),
            'avg_ticket': _met('Avg ticket', 'AVG(subtotal)', 'money', ANY),
            'total_with_tax': _met('Total incl. tax', 'SUM(total)', 'money'),
            # only moulding/mat/fillet carry a real cost in LifeSaver (see sqlite_extract/extract.py at the repo root)
            'known_cost': _met('Moulding & mat cost', 'SUM(known_cost)', 'money'),
            'mm_margin_pct': _met('Moulding & mat margin %', '100.0 * SUM(known_cost_sales - known_cost) '
                                  '/ NULLIF(SUM(known_cost_sales), 0)', 'pct'),
            'discounts': _met('Discounts', 'SUM(discount_amt)', 'money', ANY),
            'discount_pct': _met('Discount %', '100.0 * SUM(discount_amt) / NULLIF(SUM(list_subtotal), 0)', 'pct', ANY),
            'avg_days_to_done': _met('Avg days to done', 'AVG(days_to_done)', 'num1'),
            'customers': _met('Customers', 'COUNT(DISTINCT cust_no)', 'int', ANY),
            'moulding_sales': _met('Moulding sales', 'SUM(moulding_price)', 'money'),
            'mat_sales': _met('Mat sales', 'SUM(mat_price)', 'money'),
            'glass_sales': _met('Glazing sales', 'SUM(glass_price)', 'money'),
            'labor_sales': _met('Labor sales', 'SUM(labor_price)', 'money'),
        },
    },
    'components': {
        'label': 'Components',
        'table': 'comp_facts',
        'description': 'One row per material/service on a work order (moulding, mat, glazing, mounting, '
                       'stretching, fillet, special). Prices are net of discount. Cost/margin exist only '
                       'for moulding, mat and fillet; LifeSaver does not record true cost for the rest.',
        'levels': ONLY_FULL,
        'dimensions': {
            'comp_kind': _dim('Component type', 'comp_kind'),
            'comp_vendor': _dim('Vendor', 'comp_vendor'),
            'comp_name': _dim('Item', 'comp_name', 'text'),
            **COMMON_DIMS,
        },
        'metrics': {
            'lines': _met('Uses', 'COUNT(*)', 'int'),
            'jobs': _met('Jobs', 'COUNT(DISTINCT wo_no)', 'int'),
            'net_sales': _met('Net sales', 'SUM(comp_net_price)', 'money'),
            # cost is NULL except for moulding/mat/fillet, so margins cover only those
            'cost': _met('Cost', 'SUM(comp_cost)', 'money'),
            'margin': _met('Margin', 'SUM(comp_net_price - comp_cost)', 'money'),
            'margin_pct': _met('Margin %', '100.0 * SUM(comp_net_price - comp_cost) / '
                               'NULLIF(SUM(CASE WHEN comp_cost IS NOT NULL THEN comp_net_price END), 0)', 'pct'),
            'avg_price': _met('Avg price', 'AVG(comp_net_price)', 'money'),
            'feet': _met('Feet (moulding)', 'SUM(comp_feet)', 'num1'),
        },
    },
}

OPS = {'in': 'IN', 'not_in': 'NOT IN', 'eq': '=', 'neq': '!=', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<=',
       'contains': 'LIKE'}
MAX_LIMIT = 5000


class SpecError(ValueError):
    pass


def catalog(level=FULL):
    """JSON-safe description of the datasets a ``level`` has, for UIs and LLM tools."""
    return {name: {'label': ds['label'], 'description': ds['description'],
                   'dimensions': {k: {'label': v['label'], 'kind': v['kind']}
                                  for k, v in ds['dimensions'].items() if level in v['levels']},
                   'metrics': {k: {'label': v['label'], 'format': v['format']}
                               for k, v in ds['metrics'].items() if level in v['levels']}}
            for name, ds in DATASETS.items() if level in ds['levels']}


def _missing(what, key, level):
    note = LEVEL_NOTES.get(level)
    return SpecError(f'{what} {key!r} is not available here' + (f': {note}' if note else ''))


def _dataset(name, level):
    ds = DATASETS.get(name)
    if not ds:
        raise SpecError(f'unknown dataset {name!r}')
    if level not in ds['levels']:
        raise _missing('dataset', name, level)
    return ds


def _field(ds, kind, key, level):
    """The dimension or metric ``key``, if this level has it."""
    f = ds[kind].get(key)
    if f is None:
        raise SpecError(f'unknown {kind[:-1]} {key!r}')
    if level not in f['levels']:
        raise _missing(kind[:-1], key, level)
    return f


def fits(spec, level):
    """Whether a report spec only uses what ``level`` has (used to offer presets)."""
    try:
        ds = _dataset(spec.get('dataset', 'work_orders'), level)
        for d in spec.get('dimensions') or []:
            _field(ds, 'dimensions', d, level)
        for m in spec.get('metrics') or []:
            _field(ds, 'metrics', m, level)
        for f in spec.get('filters') or []:
            _field(ds, 'dimensions', f.get('field'), level)
    except SpecError:
        return False
    return True


def _resolve(value):
    if value == 'today':
        return dt.date.today().isoformat()
    return value


def _where(ds, spec, level):
    clauses, params = [], []
    if not spec.get('include_void'):
        clauses.append("status != 'Void'")
    if spec.get('date_from'):
        clauses.append(f'{D} >= ?')
        params.append(spec['date_from'])
    if spec.get('date_to'):
        clauses.append(f'{D} <= ?')
        params.append(spec['date_to'])
    for f in spec.get('filters') or []:
        op = OPS.get(f.get('op', 'in'))
        if not op:
            raise SpecError(f'bad filter: {f}')
        dim = _field(ds, 'dimensions', f.get('field'), level)
        val = f.get('value')
        if op in ('IN', 'NOT IN'):
            vals = val if isinstance(val, list) else [val]
            if not vals:
                continue
            clauses.append(f"{dim['sql']} {op} ({','.join('?' * len(vals))})")
            params += vals
        elif op == 'LIKE':
            clauses.append(f"{dim['sql']} LIKE ?")
            params.append(f'%{val}%')
        else:
            clauses.append(f"{dim['sql']} {op} ?")
            params.append(_resolve(val))
    return (' WHERE ' + ' AND '.join(clauses)) if clauses else '', params


def run_report(spec, level=FULL):
    ds = _dataset(spec.get('dataset', 'work_orders'), level)
    dims = spec.get('dimensions') or []
    mets = spec.get('metrics') or []
    for d in dims:
        _field(ds, 'dimensions', d, level)
    for m in mets:
        _field(ds, 'metrics', m, level)
    if not mets:
        raise SpecError('pick at least one metric')

    where, params = _where(ds, spec, level)
    select = [f"{ds['dimensions'][d]['sql']} AS \"{d}\"" for d in dims]
    select += [f"{ds['metrics'][m]['sql']} AS \"{m}\"" for m in mets]
    sql = f"SELECT {', '.join(select)} FROM {ds['table']}{where}"
    if dims:
        sql += ' GROUP BY ' + ', '.join(str(i + 1) for i in range(len(dims)))

    sort = spec.get('sort') or {}
    if sort.get('field') in dims + mets:
        order = f"\"{sort['field']}\" {'ASC' if sort.get('dir') == 'asc' else 'DESC'}"
    elif dims and ds['dimensions'][dims[0]]['kind'] in ('time', 'cyclic', 'ordinal', 'date'):
        order = ', '.join(f'"{d}"' for d in dims)
    else:
        order = f'"{mets[0]}" DESC'
    limit = max(1, min(int(spec.get('limit') or 500), MAX_LIMIT))
    if dims:
        sql += f' ORDER BY {order} LIMIT {limit + 1}'

    cols, rows = db.query(sql, params)
    truncated = len(rows) > limit
    rows = rows[:limit]
    totals = None
    if dims:
        _, t = db.query(f"SELECT {', '.join(ds['metrics'][m]['sql'] for m in mets)} FROM {ds['table']}{where}", params)
        totals = dict(zip(mets, t[0]))
    else:
        totals = dict(zip(mets, rows[0]))

    columns = [{'key': d, 'label': ds['dimensions'][d]['label'], 'role': 'dimension',
                'kind': ds['dimensions'][d]['kind']} for d in dims]
    columns += [{'key': m, 'label': ds['metrics'][m]['label'], 'role': 'metric',
                 'format': ds['metrics'][m]['format']} for m in mets]
    return {'columns': columns, 'rows': rows, 'totals': totals, 'truncated': truncated,
            'sql': sql, 'params': params}


def dimension_values(dataset, dim, search=None, limit=200, level=FULL):
    ds = _dataset(dataset, level)
    expr = _field(ds, 'dimensions', dim, level)['sql']
    where, params = f' WHERE {expr} IS NOT NULL', []
    if search:
        where += f' AND {expr} LIKE ?'
        params.append(f'%{search}%')
    _, rows = db.query(f"SELECT {expr} AS v, COUNT(*) n FROM {ds['table']}{where} "
                       f"GROUP BY 1 ORDER BY n DESC LIMIT {int(limit)}", params)
    return [{'value': r[0], 'count': r[1]} for r in rows]
