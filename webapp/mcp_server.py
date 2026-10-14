"""The MCP server for Claude (claude.ai custom connector, Claude Code, Claude Desktop): every store
in stores.toml, plus all of them pooled, answered from the same data the website shows.

It runs inside the website's process: ``McpMount`` routes /mcp to it and ``running()`` (entered by
the site's lifespan) starts it. Nothing here talks to lsscloud.com or the stores; it only reads the
data manager's current copies.

No OAuth: access needs MCP_TOKEN, either in the URL path (https://host/mcp/<token>, which is what a
claude.ai connector uses) or as "Authorization: Bearer <token>". Anything else gets a 404. On Cloud
Run without MCP_TOKEN the endpoint is off (404) and the site runs as usual; locally a random token is
printed instead.
"""
from __future__ import annotations

import contextlib
import functools
import hmac
import json
import logging
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from core.months import add_months, month_start
from core.stores import ALL_STORES
from lsreports import db, presets, semantic

from .data import manager
from .views import ViewConfig, store_view

log = logging.getLogger(__name__)

TOKEN = os.environ.get('MCP_TOKEN', '')
if not TOKEN and not os.environ.get('K_SERVICE'):  # K_SERVICE is set on Cloud Run
    TOKEN = secrets.token_urlsafe(24)
    log.warning('MCP_TOKEN not set; for this run the MCP endpoint is /mcp/%s', TOKEN)

SQL_MAX_ROWS = 2000

INSTRUCTIONS = """\
Sales and customer data for a group of picture-framing shops, all on LifeSaver POS.
Start with list_stores: it names each store, how current its data is, and what it can answer.
Every other tool takes a store id from there, or "all" for every store pooled together.

- get_kpis: the five retention KPIs on the website's dashboard (first -> second purchase, repeat share
  of revenue, median days to second, active customers, reactivation), month by month.
- get_catalog, then run_preset or run_report: sales, jobs, tickets, discounts and more, grouped any way.
  Use find_values for exact spellings before filtering. get_schema + run_sql only when those can't
  express the question.

Things to know:
- Stores come in two tiers. On-prem stores have the full LifeSaver database (salesperson, components,
  vendors, cost and margin, tax). Cloud stores have only the Work Order List report: work orders with
  customer, dates, status, list price and discount. get_catalog shows exactly what a store has.
- "all" has only what every store has, plus a store dimension. It is cut off at the oldest store's
  latest data, and customers are counted per store (no one is matched across stores).
- Money is pre-tax; sales are list price minus discount. Voided work orders are excluded by default.
- True cost exists only for moulding, mat and fillet, so margins cover only those components.
- Customer names, phones and addresses are personal data: only surface them when the question needs them.
"""

mcp = MCPServer('Frame Shop', instructions=INSTRUCTIONS)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

StoreId = Annotated[str, Field(description='A store id from list_stores, or "all" for every store pooled.')]
Dataset = Literal['work_orders', 'components']


def _round(x):
    """Floats to 2 decimals (4 below 1, so rates keep their precision): averages come out of SQLite as
    140.04524288107203, which is just noise here."""
    if isinstance(x, float):
        return round(x, 2 if abs(x) >= 1 else 4)
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_round(v) for v in x]
    return x


def tool(fn):
    """Register a read-only tool that returns compact JSON text. Bad specs and SQL errors go back to the
    model as readable errors it can fix (anything else would reach it as a bare "Error executing tool")."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
        except (semantic.SpecError, sqlite3.Error, ValueError) as e:
            raise ToolError(str(e)) from e
        return json.dumps(_round(result), separators=(',', ':'), default=str)

    mcp.tool(annotations=READ_ONLY, structured_output=False)(wrapper)
    return fn


# --- which data a store id means -------------------------------------------------------

@dataclass
class Target:
    id: str
    name: str
    level: str  # a semantic level
    reports: Path  # what report tools and SQL query
    warehouse: Path  # the core KPI warehouse
    through: date
    view: ViewConfig


def _target(store: str) -> Target:
    snap = manager.snapshot()
    if store == ALL_STORES:
        ok = [s for s in snap.stores if s.ok]
        if snap.reports_path is None or snap.combined_path is None:
            raise ValueError(f'the all-stores data is unavailable ({snap.reports_error or "no store has data"})')
        return Target(ALL_STORES, 'All stores', semantic.POOLED, snap.reports_path, snap.combined_path,
                      snap.combined_through, ViewConfig.combined([s.store for s in ok]))
    sd = snap.get(store)
    if sd is None:
        ids = [s.store.id for s in snap.stores] + [ALL_STORES]
        raise ValueError(f'no store {store!r}; choose from {ids}')
    if not sd.ok:
        raise ValueError(f'{sd.store.name}: data unavailable ({sd.error})')
    if sd.reports_path is None:
        raise ValueError(f'{sd.store.name}: report data unavailable ({sd.reports_error})')
    return Target(sd.store.id, sd.store.name, sd.level, sd.reports_path, sd.warehouse_path,
                  sd.data_through, ViewConfig.of(sd.store))


# --- tools ----------------------------------------------------------------------------

@tool
def list_stores() -> dict:
    """Every store: id, name, tier, the date its data is complete through, and what it can answer.
    Also the "all" view (every store pooled) and the date it is cut off at."""
    snap = manager.snapshot()
    out = []
    for s in snap.stores:
        row = {'id': s.store.id, 'name': s.store.name, 'tier': s.store.tier,
               'data_through': s.data_through.isoformat() if s.data_through else None}
        if not s.ok:
            row['unavailable'] = s.error
        else:
            row['datasets'] = list(semantic.catalog(s.level))
            if s.level in semantic.LEVEL_NOTES:
                row['limits'] = semantic.LEVEL_NOTES[s.level]
            if s.reports_path is None:
                row['reports_unavailable'] = s.reports_error
        out.append(row)
    return {'stores': out,
            'all': {'id': ALL_STORES, 'data_through': snap.combined_through.isoformat()
                    if snap.combined_through else None,
                    'stores': [s.store.id for s in snap.stores if s.ok],
                    'limits': semantic.LEVEL_NOTES[semantic.POOLED]}}


@tool
def get_catalog(store: StoreId) -> dict:
    """The datasets, dimensions and metrics this store's data has, its preset reports, and how current it is."""
    t = _target(store)
    with db.using(t.reports):
        info = db.data_info()
    return {'store': {'id': t.id, 'name': t.name, 'data_through': t.through.isoformat(), **info},
            'limits': semantic.LEVEL_NOTES.get(t.level),
            'datasets': semantic.catalog(t.level),
            'presets': [{'id': p['id'], 'title': p['title'], 'description': p['description']}
                        for p in presets.for_level(t.level)]}


class Filter(BaseModel):
    field: str = Field(description='A dimension key from get_catalog, e.g. "customer_type" or "store".')
    op: Literal['in', 'not_in', 'eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'contains'] = 'in'
    value: Any = Field(description='A list for in/not_in, otherwise one value. "today" works for date comparisons.')


class Sort(BaseModel):
    field: str = Field(description='A dimension or metric key in the report.')
    dir: Literal['asc', 'desc'] = 'desc'


def _compact(result):
    """run_report output without the UI-only bits, keyed by column so it reads well in a transcript."""
    return {'columns': [{k: c[k] for k in ('key', 'label', 'role', 'kind', 'format') if k in c}
                        for c in result['columns']],
            'rows': result['rows'], 'totals': result['totals'], 'truncated': result['truncated']}


@tool
def run_report(
    store: StoreId,
    metrics: Annotated[list[str], Field(description='Metric keys from get_catalog, e.g. ["sales", "jobs"].')],
    dimensions: Annotated[list[str], Field(description='Group-by dimension keys, e.g. ["year"]. Empty = one total row.')] = [],
    dataset: Dataset = 'work_orders',
    date_from: Annotated[str | None, Field(description='Order date lower bound, YYYY-MM-DD, inclusive.')] = None,
    date_to: Annotated[str | None, Field(description='Order date upper bound, YYYY-MM-DD, inclusive.')] = None,
    filters: list[Filter] = [],
    sort: Sort | None = None,
    limit: Annotated[int, Field(ge=1, le=semantic.MAX_LIMIT)] = 200,
    include_void: bool = False,
) -> dict:
    """Aggregate work orders (one row per job) or components (one row per moulding/mat/glazing/etc. on a job;
    on-prem stores only) by any dimensions. Returns rows plus overall totals for the same filters."""
    t = _target(store)
    spec = {'dataset': dataset, 'dimensions': dimensions, 'metrics': metrics, 'date_from': date_from,
            'date_to': date_to, 'filters': [f.model_dump() for f in filters],
            'sort': sort.model_dump() if sort else None, 'limit': limit, 'include_void': include_void}
    with db.using(t.reports):
        return {'store': t.name, **_compact(semantic.run_report(spec, t.level))}


@tool
def run_preset(
    store: StoreId,
    preset_id: Annotated[str, Field(description='A preset id from get_catalog, e.g. "monthly_sales".')],
    date_from: Annotated[str | None, Field(description='Optional order date lower bound, YYYY-MM-DD.')] = None,
    date_to: Annotated[str | None, Field(description='Optional order date upper bound, YYYY-MM-DD.')] = None,
) -> dict:
    """Run one of the ready-made reports, optionally narrowed to a date range."""
    t = _target(store)
    available = {p['id']: p for p in presets.for_level(t.level)}
    preset = available.get(preset_id)
    if not preset:
        raise ValueError(f'preset {preset_id!r} is not available for {t.name}; choose from {sorted(available)}')
    spec = dict(preset['spec'])
    spec['date_from'] = date_from or spec.get('date_from')
    spec['date_to'] = date_to or spec.get('date_to')
    with db.using(t.reports):
        return {'store': t.name, 'title': preset['title'], **_compact(semantic.run_report(spec, t.level))}


@tool
def find_values(
    store: StoreId,
    dimension: Annotated[str, Field(description='A dimension key, e.g. "customer_name", "salesperson", "comp_vendor".')],
    dataset: Dataset = 'work_orders',
    search: Annotated[str | None, Field(description='Case-insensitive substring to match.')] = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
) -> list[dict]:
    """Distinct values of a dimension with how many rows each has, most common first."""
    t = _target(store)
    with db.using(t.reports):
        return semantic.dimension_values(dataset, dimension, search, limit, t.level)


Month = Annotated[str | None, Field(description='YYYY-MM.')]


@tool
def get_kpis(
    store: StoreId,
    month_from: Month = None,
    month_to: Month = None,
    kpis: Annotated[list[str] | None, Field(description='KPI keys to include; default all five.')] = None,
) -> dict:
    """The retention KPIs from the website's dashboard, by month: each with what it means, which direction is
    good, the first month it is meaningful from (earlier values rest on too little history) and any baseline.
    Defaults to the 12 months up to the store's latest data. A month that isn't over yet has final=false."""
    t = _target(store)
    end = month_to or t.through.strftime('%Y-%m')
    start = month_from or add_months(month_start(date.fromisoformat(end + '-01')), -11).strftime('%Y-%m')
    for m in (start, end):
        date.fromisoformat(m + '-01')  # ValueError -> a readable tool error
    view = store_view(t.view, t.warehouse, t.through)
    known = [m['key'] for m in view['meta']]
    for k in kpis or []:
        if k not in known:
            raise ValueError(f'unknown KPI {k!r}; choose from {known}')
    out = []
    for m in view['meta']:
        if kpis and m['key'] not in kpis:
            continue
        series = view['series'].get(m['key'], {})
        out.append({
            'key': m['key'], 'label': m['label'], 'unit': m['unit'], 'good': m['good'],
            'meaning': m['short'], 'meaningful_from': m['plot_from'], 'baseline': m['baseline'],
            'months': [{'month': ym, 'value': p['v'], 'numerator': p['n'], 'denominator': p['d'],
                        'final': p['final']}
                       for ym, p in sorted(series.items()) if start <= ym <= end],
        })
    return {'store': t.name, 'data_through': t.through.isoformat(), 'kpis': out,
            'how_built': view['store']['method']}


@tool
def get_schema(store: StoreId) -> list[dict]:
    """Every table and view in this store's SQLite database with its columns. wo_facts (and comp_facts on
    on-prem stores) are the cleaned-up tables the reports use; start there. Cloud stores also have their
    raw line_items, customers, visits and kpi_monthly."""
    with db.using(_target(store).reports):
        return db.schema()


@tool
def run_sql(
    store: StoreId,
    sql: Annotated[str, Field(description='One SQLite SELECT statement.')],
    max_rows: Annotated[int, Field(ge=1, le=SQL_MAX_ROWS)] = 500,
) -> dict:
    """Run a read-only SQLite query (10 second limit) against one store's database (or "all": one wo_facts
    table with a store column). The database can't be modified."""
    with db.using(_target(store).reports):
        cols, rows = db.query(sql, max_rows=max_rows + 1)
    return {'columns': cols, 'rows': rows[:max_rows], 'truncated': len(rows) > max_rows}


# --- HTTP: mounted in the website -----------------------------------------------------------

_app = None  # the MCP Starlette app while running() is active


@contextlib.asynccontextmanager
async def running():
    """Start the MCP server for the life of the website (entered by its lifespan). Stateless with JSON
    responses, so any Cloud Run instance can answer any request. host='0.0.0.0' because the default
    (localhost) turns on DNS-rebinding checks that reject the Cloud Run host."""
    global _app
    app = mcp.streamable_http_app(streamable_http_path='/mcp', stateless_http=True, json_response=True,
                                  host='0.0.0.0')
    async with mcp.session_manager.run():
        _app = app
        try:
            yield
        finally:
            _app = None


def _authorized(scope) -> bool:
    """Accept /mcp/<token> (rewritten to /mcp) or /mcp with a bearer token."""
    if not TOKEN:
        return False
    path = scope['path']
    if path.startswith('/mcp/'):
        given, _, rest = path[len('/mcp/'):].partition('/')
        if rest or not hmac.compare_digest(given.encode(), TOKEN.encode()):
            return False
        scope['path'] = '/mcp'
        scope['raw_path'] = b'/mcp'
        return True
    auth = dict(scope['headers']).get(b'authorization', b'').decode('latin-1')
    return auth.startswith('Bearer ') and hmac.compare_digest(auth[7:].encode(), TOKEN.encode())


async def _send_plain(send, status, body):
    await send({'type': 'http.response.start', 'status': status,
                'headers': [(b'content-type', b'text/plain; charset=utf-8')]})
    await send({'type': 'http.response.body', 'body': body})


class McpMount:
    """ASGI middleware: /mcp and /mcp/<token> go to the MCP server (with its own token check, not the
    site's login); everything else to the website."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not (scope['path'] == '/mcp' or scope['path'].startswith('/mcp/')):
            return await self.app(scope, receive, send)
        if not _authorized(scope):
            return await _send_plain(send, 404, b'Not Found')
        if _app is None:
            return await _send_plain(send, 503, b'MCP server not running')
        return await _app(scope, receive, send)
