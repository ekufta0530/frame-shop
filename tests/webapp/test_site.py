"""The website's data manager and API, over two tiny synthetic stores (one per tier)."""
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from core.config import get_kpi_settings
from core.kpis import compute_kpis
from core.store import DATA_THROUGH, Warehouse
from core.stores import Store
from core.visits import LineForVisit, build_visits
from sqlite_extract.importer import import_store
from webapp import auth, server
from webapp.data import DataManager

CLOUD_ROWS = [("c1", "2025-01-10", 100.0), ("c1", "2025-07-01", 50.0), ("c2", "2025-02-01", 80.0),
              ("c3", "2025-08-20", 60.0), ("c4", "2024-08-05", 90.0), ("c4", "2025-08-25", 70.0)]
ONPREM_ROWS = [(1, 10, "2024-03-02", 200.0, "Delivered"), (2, 10, "2025-01-15", 120.0, "Delivered"),
               (3, 12, "2025-02-10", 300.0, "Live"), (4, 12, "2025-08-04", 40.0, "Done"),
               (5, 13, "2025-08-30", 55.0, "Delivered")]


@pytest.fixture
def stores(tmp_path):
    lines = [LineForVisit(customer_id=c, work_order_id=i, order_date=date.fromisoformat(d), retail=r,
                          discount=0.0, current_status="OnOrder") for i, (c, d, r) in enumerate(CLOUD_ROWS)]
    visits, lifecycles = build_visits(lines, non_sale_statuses=get_kpi_settings().non_sale_statuses)
    with Warehouse(tmp_path / "cloud.db") as wh:
        wh.replace_visits(visits, lifecycles)
        wh.set_checkpoint(DATA_THROUGH, "2025-09-10")
    c = sqlite3.connect(tmp_path / "lifesaver.sqlite")
    c.execute("CREATE TABLE wo_facts (wo_no, cust_no, order_date, subtotal, status)")
    c.executemany("INSERT INTO wo_facts VALUES (?, ?, ?, ?, ?)", ONPREM_ROWS)
    c.commit()
    c.close()
    return [Store(id="shop", name="Shop", tier="cloud", data=str(tmp_path / "cloud.db")),
            Store(id="mason", name="Mason", tier="on-prem", data=str(tmp_path / "lifesaver.sqlite"))]


@pytest.fixture
def client(stores, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "manager", DataManager(stores, tmp_path / "cache"))
    monkeypatch.setattr(auth, "AUTH_DISABLED", True)
    server._cache.clear()
    server._cache_version["v"] = None
    return TestClient(server.app)


def test_stores_listing_keeps_config_order_and_data_dates(client):
    r = client.get("/api/stores").json()
    assert [(s["id"], s["tier"], s["slot"], s["ok"]) for s in r["stores"]] == [
        ("shop", "cloud", 0, True), ("mason", "on-prem", 1, True)]
    assert [s["data_through"] for s in r["stores"]] == ["2025-09-10", "2025-08-30"]
    assert r["combined_through"] == "2025-08-30"  # the oldest store's data


def test_overview_compares_stores_on_the_last_month_every_store_has_closed(client):
    o = client.get("/api/overview").json()
    assert o["report_month"] == "2025-07"  # Aug isn't closed in the 08-30 snapshot
    assert [s["id"] for s in o["stores"]] == ["shop", "mason"]
    shop, mason = o["stores"]
    assert shop["periods"]["month"]["rev"] == pytest.approx(50.0)
    assert mason["periods"]["month"]["rev"] == 0
    comb = o["combined"]["periods"]["ytd"]
    assert comb["rev"] == pytest.approx(shop["periods"]["ytd"]["rev"] + mason["periods"]["ytd"]["rev"])
    assert comb["v"] == shop["periods"]["ytd"]["v"] + mason["periods"]["ytd"]["v"]


def test_all_stores_dashboard_is_pooled_and_stacked_by_store(client):
    d = client.get("/api/stores/all/dashboard").json()
    assert d["store"]["tier"] == "all" and d["store"]["data_through"] == "2025-08-30"
    assert set(d["business"]["by_store"]) == {"shop", "mason"}
    # pooled customers: shop c1..c4 + mason 10, 12, 13 -- ids never merge across stores
    assert d["summary"]["customers"] == 7


def test_store_dashboard_uses_each_stores_own_today(client):
    shop = client.get("/api/stores/shop/dashboard").json()
    mason = client.get("/api/stores/mason/dashboard").json()
    assert shop["store"]["data_through"] == "2025-09-10" and shop["current_month"] == "2025-09"
    assert mason["store"]["data_through"] == "2025-08-30" and mason["current_month"] == "2025-08"


def test_in_store_detail_is_on_prem_only(client):
    assert client.get("/api/stores/shop/insights").status_code == 404
    assert client.get("/api/stores/nope/dashboard").status_code == 404
    assert client.post("/api/stores/mason/sql", json={"sql": "select count(*) from wo_facts"}).json()["rows"] == [[5]]
    assert client.post("/api/stores/mason/sql", json={"sql": "drop table wo_facts"}).status_code == 400


def test_one_failing_insight_does_not_blank_the_section(client):
    r = client.get("/api/stores/mason/insights")  # the synthetic wo_facts lacks most columns
    assert r.status_code == 200
    reports = r.json()["reports"]
    assert any("error" in x for x in reports)  # needs columns the synthetic table lacks...
    assert any("result" in x for x in reports)  # ...while the ones it can answer still come back


def test_a_broken_store_is_reported_and_left_out_of_the_pool(stores, tmp_path, monkeypatch):
    stores[1] = Store(id="mason", name="Mason", tier="on-prem", data=str(tmp_path / "missing.sqlite"))
    monkeypatch.setattr(server, "manager", DataManager(stores, tmp_path / "cache"))
    monkeypatch.setattr(auth, "AUTH_DISABLED", True)
    server._cache.clear()
    server._cache_version["v"] = None
    c = TestClient(server.app)
    listing = c.get("/api/stores").json()
    assert [s["ok"] for s in listing["stores"]] == [True, False]
    assert c.get("/api/stores/mason/dashboard").status_code == 503
    o = c.get("/api/overview").json()
    assert [s["id"] for s in o["stores"]] == ["shop"] and o["unavailable"][0]["id"] == "mason"
    assert c.get("/api/stores/all/dashboard").json()["summary"]["customers"] == 4


def test_new_data_is_picked_up_and_old_copies_cleaned_up(stores, tmp_path, monkeypatch):
    monkeypatch.setattr("webapp.data.REFRESH_SECONDS", 0)
    mgr = DataManager(stores, tmp_path / "cache")
    first = mgr.snapshot()
    assert mgr.snapshot().version == first.version  # nothing changed
    src = tmp_path / "lifesaver.sqlite"
    c = sqlite3.connect(src)
    c.execute("INSERT INTO wo_facts VALUES (6, 14, '2025-09-02', 10.0, 'Delivered')")
    c.commit()
    c.close()
    import os
    os.utime(src, ns=(10**18, 10**18))  # a distinct generation
    second = mgr.snapshot()
    assert second.version != first.version
    assert second.get("mason").data_through == date(2025, 9, 2)
    mgr.snapshot()
    mgr.snapshot()  # replaced warehouses are deleted one refresh after they stop being served
    assert not first.combined_path.exists() and second.combined_path.exists()


def test_login_is_required_but_health_is_public(client, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    assert client.get("/health").status_code == 200
    assert client.get("/api/stores").status_code == 401
    r = client.get("/stores/mason", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/login").status_code in (200, 503)  # the SPA shell (503 when not built)


def test_import_store_is_what_on_prem_dashboards_read(stores, tmp_path):
    out = import_store(stores[1].data, tmp_path / "w.db")
    assert out["data_through"] == "2025-08-30"


def test_sql_console_cannot_attach_other_database_files(client, stores, tmp_path):
    other = tmp_path / "cloud.db"  # another store's data, readable by the server
    r = client.post("/api/stores/mason/sql", json={"sql": f"ATTACH DATABASE '{other}' AS o"})
    assert r.status_code == 400


def test_malformed_report_spec_is_a_400_not_a_crash(client):
    spec = {"dataset": "work_orders", "dimensions": ["year"], "metrics": ["jobs"], "limit": "abc"}
    assert client.post("/api/stores/mason/report", json=spec).status_code == 400


def test_a_store_far_behind_the_others_is_flagged_on_the_overview(stores, tmp_path, monkeypatch):
    with Warehouse(tmp_path / "cloud.db") as wh:
        wh.set_checkpoint(DATA_THROUGH, "2025-12-31")  # months ahead of the on-prem snapshot
    monkeypatch.setattr(server, "manager", DataManager(stores, tmp_path / "cache"))
    monkeypatch.setattr(auth, "AUTH_DISABLED", True)
    server._cache.clear()
    server._cache_version["v"] = None
    o = TestClient(server.app).get("/api/overview").json()
    assert [(s["id"], s["days_behind"] > 35) for s in o["stale"]] == [("mason", True)]


def test_background_refresh_serves_the_old_snapshot_without_waiting(stores, tmp_path, monkeypatch):
    monkeypatch.setattr("webapp.data.REFRESH_SECONDS", 0)
    mgr = DataManager(stores, tmp_path / "cache")
    first = mgr.snapshot()
    mgr.background = True
    mgr._lock.acquire()  # a refresh in progress holds the lock...
    try:
        import threading
        out = []
        t = threading.Thread(target=lambda: out.append(mgr.snapshot()))
        t.start()
        t.join(1)
        assert out == [first]  # ...but readers still get the current data immediately
    finally:
        mgr._lock.release()


def test_outliers_come_out_of_the_revenue_weighted_kpi_and_the_overview(stores, tmp_path, monkeypatch):
    # shop's c4 return visit (Aug 2025, 70.0) is the one-off; ids come from build_visits
    with Warehouse(stores[0].data) as wh:
        visits = wh.load_all_visits()
        for m in (date(2025, 7, 1), date(2025, 8, 1)):
            wh.upsert_kpi_snapshots(compute_kpis(visits, m, today=date(2025, 9, 10)))
    out = next(v.visit_id for v in visits if v.customer_id == "c4" and v.visit_rank == 2)
    stores[0] = Store(id="shop", name="Shop", tier="cloud", data=stores[0].data,
                      outliers=(out,), outlier_label="big order")
    monkeypatch.setattr(server, "manager", DataManager(stores, tmp_path / "cache2"))
    monkeypatch.setattr(auth, "AUTH_DISABLED", True)
    server._cache.clear()
    server._cache_version["v"] = None
    client = TestClient(server.app)

    assert client.get("/api/stores").json()["stores"][0]["outlier_label"] == "big order"
    d = client.get("/api/stores/shop/dashboard").json()
    raw, ex = d["series"]["repeat_revenue_share"]["2025-08"], d["series_ex"]["repeat_revenue_share"]["2025-08"]
    assert ex["d"] == pytest.approx(raw["d"] - 70.0) and ex["n"] == pytest.approx(raw["n"] - 70.0)
    assert "active_customers_ttm" not in d["series_ex"]  # customer counts don't change
    pooled = client.get("/api/stores/all/dashboard").json()["business"]["outlier"]["by_store"]
    assert pooled == {"shop": {"2025-08": 70.0}}
    shop = client.get("/api/overview").json()["stores"][0]
    assert shop["outlier_monthly"] == {"2025-08": {"rev": 70.0, "v": 1}}
    assert "repeat_revenue_share" in shop["history_ex"]
