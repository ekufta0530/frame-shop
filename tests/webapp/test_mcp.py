"""The MCP server inside the website: one cloud store, one on-prem store and the pooled view."""
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from cloud.lifesaver.models import WorkOrder
from cloud.warehouse import pipeline
from cloud.warehouse.config import WarehouseSettings
from cloud.warehouse.store import CloudWarehouse
from core.store import DATA_THROUGH
from core.stores import Store
from lsreports import semantic
from webapp import auth, mcp_server, server
from webapp.data import DataManager

# (work order number, line, customer, order date, retail, discount, status)
CLOUD_LINES = [
    ("1.1", 1, "Alice Smith", "2025-01-10", 200.0, 20.0, "OnOrder"),
    ("2.1", 1, "Bob Jones", "2025-02-01", 100.0, 0.0, "OnOrder"),
    ("2.1", 2, "Bob Jones", "2025-02-01", 50.0, 0.0, "Void"),  # a voided line on a live order
    ("3.2", 1, "Alice Smith", "2025-07-01", 120.0, 0.0, "OnOrder"),
    ("4.1", 1, "Carol King", "2025-08-20", 80.0, 0.0, "Void"),  # a wholly voided order
    ("5.1", 1, "Dave Lee", "2025-09-05", 60.0, 0.0, "OnOrder"),  # after the on-prem store's data ends
]
ONPREM_COLS = ("wo_no", "invoice_no", "cust_no", "customer_name", "order_date", "promised_date", "status",
               "description", "subtotal", "list_subtotal", "discount_amt", "customer_type", "salesperson")
ONPREM_ROWS = [
    (1, 1, 10, "Ann", "2024-03-02", None, "Delivered", "Print", 200.0, 200.0, 0.0, "New", "ECP"),
    (2, 2, 10, "Ann", "2025-01-15", None, "Delivered", "Photo", 120.0, 120.0, 0.0, "Returning", "ECP"),
    (3, 3, 12, "Ben", "2025-02-10", None, "Live", "Poster", 300.0, 330.0, 30.0, "New", "BC"),
    (4, 4, 12, "Ben", "2025-08-04", None, "Done", "Map", 40.0, 40.0, 0.0, "Returning", "BC"),
    (5, 5, 13, "Cy", "2025-08-30", None, "Delivered", "Diploma", 55.0, 55.0, 0.0, "New", "ECP"),
]


def _cloud_warehouse(path, raw):
    settings = WarehouseSettings()
    rows = [WorkOrder(invoice_number=int(won.split(".")[0]), work_order_number=won, customer=cust,
                      line_item_number=line, description="Frame", current_status=status, retail=retail,
                      discount=disc, order_date=date.fromisoformat(d), date_due=None)
            for won, line, cust, d, retail, disc, status in CLOUD_LINES]
    with CloudWarehouse(path, raw) as wh:
        wh.upsert_line_items(rows, "p1", "2025-09-10T00:00:00Z")
        pipeline.resolve_identities(wh, settings)
        pipeline.rebuild_visits(wh, settings)
        pipeline.snapshot_history(wh, settings, today=date(2025, 9, 10))
        wh.set_checkpoint(DATA_THROUGH, "2025-09-10")


@pytest.fixture
def stores(tmp_path):
    _cloud_warehouse(tmp_path / "cloud.db", tmp_path / "raw")
    c = sqlite3.connect(tmp_path / "lifesaver.sqlite")
    c.execute(f"CREATE TABLE wo_facts ({', '.join(ONPREM_COLS)})")
    c.executemany(f"INSERT INTO wo_facts VALUES ({', '.join('?' * len(ONPREM_COLS))})", ONPREM_ROWS)
    c.commit()
    c.close()
    return [Store(id="shop", name="Shop", tier="cloud", data=str(tmp_path / "cloud.db")),
            Store(id="mason", name="Mason", tier="on-prem", data=str(tmp_path / "lifesaver.sqlite"))]


@pytest.fixture
def manager(stores, tmp_path, monkeypatch):
    m = DataManager(stores, tmp_path / "cache")
    monkeypatch.setattr(server, "manager", m)
    monkeypatch.setattr(mcp_server, "manager", m)
    server._cache.clear()
    server._cache_version["v"] = None
    return m


def test_cloud_reports_sales_are_list_price_minus_discount_and_match_the_kpis(manager):
    sd = manager.snapshot().get("shop")
    c = sqlite3.connect(sd.reports_path)
    rows = c.execute("SELECT wo_no, status, subtotal, list_subtotal, discount_amt, customer_type "
                     "FROM wo_facts ORDER BY wo_no").fetchall()
    assert rows == [(1, "OnOrder", 180.0, 200.0, 20.0, "New"),
                    (2, "OnOrder", 100.0, 100.0, 0.0, "New"),  # the void line is left out
                    (3, "OnOrder", 120.0, 120.0, 0.0, "Returning"),
                    (4, "Void", 80.0, 80.0, 0.0, "New"),
                    (5, "OnOrder", 60.0, 60.0, 0.0, "New")]
    live = c.execute("SELECT SUM(subtotal) FROM wo_facts WHERE status != 'Void'").fetchone()[0]
    assert live == c.execute("SELECT SUM(revenue) FROM visits").fetchone()[0]
    # the warehouse's own tables come along for SQL
    assert c.execute("SELECT COUNT(*) FROM line_items").fetchone()[0] == len(CLOUD_LINES)


def test_list_stores_shows_tiers_freshness_and_limits(manager):
    r = mcp_server.list_stores()
    shop, mason = r["stores"]
    assert (shop["id"], shop["tier"], shop["data_through"], shop["datasets"]) == (
        "shop", "cloud", "2025-09-10", ["work_orders"])
    assert "salesperson" in shop["limits"]
    assert mason["datasets"] == ["work_orders", "components"] and "limits" not in mason
    assert r["all"]["data_through"] == "2025-08-30" and r["all"]["stores"] == ["shop", "mason"]


def test_catalog_offers_only_what_the_store_has(manager):
    shop = mcp_server.get_catalog("shop")
    dims = shop["datasets"]["work_orders"]["dimensions"]
    assert "customer_type" in dims and "salesperson" not in dims and "store" not in dims
    assert {p["id"] for p in shop["presets"]} >= {"monthly_sales", "new_vs_returning"}
    assert "salesperson" not in {p["id"] for p in shop["presets"]}
    assert shop["store"]["latest_order_date"] == "2025-09-05"
    pooled = mcp_server.get_catalog("all")
    assert "store" in pooled["datasets"]["work_orders"]["dimensions"]
    assert "by_store" in {p["id"] for p in pooled["presets"]}


def test_all_stores_pools_rows_up_to_the_common_date(manager):
    r = mcp_server.run_report("all", ["jobs", "sales", "customers"], ["store"])
    by_store = {row[0]: row[1:] for row in r["rows"]}
    # cloud: orders 1-3 (4 is void, 5 is after 2025-08-30); on-prem: all five
    assert by_store == {"Shop": [3, 400.0, 2], "Mason": [5, 715.0, 3]}
    assert r["totals"] == {"jobs": 8, "sales": 1115.0, "customers": 5}
    custs = {v["value"] for v in mcp_server.find_values("all", "cust_no")}
    assert {"mason/10", "mason/12", "mason/13"} <= custs  # customers stay per store


def test_unavailable_fields_and_presets_are_clear_errors(manager):
    with pytest.raises(semantic.SpecError, match="cloud stores only have"):
        mcp_server.run_report("shop", ["jobs"], ["salesperson"])
    with pytest.raises(semantic.SpecError, match="every store has"):
        mcp_server.run_report("all", ["lines"], ["comp_kind"], dataset="components")
    with pytest.raises(ValueError, match="not available for Shop"):
        mcp_server.run_preset("shop", "salesperson")
    with pytest.raises(ValueError, match="no store 'nope'"):
        mcp_server.get_catalog("nope")
    # the on-prem store still has everything
    r = mcp_server.run_report("mason", ["jobs"], ["salesperson"])
    assert dict(map(tuple, r["rows"])) == {"ECP": 3, "BC": 2}


def test_kpis_are_the_dashboards_numbers(manager):
    r = mcp_server.get_kpis("shop", month_from="2025-07", month_to="2025-09")
    assert r["data_through"] == "2025-09-10"
    assert [k["key"] for k in r["kpis"]] == ["first_to_second_rate", "repeat_revenue_share",
                                             "median_days_to_second", "active_customers_ttm",
                                             "reactivation_rate"]
    active = next(k for k in r["kpis"] if k["key"] == "active_customers_ttm")
    assert [m["month"] for m in active["months"]] == ["2025-07", "2025-08", "2025-09"]
    assert active["months"][-1]["final"] is False  # September isn't over
    assert active["months"][-1]["value"] == 3  # Alice, Bob, Dave
    assert mcp_server.get_kpis("all", kpis=["active_customers_ttm"])["kpis"][0]["months"]
    with pytest.raises(ValueError, match="unknown KPI"):
        mcp_server.get_kpis("shop", kpis=["revenue"])


def test_sql_is_read_only(manager):
    assert mcp_server.run_sql("shop", "SELECT COUNT(*) FROM customers")["rows"] == [[4]]
    assert mcp_server.run_sql("all", "SELECT COUNT(DISTINCT store) FROM wo_facts")["rows"] == [[2]]
    with pytest.raises(sqlite3.Error):
        mcp_server.run_sql("shop", "DELETE FROM line_items")


def test_a_store_without_line_items_keeps_its_dashboard(tmp_path, manager, stores, monkeypatch):
    # a bare core warehouse (no line_items) can't get a reports DB; the dashboard doesn't care
    bare = tmp_path / "bare.db"
    sqlite3.connect(stores[0].data).execute(f"VACUUM INTO '{bare}'")
    c = sqlite3.connect(bare)
    c.execute("DROP TABLE line_items")
    c.commit()
    c.close()
    m = DataManager([Store(id="bare", name="Bare", tier="cloud", data=str(bare)), stores[1]], tmp_path / "c2")
    monkeypatch.setattr(mcp_server, "manager", m)
    snap = m.snapshot()
    assert snap.get("bare").ok and snap.get("bare").reports_error
    row = mcp_server.list_stores()["stores"][0]
    assert row["reports_unavailable"]
    with pytest.raises(ValueError, match="report data unavailable"):
        mcp_server.run_report("bare", ["jobs"])
    assert mcp_server.run_report("all", ["jobs"])["totals"] == {"jobs": 5}  # the pooled view goes on without it


# --- over HTTP, inside the website -------------------------------------------------------

H = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def _rpc(method, params=None):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}


@pytest.fixture
def http(manager, monkeypatch):
    monkeypatch.setattr(mcp_server, "TOKEN", "secret-token")
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)  # the site's login is on
    with TestClient(server.app) as c:
        yield c


def test_mcp_needs_its_token_not_the_site_login(http):
    assert http.get("/api/stores").status_code == 401  # the site still wants a login
    assert http.post("/mcp/wrong", headers=H, json=_rpc("tools/list")).status_code == 404
    assert http.post("/mcp", headers=H, json=_rpc("tools/list")).status_code == 404
    r = http.post("/mcp/secret-token", headers=H, json=_rpc("tools/list"))
    assert r.status_code == 200
    assert "get_kpis" in {t["name"] for t in r.json()["result"]["tools"]}
    r = http.post("/mcp", headers={**H, "authorization": "Bearer secret-token"},
                  json=_rpc("tools/call", {"name": "run_report",
                                           "arguments": {"store": "shop", "metrics": ["salesperson"]}}))
    assert r.json()["result"]["isError"] is True
    assert "metric 'salesperson'" in r.json()["result"]["content"][0]["text"]


def test_mcp_is_off_without_a_token(http, monkeypatch):
    monkeypatch.setattr(mcp_server, "TOKEN", "")
    assert http.post("/mcp/", headers=H, json=_rpc("tools/list")).status_code == 404
