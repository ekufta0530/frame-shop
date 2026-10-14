"""The Frame Shop website: JSON API + the built React frontend.

    uvicorn webapp.server:app --port 8765          # needs webapp/frontend/dist (npm run build)

Pages (client-side routes, all served index.html):
    /                       all-stores overview: stores side by side + the pooled dashboard
    /stores/<id>            one store's dashboard (+ the on-prem section for on-prem stores)
    /stores/<id>/reports    report builder + SQL console (on-prem stores)
    /login
    /privacy, /terms        the public privacy policy and terms (served by the API, not the SPA)
    /mcp/<token>            the MCP server for Claude (webapp/mcp_server.py; its own token, not the login)

Data comes from ``webapp.data.manager`` (see there); payloads are cached until any store's data
changes. Env: STORES_FILE, STORE_<ID>_DATA, USERS_URI, SESSION_SECRET, AUTH_DISABLED=1 (local).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.stores import ALL_STORES
from lsreports import db as lsdb
from lsreports import presets, semantic

from . import auth
from . import mcp_server
from .data import manager
from .insights import insights
from .legal import PRIVACY_HTML, TERMS_HTML
from .views import ViewConfig, overview, store_view

DIST = Path(__file__).resolve().parent / "frontend" / "dist"

log = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(_):
    # Load every store before the first request, then refresh in the background from then on.
    try:
        manager.snapshot()
    except Exception:
        log.exception("initial data load failed; the first request will retry")
    manager.background = True
    async with mcp_server.running():
        yield


app = FastAPI(title="Frame Shop", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.middleware("http")(auth.auth_middleware)
app.add_middleware(mcp_server.McpMount)  # added last = outermost: /mcp never reaches the login check
app.include_router(auth.router)


@app.exception_handler(semantic.SpecError)
async def spec_error(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/health")  # not /healthz: Cloud Run reserves paths ending in z
def health():
    return {"ok": True}


@app.get("/privacy", response_class=HTMLResponse)  # public: linked from the Google consent screen
def privacy():
    return PRIVACY_HTML


@app.get("/terms", response_class=HTMLResponse)
def terms():
    return TERMS_HTML


# --- payload cache: one entry per (view, store), dropped when any data changes ----------

_cache: dict[tuple, object] = {}
_cache_version = {"v": None}
_cache_lock = threading.Lock()


def _cached(key: tuple, build):
    snap = manager.snapshot()
    with _cache_lock:
        if _cache_version["v"] != snap.version:
            _cache.clear()
            _cache_version["v"] = snap.version
        if key in _cache:
            return _cache[key]
    value = build(snap)
    with _cache_lock:
        _cache[key] = value
    return value


def _store_or_404(snap, store_id: str):
    sd = snap.get(store_id)
    if sd is None:
        raise HTTPException(404, f"No store {store_id!r}")
    if not sd.ok:
        raise HTTPException(503, f"{sd.store.name}: data unavailable ({sd.error})")
    return sd


def _on_prem_or_404(snap, store_id: str):
    sd = _store_or_404(snap, store_id)
    if not sd.store.on_prem:
        raise HTTPException(404, f"{sd.store.name} is a cloud store: the report builder needs "
                                 "an on-prem store's full database")
    return sd


# --- stores, overview, dashboards ---------------------------------------------

@app.get("/api/stores")
def stores():
    snap = manager.snapshot()
    return {
        "stores": [{
            "id": s.store.id, "name": s.store.name, "tier": s.store.tier, "slot": i,
            "ok": s.ok, "error": s.error,
            "data_through": s.data_through.isoformat() if s.data_through else None,
        } for i, s in enumerate(snap.stores)],
        "combined_through": snap.combined_through.isoformat() if snap.combined_through else None,
    }


@app.get("/api/overview")
def get_overview():
    return _cached(("overview",), overview)


@app.get("/api/stores/{store_id}/dashboard")
def dashboard(store_id: str):
    def build(snap):
        if store_id == ALL_STORES:
            if snap.combined_path is None:
                raise HTTPException(503, "No store has data yet")
            cfg = ViewConfig.combined([s.store for s in snap.stores if s.ok])
            return store_view(cfg, snap.combined_path, snap.combined_through)
        sd = _store_or_404(snap, store_id)
        return store_view(ViewConfig.of(sd.store), sd.warehouse_path, sd.data_through)
    return _cached(("dashboard", store_id), build)


@app.get("/api/stores/{store_id}/insights")
def store_insights(store_id: str):
    def build(snap):
        sd = _on_prem_or_404(snap, store_id)
        return insights(sd.source_path, sd.data_through)
    return _cached(("insights", store_id), build)


# --- report builder (on-prem stores) ---------------------------------------------

@app.get("/api/stores/{store_id}/catalog")
def catalog(store_id: str):
    sd = _on_prem_or_404(manager.snapshot(), store_id)
    with lsdb.using(sd.source_path):
        return {"datasets": semantic.catalog(), "presets": presets.for_level(semantic.FULL), "data": lsdb.data_info()}


@app.post("/api/stores/{store_id}/report")
def report(store_id: str, spec: dict):
    sd = _on_prem_or_404(manager.snapshot(), store_id)
    with lsdb.using(sd.source_path):
        try:
            return semantic.run_report(spec)
        except (ValueError, TypeError, AttributeError) as e:  # malformed spec values, e.g. limit="abc"
            raise semantic.SpecError(f"invalid report spec: {e}") from e


@app.get("/api/stores/{store_id}/values")
def values(store_id: str, dataset: str, dim: str, q: str | None = None):
    sd = _on_prem_or_404(manager.snapshot(), store_id)
    with lsdb.using(sd.source_path):
        return semantic.dimension_values(dataset, dim, q)


@app.get("/api/stores/{store_id}/schema")
def schema(store_id: str):
    sd = _on_prem_or_404(manager.snapshot(), store_id)
    with lsdb.using(sd.source_path):
        return lsdb.schema()


class SqlIn(BaseModel):
    sql: str


@app.post("/api/stores/{store_id}/sql")
def run_sql(store_id: str, body: SqlIn):
    sd = _on_prem_or_404(manager.snapshot(), store_id)
    # read-only connection with query_only and a time limit: this can't modify anything
    try:
        with lsdb.using(sd.source_path):
            cols, rows = lsdb.query(body.sql, max_rows=5001)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"columns": cols, "rows": rows[:5000], "truncated": len(rows) > 5000}


# --- frontend -----------------------------------------------------------------------

@app.get("/api/{rest:path}")
def api_404(rest: str):
    raise HTTPException(404, "Not found")


if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{rest:path}")
def spa(rest: str):
    f = (DIST / rest).resolve()
    if rest and f.is_file() and f.is_relative_to(DIST):
        return FileResponse(f)
    index = DIST / "index.html"
    if not index.exists():
        raise HTTPException(503, "Frontend not built: cd webapp/frontend && npm install && npm run build")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
