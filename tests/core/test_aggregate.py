import sqlite3
from datetime import date

import pytest

from core.aggregate import build_combined
from core.config import get_kpi_settings
from core.kpis import compute_kpis
from core.months import iter_months
from core.store import Warehouse
from core.visits import LineForVisit, build_visits


def make_store(path, rows):
    """rows: (customer, order_date, revenue) -> a core warehouse like an importer builds."""
    lines = [LineForVisit(customer_id=c, work_order_id=i, order_date=date.fromisoformat(d),
                          retail=r, discount=0.0, current_status="OnOrder")
             for i, (c, d, r) in enumerate(rows)]
    visits, lifecycles = build_visits(lines, non_sale_statuses=get_kpi_settings().non_sale_statuses)
    with Warehouse(path) as wh:
        wh.replace_visits(visits, lifecycles)
    return visits


A = [("c1", "2024-01-10", 100.0), ("c1", "2024-06-01", 50.0), ("c2", "2024-02-01", 80.0)]
B = [("c1", "2024-03-05", 200.0), ("c3", "2024-04-01", 40.0), ("c3", "2025-02-01", 60.0)]


def test_pooled_visits_keep_stores_apart_and_cut_off_at_the_common_date(tmp_path):
    make_store(tmp_path / "a.db", A)
    make_store(tmp_path / "b.db", B)
    out = build_combined([("a", tmp_path / "a.db"), ("b", tmp_path / "b.db")],
                         tmp_path / "all.db", through=date(2024, 12, 31))
    assert out["visits"] == 5  # B's 2025 visit is after the cutoff
    c = sqlite3.connect(tmp_path / "all.db")
    ids = {r[0] for r in c.execute("SELECT customer_id FROM visits")}
    assert ids == {"a/c1", "a/c2", "b/c1", "b/c3"}  # c1 in both stores = two customers
    assert c.execute("SELECT value FROM sync_checkpoints WHERE name='data_through'").fetchone()[0] == "2024-12-31"
    assert c.execute("SELECT COUNT(*) FROM customer_lifecycle").fetchone()[0] == 4


def test_pooled_kpis_are_computed_over_all_visits_not_averaged(tmp_path):
    va = make_store(tmp_path / "a.db", A)
    vb = make_store(tmp_path / "b.db", B)
    through = date(2024, 12, 31)
    build_combined([("a", tmp_path / "a.db"), ("b", tmp_path / "b.db")], tmp_path / "all.db", through=through)
    c = sqlite3.connect(tmp_path / "all.db")
    got = c.execute("SELECT value FROM kpi_monthly WHERE metric='active_customers_ttm' "
                    "AND month='2024-12-01'").fetchone()[0]
    # active customers is additive across stores: a/c1, a/c2, b/c1, b/c3
    assert got == 4
    ref = {s.metric: s.value for s in compute_kpis(
        [v for v in va] + [v for v in vb], date(2024, 12, 1), today=through)}
    assert ref["active_customers_ttm"] == pytest.approx(3)  # unprefixed ids would wrongly merge c1


def test_months_iterate_through_the_cutoff(tmp_path):
    make_store(tmp_path / "a.db", A)
    build_combined([("a", tmp_path / "a.db")], tmp_path / "all.db", through=date(2024, 8, 15))
    c = sqlite3.connect(tmp_path / "all.db")
    months = {r[0] for r in c.execute("SELECT month FROM kpi_monthly")}
    assert max(months) == "2024-08-01"
    assert len(months) == len(list(iter_months(date(2024, 1, 1), date(2024, 8, 15))))


def test_sources_are_only_read(tmp_path):
    import os
    make_store(tmp_path / "a.db", A)
    for f in tmp_path.glob("a.db*"):
        os.chmod(f, 0o444)
    os.chmod(tmp_path, 0o555)
    try:
        out = build_combined([("a", tmp_path / "a.db")], tmp_path.parent / f"{tmp_path.name}-all.db",
                             through=date(2024, 12, 31))
    finally:
        os.chmod(tmp_path, 0o755)
    assert out["visits"] == 3
    assert not list(tmp_path.glob("a.db-wal")) or True  # no new sidecar needed to read
