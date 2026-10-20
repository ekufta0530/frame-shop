"""The store registry: ``stores.toml`` at the repo root, parsed and validated.

Everything that needs to know which stores exist -- the website, the cloud
refresh workflow, and later the unified MCP server -- reads it from here, so
adding a store is a config change, never a code change.

    python -m core.stores                   # validate + list
    python -m core.stores --tier cloud --matrix   # JSON for a GitHub Actions matrix

``STORES_FILE`` overrides the file's location (tests use it). ``STORE_<ID>_DATA``
overrides one store's data location, e.g. ``STORE_MAIN_DATA=warehouse.db`` to
develop against a local copy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CLOUD = "cloud"
ON_PREM = "on-prem"
TIERS = (CLOUD, ON_PREM)

DEFAULT_FILE = Path(__file__).resolve().parent.parent / "stores.toml"
CLOUD_WAREHOUSE = "gs://frame-shop-511616-kpi-warehouse/stores/{id}/warehouse.db"
CLOUD_SECRET = "lifesaver-store-{id}"

# Reserved: the pooled all-stores view uses this id.
ALL_STORES = "all"

_ID = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_KNOWN_KEYS = {
    "id", "name", "tier", "data", "secret", "chart_from", "plot_from", "baselines",
    "outliers", "outlier_label", "notes", "exclude_invoices",
}
_KPIS = {
    "first_to_second_rate", "repeat_revenue_share", "median_days_to_second",
    "active_customers_ttm", "reactivation_rate",
}


class StoreConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Store:
    id: str
    name: str
    tier: str
    data: str  # gs://... or a local path: warehouse.db (cloud) / lifesaver.sqlite (on-prem)
    secret: str | None = None
    chart_from: str | None = None
    plot_from: dict[str, str] = field(default_factory=dict)
    baselines: dict[str, float] = field(default_factory=dict)
    outliers: tuple[str, ...] = ()
    outlier_label: str = ""
    notes: tuple[str, ...] = ()
    # cloud only: invoices that were never sales (test orders), left out of every figure
    exclude_invoices: tuple[int, ...] = ()

    @property
    def on_prem(self) -> bool:
        return self.tier == ON_PREM


def _fail(where: str, msg: str):
    raise StoreConfigError(f"stores.toml, {where}: {msg}")


def _parse(raw: dict, i: int) -> Store:
    where = f"store #{i + 1}"
    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        _fail(where, f"unknown keys {sorted(unknown)}")
    sid = raw.get("id")
    if not isinstance(sid, str) or not _ID.match(sid):
        _fail(where, "id must be lowercase letters, digits or '-', starting with a letter")
    if sid == ALL_STORES:
        _fail(where, f"id '{ALL_STORES}' is reserved for the all-stores view")
    where = f"store '{sid}'"
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        _fail(where, "name is required")
    tier = raw.get("tier")
    if tier not in TIERS:
        _fail(where, f"tier must be one of {TIERS}")
    data = raw.get("data")
    if data is None:
        if tier == ON_PREM:
            _fail(where, "on-prem stores need data = the published lifesaver.sqlite")
        data = CLOUD_WAREHOUSE.format(id=sid)
    data = os.environ.get(f"STORE_{sid.upper().replace('-', '_')}_DATA", data)
    for key in ("chart_from",):
        if raw.get(key) is not None and not _MONTH.match(str(raw[key])):
            _fail(where, f"{key} must be YYYY-MM")
    plot_from = dict(raw.get("plot_from") or {})
    for k, v in plot_from.items():
        if k not in ("cohort", "trailing") or not _MONTH.match(str(v)):
            _fail(where, "plot_from takes cohort / trailing = YYYY-MM")
    baselines = dict(raw.get("baselines") or {})
    for k in baselines:
        if k not in _KPIS:
            _fail(where, f"baselines: unknown KPI {k!r}")
    exclude = raw.get("exclude_invoices") or []
    if exclude and tier != CLOUD:
        _fail(where, "exclude_invoices is for cloud stores (an on-prem store voids the ticket instead)")
    if not isinstance(exclude, list) or not all(isinstance(n, int) and not isinstance(n, bool)
                                                 for n in exclude):
        _fail(where, "exclude_invoices must be a list of invoice numbers")
    return Store(
        id=sid,
        name=name.strip(),
        tier=tier,
        data=str(data),
        secret=raw.get("secret") or (CLOUD_SECRET.format(id=sid) if tier == CLOUD else None),
        chart_from=raw.get("chart_from"),
        plot_from=plot_from,
        baselines={k: float(v) for k, v in baselines.items()},
        outliers=tuple(raw.get("outliers") or ()),
        outlier_label=raw.get("outlier_label") or "",
        notes=tuple(raw.get("notes") or ()),
        exclude_invoices=tuple(exclude),
    )


def load_stores(path: str | Path | None = None) -> list[Store]:
    path = Path(path or os.environ.get("STORES_FILE") or DEFAULT_FILE)
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    stores = [_parse(raw, i) for i, raw in enumerate(doc.get("store") or [])]
    if not stores:
        raise StoreConfigError(f"{path}: no [[store]] entries")
    ids = [s.id for s in stores]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise StoreConfigError(f"{path}: duplicate store ids {sorted(dupes)}")
    return stores


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate and list stores.toml")
    ap.add_argument("--tier", choices=TIERS)
    ap.add_argument("--matrix", action="store_true",
                    help="print a GitHub Actions matrix: {\"include\": [{id, name, data, secret}]}")
    args = ap.parse_args(argv)
    stores = [s for s in load_stores() if args.tier in (None, s.tier)]
    if args.matrix:
        print(json.dumps({"include": [
            {"id": s.id, "name": s.name, "data": s.data, "secret": s.secret or "",
             "exclude_invoices": ",".join(map(str, s.exclude_invoices))} for s in stores
        ]}))
        return 0
    for s in stores:
        print(f"{s.id:<12} {s.tier:<8} {s.name:<24} {s.data}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
