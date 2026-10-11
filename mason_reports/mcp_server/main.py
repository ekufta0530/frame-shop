"""MCP server over lsreports, for Claude (claude.ai custom connector, Claude Code, Claude Desktop).

No OAuth: access needs MCP_TOKEN, either in the URL path (https://host/mcp/<token>, which is what a
claude.ai connector uses) or as "Authorization: Bearer <token>". Anything else gets a 404. Changing the
secret and redeploying (deploy/deploy_mcp.sh --rotate) cuts off every existing connection.

  MCP_TOKEN=dev uvicorn mcp_server.main:app --port 8766     # local: http://localhost:8766/mcp/dev
"""
import functools
import hmac
import json
import os
import secrets
import sqlite3
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from lsreports import db, presets, semantic

TOKEN = os.environ.get('MCP_TOKEN', '')
if not TOKEN:
    if os.environ.get('K_SERVICE'):  # K_SERVICE is set on Cloud Run
        raise RuntimeError('MCP_TOKEN must be set')
    TOKEN = secrets.token_urlsafe(24)
    print(f'MCP_TOKEN not set; for this run use /mcp/{TOKEN}')

SQL_MAX_ROWS = 2000

INSTRUCTIONS = """\
Sales and operations data for a picture-framing shop, extracted from its LifeSaver POS.
Start with get_catalog to see the datasets, dimensions, metrics and preset reports, then answer with
run_preset or run_report. Use find_values to get exact spellings (salesperson initials, vendors, items)
before filtering. Use get_schema + run_sql only when the report tools can't express the question.

Things to know:
- Money is pre-tax unless the metric says otherwise. Voided work orders are excluded by default.
- True cost exists only for moulding, mat and fillet, so margins cover only those components.
- The data is a periodic snapshot; get_catalog reports the latest order date it contains.
- Customer names, phones and addresses are personal data: only surface them when the question needs them.
"""

mcp = MCPServer('LifeSaver reports', instructions=INSTRUCTIONS)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def _round(x):
    """Floats to 2 decimals: averages come out of SQLite as 140.04524288107203, which is just noise here."""
    if isinstance(x, float):
        return round(x, 2)
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

Dataset = Literal['work_orders', 'components']


class Filter(BaseModel):
    field: str = Field(description='A dimension key from get_catalog, e.g. "salesperson" or "comp_kind".')
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
def get_catalog() -> dict:
    """Datasets with their dimensions and metrics, the preset reports, and how current the data is."""
    return {'datasets': semantic.catalog(),
            'presets': [{'id': p['id'], 'title': p['title'], 'description': p['description']}
                        for p in presets.PRESETS],
            'data': db.data_info()}


@tool
def run_report(
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
    """Aggregate work orders (one row per job) or components (one row per moulding/mat/glazing/etc. on a job)
    by any dimensions. Returns rows plus overall totals for the same filters."""
    spec = {'dataset': dataset, 'dimensions': dimensions, 'metrics': metrics, 'date_from': date_from,
            'date_to': date_to, 'filters': [f.model_dump() for f in filters],
            'sort': sort.model_dump() if sort else None, 'limit': limit, 'include_void': include_void}
    return _compact(semantic.run_report(spec))


@tool
def run_preset(
    preset_id: Annotated[str, Field(description='A preset id from get_catalog, e.g. "yearly_summary".')],
    date_from: Annotated[str | None, Field(description='Optional order date lower bound, YYYY-MM-DD.')] = None,
    date_to: Annotated[str | None, Field(description='Optional order date upper bound, YYYY-MM-DD.')] = None,
) -> dict:
    """Run one of the ready-made reports, optionally narrowed to a date range."""
    preset = presets.BY_ID.get(preset_id)
    if not preset:
        raise ValueError(f'unknown preset {preset_id!r}; choose from {sorted(presets.BY_ID)}')
    spec = dict(preset['spec'])
    spec['date_from'] = date_from or spec.get('date_from')
    spec['date_to'] = date_to or spec.get('date_to')
    return {'title': preset['title'], **_compact(semantic.run_report(spec))}


@tool
def find_values(
    dimension: Annotated[str, Field(description='A dimension key, e.g. "salesperson", "comp_vendor", "customer_name".')],
    dataset: Dataset = 'work_orders',
    search: Annotated[str | None, Field(description='Case-insensitive substring to match.')] = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
) -> list[dict]:
    """Distinct values of a dimension with how many rows each has, most common first."""
    return semantic.dimension_values(dataset, dimension, search, limit)


@tool
def get_schema() -> list[dict]:
    """Every table and view in the SQLite database with its columns. wo_facts and comp_facts are the
    cleaned-up views the reports use; start there."""
    return db.schema()


@tool
def run_sql(
    sql: Annotated[str, Field(description='One SQLite SELECT statement.')],
    max_rows: Annotated[int, Field(ge=1, le=SQL_MAX_ROWS)] = 500,
) -> dict:
    """Run a read-only SQLite query (10 second limit). The database can't be modified."""
    cols, rows = db.query(sql, max_rows=max_rows + 1)
    return {'columns': cols, 'rows': rows[:max_rows], 'truncated': len(rows) > max_rows}


# stateless + JSON responses: any Cloud Run instance can answer any request, no sticky sessions.
# host='0.0.0.0' because the default (localhost) turns on DNS-rebinding checks that reject the Cloud Run host.
_mcp_app = mcp.streamable_http_app(streamable_http_path='/mcp', stateless_http=True, json_response=True,
                                   host='0.0.0.0')


def _authorized(scope):
    """Accept /mcp/<token> (rewritten to /mcp) or /mcp with a bearer token."""
    path = scope['path']
    if path.startswith('/mcp/'):
        given, _, rest = path[len('/mcp/'):].partition('/')
        if rest or not hmac.compare_digest(given.encode(), TOKEN.encode()):
            return False
        scope['path'] = '/mcp'
        scope['raw_path'] = b'/mcp'
        return True
    if path == '/mcp':
        auth = dict(scope['headers']).get(b'authorization', b'').decode('latin-1')
        return auth.startswith('Bearer ') and hmac.compare_digest(auth[7:].encode(), TOKEN.encode())
    return False


async def _send_plain(send, status, body):
    await send({'type': 'http.response.start', 'status': status,
                'headers': [(b'content-type', b'text/plain; charset=utf-8')]})
    await send({'type': 'http.response.body', 'body': body})


async def app(scope, receive, send):
    if scope['type'] != 'http':  # lifespan: starts the MCP session manager
        return await _mcp_app(scope, receive, send)
    if scope['path'] == '/health':  # not /healthz: Cloud Run reserves paths ending in z
        return await _send_plain(send, 200, b'ok')
    if not _authorized(scope):
        return await _send_plain(send, 404, b'Not Found')
    return await _mcp_app(scope, receive, send)
