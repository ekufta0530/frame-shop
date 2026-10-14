"""Builds the report databases that don't come ready-made (see semantic's levels).

An on-prem store's lifesaver.sqlite already has ``wo_facts``. These add it everywhere else:

- ``build_cloud_reports``: a cloud store's ``warehouse.db`` plus a ``wo_facts`` table rolled up
  from its Work Order List line items (level "basic"). The warehouse's own tables come along,
  so SQL can still reach ``line_items``, ``customers``, ``visits`` and ``kpi_monthly``.
- ``build_pooled_reports``: every store's work orders in one ``wo_facts`` table with a
  ``store`` column (level "pooled"), cut off at one common date like core.aggregate's pooled
  warehouse, so the all-stores figures never mix a current store with a stale one.

Sales follow the KPI pipeline's rule (core/visits.py): ``retail`` is the list price and
revenue is ``retail - discount``.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path

# The columns every level has: what pooled reports carry from each store.
BASIC_COLUMNS = ('wo_no', 'invoice_no', 'cust_no', 'customer_name', 'order_date', 'promised_date',
                 'status', 'description', 'subtotal', 'list_subtotal', 'discount_amt', 'customer_type')

# One row per work order. A work order's line items share its id (work_order_number is
# "<id>.<revision>"). Void lines are left out of the money unless the whole order is void, the
# same lines the KPI pipeline drops; customer_type mirrors the on-prem view's definition.
_CLOUD_WO_FACTS = """
CREATE TABLE wo_facts AS
WITH lines AS (
  SELECT *, lower(coalesce(current_status, '')) != 'void' AS live,
         MAX(lower(coalesce(current_status, '')) != 'void') OVER (PARTITION BY work_order_id) AS has_live
  FROM line_items
),
wo AS (
  SELECT work_order_id AS wo_no, MAX(invoice_number) AS invoice_no, MAX(customer_id) AS cust_no,
    MAX(customer_raw) AS raw_name, MIN(order_date) AS order_date, MAX(date_due) AS promised_date,
    CASE WHEN MAX(has_live) THEN MAX(CASE WHEN live THEN current_status END) ELSE 'Void' END AS status,
    group_concat(description, '; ') AS description,
    ROUND(SUM(CASE WHEN live OR NOT has_live THEN coalesce(retail, 0) END), 2) AS list_subtotal,
    ROUND(SUM(CASE WHEN live OR NOT has_live THEN coalesce(discount, 0) END), 2) AS discount_amt,
    ROUND(SUM(CASE WHEN live OR NOT has_live THEN coalesce(retail, 0) - coalesce(discount, 0) END), 2)
      AS subtotal
  FROM lines GROUP BY work_order_id
)
SELECT wo.wo_no, wo.invoice_no, wo.cust_no, coalesce(c.display_name, wo.raw_name) AS customer_name,
  wo.order_date, wo.promised_date, wo.status, wo.description, wo.subtotal, wo.list_subtotal,
  wo.discount_amt,
  CASE WHEN wo.order_date = MIN(wo.order_date) OVER (PARTITION BY wo.cust_no) THEN 'New' ELSE 'Returning' END
    AS customer_type
FROM wo LEFT JOIN customers c ON c.customer_id = wo.cust_no;
CREATE INDEX ix_wo_facts_order_date ON wo_facts(order_date);
"""


def _fresh(dest: Path) -> None:
    for p in (dest, Path(f'{dest}-wal'), Path(f'{dest}-shm'), Path(f'{dest}.part')):
        p.unlink(missing_ok=True)
    _drop_journal(Path(f'{dest}.part'))


def _drop_journal(db: Path) -> None:
    for p in (Path(f'{db}-wal'), Path(f'{db}-shm')):
        p.unlink(missing_ok=True)


def build_cloud_reports(warehouse: str | Path, dest: str | Path) -> int:
    """(Re)create ``dest`` as ``warehouse`` plus ``wo_facts``. Returns the number of work orders."""
    dest = Path(dest)
    _fresh(dest)
    tmp = Path(f'{dest}.part')
    src = sqlite3.connect(f'file:{warehouse}?mode=ro', uri=True)
    out = sqlite3.connect(tmp)
    try:
        src.backup(out)
        # the copy inherits the warehouse's WAL mode, and a WAL file can't be opened read-only
        # without its -shm file: make it a plain single file
        out.execute('PRAGMA journal_mode = DELETE')
        out.executescript(_CLOUD_WO_FACTS)
        n = out.execute('SELECT COUNT(*) FROM wo_facts').fetchone()[0]
        out.commit()
    finally:
        out.close()
        src.close()
    _drop_journal(tmp)
    tmp.replace(dest)
    return n


def build_pooled_reports(sources: Sequence[tuple[str, str, str | Path]], dest: str | Path, *,
                         through: date) -> int:
    """(Re)create ``dest`` from ``(store_id, store_name, reports_db)`` sources, keeping orders up to
    ``through``. Customer numbers are prefixed with the store id: no one is matched across stores."""
    dest = Path(dest)
    _fresh(dest)
    tmp = Path(f'{dest}.part')
    cols = ', '.join(BASIC_COLUMNS)
    out = sqlite3.connect(f'file:{tmp}', uri=True)  # uri: lets ATTACH open the sources read-only
    try:
        out.execute(f'CREATE TABLE wo_facts (store, {cols})')
        for store_id, name, path in sources:
            out.execute('ATTACH ? AS src', (f'file:{path}?mode=ro',))
            try:
                picked = ', '.join("? || '/' || cust_no" if c == 'cust_no' else c for c in BASIC_COLUMNS)
                out.execute(f'INSERT INTO wo_facts SELECT ?, {picked} FROM src.wo_facts WHERE order_date <= ?',
                            (name, store_id, through.isoformat()))
                out.commit()
            finally:
                out.execute('DETACH src')
        out.execute('CREATE INDEX ix_wo_facts_order_date ON wo_facts(order_date)')
        n = out.execute('SELECT COUNT(*) FROM wo_facts').fetchone()[0]
        out.commit()
    finally:
        out.close()
    tmp.replace(dest)
    return n
