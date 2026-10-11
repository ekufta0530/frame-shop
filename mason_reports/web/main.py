"""Report builder web app: JSON API over lsreports + a static single-page UI."""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lsreports import db, presets, semantic
from web import auth

app = FastAPI(title='LifeSaver Reports', docs_url=None, redoc_url=None)

app.middleware('http')(auth.auth_middleware)
app.include_router(auth.router)


@app.exception_handler(semantic.SpecError)
async def spec_error(_, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)


@app.get('/health')  # not /healthz: Cloud Run reserves paths ending in z
def health():
    return {'ok': True}


@app.get('/api/catalog')
def catalog():
    return {'datasets': semantic.catalog(), 'presets': presets.PRESETS, 'data': db.data_info()}


@app.post('/api/report')
def report(spec: dict):
    return semantic.run_report(spec)


@app.get('/api/values')
def values(dataset: str, dim: str, q: str | None = None):
    return semantic.dimension_values(dataset, dim, q)


@app.get('/api/schema')
def schema():
    return db.schema()


class SqlIn(BaseModel):
    sql: str


@app.post('/api/sql')
def run_sql(body: SqlIn):
    # The DB is opened read-only with query_only and a time limit, so this can't modify anything.
    try:
        cols, rows = db.query(body.sql, max_rows=5001)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {'columns': cols, 'rows': rows[:5000], 'truncated': len(rows) > 5000}


app.mount('/', StaticFiles(directory=Path(__file__).parent / 'static', html=True), name='static')
