"""Warehouse -> the JSON each page renders. Pure reads; no store-specific code.

``store_view`` is the "cloud" dashboard every store gets (and the all-stores view, which is
just the pooled warehouse): business numbers, year-over-year, the five retention KPIs.
``overview`` is the home page's side-by-side comparison. Both are computed only from the core
tables (``visits``, ``customer_lifecycle``, ``kpi_monthly``), so a cloud store and an on-prem
store are measured identically.
"""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from core.models import CALC_VERSION
from core.months import add_months, month_start
from core.store import SOURCE_WORK_ORDERS
from core.stores import ALL_STORES, Store

# --- KPI metadata ------------------------------------------------------------
#   good  -- the healthy direction
#   kind  -- cohort KPIs need matured cohorts before they mean anything; trailing ones a year
#   short -- the one-liner under the chart title; long -- the "what does this mean" tooltip
META = [
    {
        "key": "first_to_second_rate", "label": "First → second purchase", "tile": "First → second",
        "unit": "pct", "good": "up", "kind": "cohort",
        "short": "Share of a first-visit cohort that comes back within 12 months.",
        "long": (
            "Of every customer whose first-ever visit fell in a given month, what fraction "
            "returned for a second visit within 365 days? Reported as the blend of the twelve "
            "monthly cohorts whose 12-month window closed a year before the report date — one "
            "month alone is too small to trust. A customer who bought once and never came back "
            "counts against this forever. It is the highest-leverage retention number."
        ),
    },
    {
        "key": "repeat_revenue_share", "label": "Repeat share of revenue", "tile": "Repeat revenue",
        "unit": "pct", "good": "up", "kind": "trailing",
        "short": "Revenue from return visits, trailing 12 months.",
        "long": (
            "Of all revenue in the trailing 12 months, how much came from visits that were not "
            "the customer's first? A visit is one customer on one day (work orders dropped off "
            "together count once). This climbs when existing customers come back and spend — "
            "the clearest single sign retention is working."
        ),
    },
    {
        "key": "median_days_to_second", "label": "Median days to second", "tile": "Days to second",
        "unit": "days", "good": "down", "kind": "cohort",
        "short": "Typical gap between a customer's first and second visit.",
        "long": (
            "Among customers in the matured cohort who did return within a year, the median "
            "number of days between visit one and visit two. Lower means people come back "
            "sooner. It can drift up simply because slower returners keep being counted, so "
            "read it next to the first→second rate."
        ),
    },
    {
        "key": "active_customers_ttm", "label": "Active customers", "tile": "Active customers",
        "unit": "int", "good": "up", "kind": "trailing",
        "short": "Distinct customers with a visit in the last 12 months.",
        "long": (
            "How many different customers made at least one visit in the trailing 12 months — "
            "the real size of the engaged base. Someone who hasn't visited in over a year has "
            "effectively lapsed and drops out of this count."
        ),
    },
    {
        "key": "reactivated_customers_ttm", "label": "Reactivated customers", "tile": "Reactivated",
        "unit": "int", "good": "up", "kind": "cohort",
        "short": "Lapsed customers who came back, trailing 12 months.",
        "long": (
            "A customer is lapsed when they have bought before but not in the previous 365 days. "
            "This counts how many lapsed customers came back and bought in the trailing 12 "
            "months. It is a count, not a share of the lapsed pool, because that pool holds "
            "every customer who ever drifted away and only grows, so a rate over it sinks on its "
            "own. This is the number that should move when a win-back campaign goes out."
        ),
    },
]
KPI_KEYS = [m["key"] for m in META]
# Derived in the view from a stored KPI: the reactivation_rate numerator (customers who came back
# that month), summed over 12 months. Stored rows stay as they are, so frozen months need no rebuild.
REACTIVATED_TTM = "reactivated_customers_ttm"
STORED_KPIS = [k for k in KPI_KEYS if k != REACTIVATED_TTM] + ["reactivation_rate"]

METHOD = [
    "**A purchase is a visit** — one customer on one day. Several work orders dropped off "
    "together count once.",
    "**Revenue is pre-tax and after discounts**, booked to the order date. Voided orders "
    "are excluded from every figure.",
    "**Year-over-year on the open month** compares only the same run of days a year "
    "earlier, so a part-month isn't measured against a whole one. \"On pace\" is the "
    "month-to-date rate carried to month end.",
    "**Cohort metrics** (first → second, median days) use the 12 monthly "
    "cohorts that matured a year before the report month, so the sample is stable.",
    "**Closed months are frozen.** Once a month closes its retention figures never change; "
    "pick it from the period menu to see the report as it stood.",
]
METHOD_ALL = [
    "**All stores is one pooled store**, not an average: every store's visits go into one "
    "set and the same KPI math runs over it, so bigger stores weigh more, as they should.",
    "**Customers are counted per store.** Someone who shops at two stores counts twice — "
    "the stores don't share customer numbers.",
    "**Every store is cut off at the same date** — the oldest store's latest data — so the "
    "open month never mixes a complete store with a stale one.",
    "**History before a store's data begins is that store's absence**, not a drop: the "
    "monthly revenue chart is stacked by store to show where each one starts.",
]


def month_end(m: date) -> date:
    return m.replace(day=calendar.monthrange(m.year, m.month)[1])


def last_closed_month(through: date) -> date:
    """The newest month whose every day is on record."""
    return month_start(through) if through == month_end(through) else add_months(month_start(through), -1)


def _ym(d: date) -> str:
    return d.strftime("%Y-%m")


def _shift(ym: str, n: int) -> str:
    return _ym(add_months(date.fromisoformat(ym + "-01"), n))


@dataclass
class ViewConfig:
    """What a page needs to know about the store besides its warehouse."""
    id: str
    name: str
    tier: str  # cloud | on-prem | all
    chart_from: str | None = None
    plot_from: dict[str, str] = field(default_factory=dict)
    baselines: dict[str, float] = field(default_factory=dict)
    outliers: tuple[str, ...] = ()
    outlier_label: str = ""
    notes: tuple[str, ...] = ()

    @classmethod
    def of(cls, s: Store) -> ViewConfig:
        return cls(id=s.id, name=s.name, tier=s.tier, chart_from=s.chart_from,
                   plot_from=dict(s.plot_from), baselines=dict(s.baselines),
                   outliers=s.outliers, outlier_label=s.outlier_label, notes=s.notes)

    @classmethod
    def combined(cls, stores: list[Store]) -> ViewConfig:
        def earliest(vals):
            vals = [v for v in vals if v]
            return min(vals) if vals else None
        labels = sorted({s.outlier_label for s in stores if s.outliers and s.outlier_label})
        return cls(
            id=ALL_STORES, name="All stores", tier="all",
            chart_from=earliest(s.chart_from for s in stores),
            plot_from={k: v for k in ("cohort", "trailing")
                       if (v := earliest(s.plot_from.get(k) for s in stores))},
            outliers=tuple(f"{s.id}/{o}" for s in stores for o in s.outliers),
            outlier_label=" + ".join(labels),
        )


def _connect(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _series(c: sqlite3.Connection) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {k: {} for k in STORED_KPIS}
    for r in c.execute(
        "SELECT metric, month, value, numerator, denominator, is_final FROM kpi_monthly "
        "WHERE calc_version = ? ORDER BY month", (CALC_VERSION,)
    ):
        if r["metric"] in out:
            out[r["metric"]][r["month"][:7]] = {
                "v": r["value"], "n": r["numerator"], "d": r["denominator"],
                "final": bool(r["is_final"]),
            }
    out[REACTIVATED_TTM] = _trailing_count(out["reactivation_rate"])
    return out


def _trailing_count(monthly: dict[str, dict]) -> dict[str, dict]:
    """Each month's numerator summed with the 11 months before it. A month missing from the window
    predates the data, so it counts as nobody."""
    return {m: {"v": sum((monthly.get(_shift(m, -i)) or {}).get("n") or 0 for i in range(12)),
                "n": None, "d": None, "final": p["final"]}
            for m, p in monthly.items()}


def _outlier_visits(c: sqlite3.Connection, outliers: list[str]) -> list[dict]:
    """The excluded visits one by one: month, revenue, and whether it was a return visit."""
    if not outliers:
        return []
    qs = ",".join("?" * len(outliers))
    return [{"id": r["visit_id"], "m": r["visit_date"][:7], "rev": r["revenue"] or 0.0,
             "repeat": r["visit_rank"] >= 2} for r in c.execute(
        f"SELECT visit_id, visit_date, revenue, visit_rank FROM visits WHERE visit_id IN ({qs})",
        outliers)]


def _repeat_share_ex(points: dict[str, tuple[float | None, float | None]], outs: list[dict]) -> dict:
    """repeat_revenue_share with the outliers taken out of every trailing-12-month window they fall
    in: their revenue leaves the denominator, and the numerator too if they were return visits."""
    out = {}
    for m, (n, d) in points.items():
        if n is None or d is None:
            continue
        lo = _shift(m, -11)
        for o in outs:
            if lo <= o["m"] <= m:
                d -= o["rev"]
                n -= o["rev"] if o["repeat"] else 0.0
        out[m] = {"v": n / d if d > 0 else None, "n": round(n, 2), "d": round(d, 2)}
    return out


def store_view(cfg: ViewConfig, warehouse: Path, today: date) -> dict:
    """Everything the store dashboard renders, for the warehouse's data through ``today``."""
    c = _connect(warehouse)
    try:
        series = _series(c)
        first_day, last_day = c.execute("SELECT MIN(visit_date), MAX(visit_date) FROM visits").fetchone()
        first_month = first_day[:7] if first_day else _ym(today)

        cohorts = [{"m": r["m"], "n": r["n"]} for r in c.execute(
            "SELECT substr(first_visit_date, 1, 7) m, COUNT(*) n FROM customer_lifecycle "
            "GROUP BY m ORDER BY m")]
        s = dict(c.execute(
            "SELECT (SELECT COUNT(*) FROM customer_lifecycle) customers, "
            "(SELECT COUNT(*) FROM customer_lifecycle WHERE lifetime_visits >= 2) repeat_customers, "
            "(SELECT COUNT(*) FROM visits) visits, "
            "(SELECT ROUND(SUM(revenue)) FROM visits) revenue"
        ).fetchone())
        r = c.execute("SELECT value FROM sync_checkpoints WHERE name = ?", (SOURCE_WORK_ORDERS,)).fetchone()
        s["work_orders"] = int(r[0]) if r else None
        s["first_day"], s["last_day"] = first_day, last_day

        monthly = {r["m"]: {"rev": r["rev"], "v": r["v"]} for r in c.execute(
            "SELECT substr(visit_date, 1, 7) m, ROUND(SUM(revenue), 2) rev, COUNT(*) v "
            "FROM visits GROUP BY m ORDER BY m")}

        # the open month: today's month, unless the KPI history ends earlier
        all_months = sorted({mo for k in series.values() for mo in k})
        today_month = _ym(today)
        current = min(today_month, all_months[-1]) if all_months else today_month
        cy, cm = int(current[:4]), int(current[5:7])
        cutoff_day = today.day if current == today_month else calendar.monthrange(cy, cm)[1]
        ly_lo = date(cy - 1, cm, 1)
        ly_hi = date(cy - 1, cm, min(cutoff_day, calendar.monthrange(cy - 1, cm)[1]))

        outliers = list(cfg.outliers)
        qs = ",".join("?" * len(outliers)) or "''"
        ly = c.execute(
            "SELECT COALESCE(ROUND(SUM(revenue), 2), 0) rev, COUNT(*) v FROM visits "
            "WHERE visit_date BETWEEN ? AND ?", (ly_lo.isoformat(), ly_hi.isoformat())).fetchone()
        ly_ex = c.execute(
            f"SELECT COALESCE(ROUND(SUM(revenue), 2), 0) rev, COUNT(*) v FROM visits "
            f"WHERE visit_date BETWEEN ? AND ? AND visit_id NOT IN ({qs})",
            (ly_lo.isoformat(), ly_hi.isoformat(), *outliers)).fetchone()
        outlier_monthly = {r["m"]: {"rev": r["rev"] or 0.0, "v": r["v"]} for r in c.execute(
            f"SELECT substr(visit_date, 1, 7) m, ROUND(SUM(revenue), 2) rev, COUNT(*) v "
            f"FROM visits WHERE visit_id IN ({qs}) GROUP BY m ORDER BY m", outliers)}
        otot = c.execute(
            f"SELECT COALESCE(ROUND(SUM(revenue), 2), 0) rev, COUNT(*) v FROM visits "
            f"WHERE visit_id IN ({qs})", outliers).fetchone()
        outs = _outlier_visits(c, outliers)

        by_store = None
        if cfg.tier == "all":
            by_store = {}
            for r in c.execute(
                "SELECT substr(visit_id, 1, instr(visit_id, '/') - 1) sid, substr(visit_date, 1, 7) m, "
                "ROUND(SUM(revenue), 2) rev FROM visits GROUP BY sid, m ORDER BY m"
            ):
                by_store.setdefault(r["sid"], {})[r["m"]] = r["rev"]
    finally:
        c.close()

    plot_from = {
        "cohort": cfg.plot_from.get("cohort") or _shift(first_month, 16),
        "trailing": cfg.plot_from.get("trailing") or _shift(first_month, 3),
    }
    meta = [{**m, "plot_from": plot_from[m["kind"]], "baseline": cfg.baselines.get(m["key"])}
            for m in META]

    # what changes when the page excludes the outliers; the customer-count KPIs don't (the customer
    # still visited), only the revenue-weighted one does
    rrs = series["repeat_revenue_share"]
    series_ex = {"repeat_revenue_share": {
        m: {**x, "final": rrs[m]["final"]}
        for m, x in _repeat_share_ex({m: (p["n"], p["d"]) for m, p in rrs.items()}, outs).items()
    }} if outs else {}
    outlier_by_store: dict[str, dict[str, float]] = {}
    if cfg.tier == "all":
        for o in outs:
            row = outlier_by_store.setdefault(o["id"].split("/", 1)[0], {})
            row[o["m"]] = round(row.get(o["m"], 0.0) + o["rev"], 2)

    return {
        "store": {
            "id": cfg.id, "name": cfg.name, "tier": cfg.tier,
            "data_through": today.isoformat(),
            "chart_from": cfg.chart_from or first_month,
            "method": (METHOD_ALL if cfg.tier == "all" else []) + METHOD + list(cfg.notes),
        },
        "meta": meta,
        "series": series,
        "series_ex": series_ex,
        "cohorts": cohorts,
        "summary": s,
        "current_month": current,
        "business": {
            "monthly": monthly,
            "by_store": by_store,
            "all_time_revenue": s["revenue"],
            "first_day": first_day,
            "partial_month": current,
            "partial_day": cutoff_day,
            "days_in_partial": calendar.monthrange(cy, cm)[1],
            "partial_ly_rev": ly["rev"], "partial_ly_v": ly["v"],
            "partial_ly_rev_ex": ly_ex["rev"], "partial_ly_v_ex": ly_ex["v"],
            "outlier": {
                "label": cfg.outlier_label,
                "monthly": outlier_monthly,
                "months": list(outlier_monthly),
                "total_rev": otot["rev"], "total_v": otot["v"],
                "by_store": outlier_by_store,
            },
        },
        "calc_version": CALC_VERSION,
    }


# --- overview: stores side by side ------------------------------------------------

PERIODS = ("month", "ytd", "ttm")


def _period_bounds(kind: str, report: date) -> tuple[date, date]:
    if kind == "month":
        return report, month_end(report)
    if kind == "ytd":
        return report.replace(month=1), month_end(report)
    return add_months(report, -11), month_end(report)


def _ly(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year - 1, day=28)


def _period_stats(c: sqlite3.Connection, lo: date, hi: date, outliers: list[str]) -> dict:
    qs = ",".join("?" * len(outliers)) or "''"
    q = (f"SELECT COALESCE(SUM(revenue), 0) rev, COUNT(*) v, "
         f"COALESCE(SUM(CASE WHEN visit_id IN ({qs}) THEN revenue END), 0) xrev, "
         f"COUNT(CASE WHEN visit_id IN ({qs}) THEN 1 END) xv, "
         f"COUNT(CASE WHEN is_first_visit THEN 1 END) new "
         f"FROM visits WHERE visit_date BETWEEN ? AND ?")
    cur = c.execute(q, (*outliers, *outliers, lo.isoformat(), hi.isoformat())).fetchone()
    prev = c.execute(q, (*outliers, *outliers, _ly(lo).isoformat(), _ly(hi).isoformat())).fetchone()
    return {
        "rev": round(cur["rev"], 2), "v": cur["v"], "new": cur["new"],
        "rev_ex": round(cur["rev"] - cur["xrev"], 2), "v_ex": cur["v"] - cur["xv"],
        "ly_rev": round(prev["rev"], 2), "ly_v": prev["v"], "ly_new": prev["new"],
        "ly_rev_ex": round(prev["rev"] - prev["xrev"], 2), "ly_v_ex": prev["v"] - prev["xv"],
    }


def overview_row(cfg: ViewConfig, warehouse: Path, report: date, history_from: date) -> dict:
    c = _connect(warehouse)
    try:
        outliers = list(cfg.outliers)
        periods = {k: _period_stats(c, *_period_bounds(k, report), outliers) for k in PERIODS}
        kpis = {}
        for r in c.execute(
            "SELECT metric, value FROM kpi_monthly WHERE calc_version = ? AND month = ?",
            (CALC_VERSION, report.isoformat())
        ):
            kpis[r["metric"]] = r["value"]
        hist = {}
        for r in c.execute(
            "SELECT metric, substr(month, 1, 7) m, value FROM kpi_monthly WHERE calc_version = ? "
            "AND metric IN ('active_customers_ttm', 'repeat_revenue_share') AND month BETWEEN ? AND ? "
            "ORDER BY month", (CALC_VERSION, history_from.isoformat(), report.isoformat())
        ):
            hist.setdefault(r["metric"], {})[r["m"]] = r["value"]
        monthly = {r["m"]: {"rev": r["rev"], "v": r["v"]} for r in c.execute(
            "SELECT substr(visit_date, 1, 7) m, ROUND(SUM(revenue), 2) rev, COUNT(*) v FROM visits "
            "WHERE visit_date BETWEEN ? AND ? GROUP BY m ORDER BY m",
            (add_months(history_from, -12).isoformat(), month_end(report).isoformat()))}
        first_day = c.execute("SELECT MIN(visit_date) FROM visits").fetchone()[0]
        outs = _outlier_visits(c, outliers)
        rrs = {r["m"]: (r["numerator"], r["denominator"]) for r in c.execute(
            "SELECT substr(month, 1, 7) m, numerator, denominator FROM kpi_monthly WHERE calc_version = ? "
            "AND metric = 'repeat_revenue_share' AND month BETWEEN ? AND ?",
            (CALC_VERSION, history_from.isoformat(), report.isoformat()))} if outs else {}
    finally:
        c.close()
    return {
        "id": cfg.id, "name": cfg.name, "tier": cfg.tier, "first_day": first_day,
        "outlier_label": cfg.outlier_label if outliers else "",
        "periods": periods, "kpis": kpis, "history": hist, "monthly": monthly,
        **_overview_ex(outs, rrs, report),
    }


def _overview_ex(outs: list[dict], rrs: dict, report: date) -> dict:
    """The parts of an overview row that change when the page excludes the outliers."""
    monthly: dict[str, dict] = {}
    for o in outs:
        x = monthly.setdefault(o["m"], {"rev": 0.0, "v": 0})
        x["rev"] = round(x["rev"] + o["rev"], 2)
        x["v"] += 1
    rrs_ex = {m: x["v"] for m, x in _repeat_share_ex(rrs, outs).items()}
    return {
        "outlier_monthly": monthly,
        "kpis_ex": {"repeat_revenue_share": rrs_ex[_ym(report)]} if _ym(report) in rrs_ex else {},
        "history_ex": {"repeat_revenue_share": rrs_ex} if rrs_ex else {},
    }


STALE_DAYS = 35  # a store this far behind the freshest one is flagged on the home page


def overview(snapshot) -> dict:
    ok = [s for s in snapshot.stores if s.ok]
    through = snapshot.combined_through
    report = last_closed_month(through) if through else None
    history_from = add_months(report, -23) if report else None
    newest = max((s.data_through for s in ok), default=None)
    rows = []
    if report is not None:
        rows = [overview_row(ViewConfig.of(s.store), s.warehouse_path, report, history_from)
                for s in ok]
        combined = overview_row(ViewConfig.combined([s.store for s in ok]),
                                snapshot.combined_path, report, history_from)
    else:
        combined = None
    return {
        "report_month": _ym(report) if report else None,
        "report_through": month_end(report).isoformat() if report else None,
        "common_through": through.isoformat() if through else None,
        "history_from": _ym(history_from) if history_from else None,
        "stores": rows,
        "combined": combined,
        "stale": [{"id": s.store.id, "name": s.store.name, "data_through": s.data_through.isoformat(),
                   "days_behind": (newest - s.data_through).days}
                  for s in ok if (newest - s.data_through).days > STALE_DAYS],
        "unavailable": [{"id": s.store.id, "name": s.store.name, "error": s.error}
                        for s in snapshot.stores if not s.ok],
    }

