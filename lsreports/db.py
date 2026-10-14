"""Read-only access to a report database: a store's lifesaver.sqlite (on-prem), or a reports DB
built by lsreports.build (cloud stores, all stores pooled).

The website keeps current copies of every store's data (webapp/data.py) and points each request
at one store's file with ``using(path)``; everything below (semantic, presets) then queries it.
"""
import contextlib
import contextvars
import sqlite3
import time

QUERY_TIMEOUT_SECONDS = 10

_override = contextvars.ContextVar('lsreports_db', default=None)


@contextlib.contextmanager
def using(path):
    """Run the queries inside the block against the local SQLite file at ``path``."""
    token = _override.set(str(path))
    try:
        yield
    finally:
        _override.reset(token)


def db_path():
    path = _override.get()
    if path is None:
        raise RuntimeError('no report database selected: wrap the query in lsreports.db.using(path)')
    return path


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
    return {'latest_order_date': rows[0][0], 'work_orders': rows[0][1]}


def schema():
    """Tables/views and their columns, for the SQL console and the MCP server."""
    _, objs = query("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name")
    return [{'name': n, 'type': t, 'columns': [r[1] for r in query(f'PRAGMA table_info("{n}")')[1]]}
            for n, t in objs]
