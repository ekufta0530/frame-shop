"""The on-prem section: deeper reports only a full LifeSaver database can answer.

Each entry is a report spec for ``lsreports.semantic`` plus how to draw it. They run against
the store's own ``lifesaver.sqlite`` and are dated relative to its data-through date (the
database is a snapshot, so "the last 12 months" means the 12 months before its newest order).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from core.months import add_months
from lsreports import db, semantic


def _specs(through: date) -> list[dict]:
    last12 = add_months(through, -12).isoformat()
    five_years = date(through.year - 5, 1, 1).isoformat()
    upto = through.isoformat()
    return [
        {
            "id": "category_mix", "title": "Sales by category",
            "blurb": "Moulding, mat, glazing and labor revenue per year — where the money comes from.",
            "chart": "stacked", "wide": True,
            "spec": {"dimensions": ["year"], "date_from": five_years, "date_to": upto,
                     "metrics": ["moulding_sales", "mat_sales", "glass_sales", "labor_sales"]},
        },
        {
            "id": "salesperson", "title": "Salespeople, last 12 months",
            "blurb": "Sales written, average ticket, discounting and moulding & mat margin per person.",
            "chart": "hbar", "metric": "sales",
            "spec": {"dimensions": ["salesperson"], "date_from": last12, "date_to": upto,
                     "metrics": ["sales", "jobs", "avg_ticket", "discount_pct", "mm_margin_pct"],
                     "limit": 12},
        },
        {
            "id": "ticket_size", "title": "Ticket size mix, last 12 months",
            "blurb": "How jobs split across price bands.",
            "chart": "bar", "metric": "jobs",
            "spec": {"dimensions": ["price_band"], "date_from": last12, "date_to": upto,
                     "metrics": ["jobs", "sales"]},
        },
        {
            "id": "turnaround", "title": "Turnaround, last 12 months",
            "blurb": "Days from order to done. Jobs still in the shop show as not done.",
            "chart": "bar", "metric": "jobs",
            "spec": {"dimensions": ["turnaround_band"], "date_from": last12, "date_to": upto,
                     "metrics": ["jobs"]},
        },
        {
            "id": "vendors", "title": "Moulding vendors, last 12 months",
            "blurb": "Net moulding sales by vendor, with the margin LifeSaver can measure.",
            "chart": "hbar", "metric": "net_sales",
            "spec": {"dataset": "components", "dimensions": ["comp_vendor"], "date_from": last12,
                     "date_to": upto, "metrics": ["net_sales", "lines", "margin_pct"], "limit": 10,
                     "filters": [{"field": "comp_kind", "op": "in", "value": ["Moulding"]}]},
        },
        {
            "id": "new_vs_returning", "title": "New vs returning customers",
            "blurb": "Jobs from first-time and repeat customers, per year.",
            "chart": "stacked_dim", "metric": "jobs",
            "spec": {"dimensions": ["year", "customer_type"], "date_from": five_years,
                     "date_to": upto, "metrics": ["jobs"]},
        },
        {
            "id": "discounting", "title": "Discounting by year",
            "blurb": "Discount dollars as a share of list price.",
            "chart": "line", "metric": "discount_pct",
            "spec": {"dimensions": ["year"], "date_from": five_years, "date_to": upto,
                     "metrics": ["discount_pct", "discounts"]},
        },
    ]


def insights(sqlite_path: Path, through: date) -> dict:
    out = []
    with db.using(sqlite_path):
        info = db.data_info()
        for item in _specs(through):
            spec = {"dataset": "work_orders", **item["spec"]}
            try:
                res = semantic.run_report(spec)
                res.pop("sql", None)
                res.pop("params", None)
                out.append({**{k: v for k, v in item.items() if k != "spec"}, "spec": spec, "result": res})
            except Exception as e:  # one bad report shouldn't blank the section
                out.append({**{k: v for k, v in item.items() if k != "spec"}, "spec": spec,
                            "error": str(e)})
    return {
        "work_orders": info["work_orders"],
        "latest_order_date": info["latest_order_date"],
        "reports": out,
    }
