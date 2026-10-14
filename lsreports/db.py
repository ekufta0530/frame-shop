"""Read-only access to an extracted LifeSaver SQLite DB (an on-prem store's lifesaver.sqlite).

By default that is DATA_URI: a local path or gs://bucket/path/lifesaver.sqlite. GCS copies are
pulled to /tmp and re-checked at most every REFRESH_SECONDS, so uploading a new file is all a
data refresh takes. That is how the single-store MCP server (mason_reports/) runs.

The multi-store website manages its own copies and points each request at one store's file
with ``using(path)``; everything below (semantic, presets) then queries that file.
"""
import contextlib
import contextvars
import os
import sqlite3
import threading
import time
from pathlib import Path

DATA_URI = os.environ.get('DATA_URI', 'lifesaver.sqlite')
REFRESH_SECONDS = int(os.environ.get('REFRESH_SECONDS', '600'))
QUERY_TIMEOUT_SECONDS = 10

_lock = threading.Lock()
_state = {'path': None, 'generation': None, 'checked': 0.0, 'loaded_at': None}
_override = contextvars.ContextVar('lsreports_db', default=None)


@contextlib.contextmanager
def using(path):
    """Run the queries inside the block against the local SQLite file at ``path``."""
    token = _override.set(str(path))
    try:
        yield
    finally:
        _override.reset(token)


def _sync_from_gcs():
    from google.cloud import storage  # only needed in the cloud

    bucket, _, blob_name = DATA_URI[len('gs://'):].partition('/')
    blob = storage.Client().bucket(bucket).get_blob(blob_name)
    if blob is None:
        raise FileNotFoundError(DATA_URI)
    if blob.generation != _state['generation']:
        dest = Path('/tmp') / f'lifesaver-{blob.generation}.sqlite'
        blob.download_to_filename(dest)
        old = _state['path']
        _state.update(path=str(dest), generation=blob.generation, loaded_at=time.time())
        if old and old != str(dest):
            Path(old).unlink(missing_ok=True)


def db_path():
    if _override.get() is not None:
        return _override.get()
    with _lock:
        if not DATA_URI.startswith('gs://'):
            _state['path'] = DATA_URI
            _state['loaded_at'] = _state['loaded_at'] or os.path.getmtime(DATA_URI)
        elif time.time() - _state['checked'] > REFRESH_SECONDS or _state['path'] is None:
            _state['checked'] = time.time()
            try:
                _sync_from_gcs()
            except Exception:
                if _state['path'] is None:
                    raise  # nothing cached to fall back on
        return _state['path']


def connect():
    con = sqlite3.connect(f'file:{db_path()}?mode=ro', uri=True, check_same_thread=False)
    con.execute('PRAGMA query_only = 1')
    con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)  # read-only still allows ATTACH: no reading other files
    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
    con.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)  # abort runaway queries
    return con


def query(sql, params=(), max_rows=None):
    con = connect()
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(max_rows) if max_rows else cur.fetchall()
        return cols, [list(r) for r in rows]
    finally:
        con.close()


def data_info():
    _, rows = query('SELECT max(order_date), count(*) FROM wo_facts')
    return {'latest_order_date': rows[0][0], 'work_orders': rows[0][1],
            'loaded_at': None if _override.get() else _state['loaded_at'],
            'source': _override.get() or DATA_URI}


def schema():
    """Tables/views and their columns, for the SQL console and (later) the MCP server."""
    _, objs = query("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name")
    return [{'name': n, 'type': t, 'columns': [r[1] for r in query(f'PRAGMA table_info("{n}")')[1]]}
            for n, t in objs]
