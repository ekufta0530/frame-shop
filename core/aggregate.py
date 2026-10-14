"""Several stores' warehouses -> one pooled "all stores" warehouse.

The pooled figures are not averages of the stores' figures: every store's
visits go into one list and the same ``compute_kpis`` runs over it, so a rate
like first->second purchase is (all stores' returners) / (all stores' first
visits), weighted by size exactly as it should be.

- **Customers stay per store.** Ids are prefixed with the store id, so someone
  who shops at two stores counts as two customers -- no cross-store identity
  exists (cloud stores match by name, on-prem by LifeSaver customer number).
- **One common "today".** Stores refresh on different schedules (daily for
  cloud, per upload for on-prem). Everything is cut off at the *oldest* store's
  data-through date, so the pooled open month never mixes a complete store with
  a stale one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path

from .config import get_kpi_settings
from .kpis import compute_kpis
from .models import Visit
from .months import iter_months
from .store import DATA_THROUGH, Warehouse
from .visits import lifecycles_from_visits


def pooled_visits(sources: Sequence[tuple[str, str | Path]], through: date) -> list[Visit]:
    out: list[Visit] = []
    for store_id, path in sources:
        with Warehouse(path, readonly=True) as wh:
            for v in wh.load_all_visits():
                if v.visit_date > through:
                    continue
                out.append(replace(
                    v,
                    visit_id=f"{store_id}/{v.visit_id}",
                    customer_id=f"{store_id}/{v.customer_id}",
                ))
    out.sort(key=lambda v: (v.customer_id, v.visit_date))
    return out


def build_combined(
    sources: Sequence[tuple[str, str | Path]], dest: str | Path, *, through: date
) -> dict:
    """(Re)create ``dest`` as the pooled warehouse of ``sources`` up to ``through``."""
    settings = get_kpi_settings()
    visits = pooled_visits(sources, through)
    dest = Path(dest)
    for p in (dest, Path(f"{dest}-wal"), Path(f"{dest}-shm")):
        p.unlink(missing_ok=True)

    with Warehouse(dest) as wh:
        wh.replace_visits(visits, lifecycles_from_visits(visits))
        if visits:
            first = min(v.visit_date for v in visits)
            for m_start, _ in iter_months(first, through):
                wh.upsert_kpi_snapshots(compute_kpis(
                    visits, m_start, today=through,
                    cohort_window_months=settings.warehouse_cohort_window_months,
                ))
        wh.set_checkpoint(DATA_THROUGH, through.isoformat())
    return {"stores": len(sources), "visits": len(visits), "through": through.isoformat()}
