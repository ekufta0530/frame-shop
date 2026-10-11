"""LifeSaver desktop (Visual FoxPro .dbf) files -> analysis-ready ``lifesaver.sqlite``.

    python -m sqlite_extract.extract <LifeSaver dir | zip> lifesaver.sqlite

The owner's data arrives as a copy of the whole LifeSaver install folder (8k+
files: program files, images, printer configs...). Only ``NEEDED`` is read.
A zip is never extracted wholesale: ``unpack_needed`` copies just those members
out, under flat lowercase names, so archive paths can't escape the destination.

``wo_facts`` (one row per work order, latest revision) is what
``sqlite_extract.importer`` and the SQL MCP server read.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import tempfile
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path, PurePosixPath

import dbfread

# the tables the converter reads (+ ticket's memo file, which holds FULFIELDS)
NEEDED = (
    "ticket.dbf", "ticket.fpt", "lifecust.dbf", "billsum.dbf", "bill.dbf",
    "payment.dbf", "paydetail.dbf", "moulding.dbf", "mat.dbf",
)
# the largest real member is ticket.dbf at ~40 MB; anything far bigger is not LifeSaver data
MAX_MEMBER_BYTES = 2 * 1024**3

UNUSED = re.compile(r"^FUNUSED\d+$")
# ticket memo fields that are always empty in this store's data
EMPTY_MEMOS = {"FMWFIELDS", "FPIFIMAGE", "FFVDATA", "FPCVAR"}


class SourceError(ValueError):
    """The upload doesn't contain a usable LifeSaver data folder."""


def find_needed(names: list[str]) -> dict[str, str]:
    """Map each NEEDED file to its path in ``names`` (zip members or relative paths).

    Uses the folder holding ``ticket.dbf`` (the shallowest, if a backup nests
    another copy) and matches names case-insensitively -- the install mixes
    ``Paydetail.dbf`` with ``paydetail.CDX``.
    """
    tickets = [n for n in names if PurePosixPath(n).name.lower() == "ticket.dbf"]
    if not tickets:
        raise SourceError("no ticket.dbf found -- is this a LifeSaver folder?")
    folder = PurePosixPath(min(tickets, key=lambda n: (n.count("/"), n))).parent
    found = {}
    for n in names:
        p = PurePosixPath(n)
        if p.parent == folder and p.name.lower() in NEEDED:
            found[p.name.lower()] = n
    missing = [n for n in NEEDED if n not in found]
    if missing:
        raise SourceError(f"missing from {folder}/: {', '.join(missing)}")
    return found


def unpack_needed(zip_path: str | Path, dest: str | Path) -> Path:
    """Copy just the NEEDED members of a LifeSaver zip into ``dest`` (flat, lowercase)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        infos = {i.filename: i for i in z.infolist() if not i.is_dir()}
        for name, member in find_needed(list(infos)).items():
            if infos[member].file_size > MAX_MEMBER_BYTES:
                raise SourceError(f"{member} is implausibly large ({infos[member].file_size:,} bytes)")
            with z.open(member) as src, open(dest / name, "wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
    return dest


def _resolve(src: Path, name: str) -> Path:
    for p in src.iterdir():
        if p.name.lower() == name:
            return p
    raise SourceError(f"{name} not found in {src}")


def _read(src: Path, name: str) -> list[dict]:
    t = dbfread.DBF(_resolve(src, name), char_decode_errors="replace", ignore_missing_memofile=True)
    return [dict(r) for r in t]


def _clean(v):
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, datetime):
        return v.isoformat(" ")
    if isinstance(v, date):
        return v.isoformat()
    return v


def _write(con, table, rows, drop=lambda k: False) -> int:
    rows = [{k: _clean(v) for k, v in r.items() if not drop(k)} for r in rows]
    if not rows:
        return 0
    cols = list(rows[0])
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(f'CREATE TABLE {table} ({", ".join(c.lower() for c in cols)})')
    con.executemany(f'INSERT INTO {table} VALUES ({",".join("?" * len(cols))})',
                    [[r[c] for c in cols] for r in rows])
    return len(rows)


def parse_components(r: dict) -> list[dict]:
    """FULFIELDS is CR-separated 'Kind: k=v;k=v' lines describing each component of the frame job."""
    out = []
    for line in (r.get("FULFIELDS") or "").split("\r"):
        kind, sep, body = line.partition(": ")
        if not sep or kind in ("LsVersion", "Ticket", "MldgInside"):
            continue
        kv = dict(p.split("=", 1) for p in body.split(";") if "=" in p)
        num = lambda k: float(kv[k]) if kv.get(k) not in (None, "") and re.fullmatch(r"-?[\d.]+", kv[k]) else None
        out.append({
            "wo_no": r["FWKORDERNO"], "revision": r["FREVISION"], "kind": kind,
            "name": kv.get("name"), "vendor": kv.get("vendor"), "category": kv.get("category"),
            "layer": num("layerNumber"), "quantity": num("quantity"), "feet": num("feet"),
            "list_price": num("prediscountprice"), "cost": num("cost"), "discount": num("discount"),
            "width_in": num("mouldingWidth"), "raw": body,
        })
    return out


def parse_ticket_header(r: dict) -> dict:
    m = re.search(r"Ticket: ([^\r]*)", r.get("FULFIELDS") or "")
    kv = dict(p.split("=", 1) for p in m.group(1).split(";") if "=" in p) if m else {}
    f = lambda k: float(kv[k]) if kv.get(k) else None
    v = re.search(r"LsVersion: ([^\r\s]+)", r.get("FULFIELDS") or "")
    return {"finished_h": f("finishedHeight"), "finished_w": f("finishedWidth"),
            "opening_h": f("openingHeight"), "opening_w": f("openingWidth"),
            "ls_version": v.group(1) if v else None}


VIEWS = """
DROP VIEW IF EXISTS work_orders;
CREATE VIEW work_orders AS
  SELECT * FROM work_order_revisions WHERE is_latest = 1;
DROP VIEW IF EXISTS work_order_components_current;
CREATE VIEW work_order_components_current AS
  SELECT c.* FROM work_order_components c
  JOIN work_orders w ON w.fwkorderno = c.wo_no AND w.frevision = c.revision;
DROP VIEW IF EXISTS wo_facts;
CREATE VIEW wo_facts AS
  SELECT w.fwkorderno AS wo_no, w.finvoiceno AS invoice_no, w.fcustno AS cust_no,
    w.fdate AS order_date, w.fdatepr AS promised_date, w.fdonedate AS done_date,
    w.fdelivdate AS delivered_date, w.fstatustxt AS status,
    -- salesperson is free text ('ECP', 'ECP - Christ', 'bc - bernie'); keep the initials
    CASE WHEN trim(coalesce(w.fsp, '')) = '' THEN '(none)'
         ELSE upper(trim(CASE WHEN instr(w.fsp, ' - ') > 0
                              THEN substr(w.fsp, 1, instr(w.fsp, ' - ') - 1) ELSE w.fsp END)) END AS salesperson,
    -- account name from the customer file; the ticket's name is just the contact person
    coalesce(nullif(trim(coalesce(c.fname, '') || ' ' || coalesce(c.lname, '')), ''), c.company,
             trim(coalesce(w.ffname, '') || ' ' || coalesce(w.flname, ''))) AS customer_name,
    coalesce(c.company, w.fcompany) AS company, c.city AS customer_city, c.zip5 AS customer_zip,
    w.fdesc AS description, w.fcomment AS comment,
    w.fsubt AS subtotal, w.ftax AS tax, w.ftotal AS total,
    w.flistsubt AS list_subtotal, w.fdiscamt AS discount_amt, w.fdisc AS discount_code,
    w.fmouldpr AS moulding_price, w.fmatspr AS mat_price, w.fglasspr AS glass_price,
    w.fmountpr AS mount_price, w.flaborpr AS labor_price, w.fspecialpr AS special_price,
    -- LifeSaver only records true cost for moulding/mat/fillet; glazing, specials etc. store list price
    -- as "cost" (and the ticket-level cost fields inherit that), so only these three are trusted.
    coalesce(k.known_cost, 0) AS known_cost, coalesce(k.known_net, 0) AS known_cost_sales,
    w.finished_w, w.finished_h, w.rev_count,
    julianday(w.fdonedate) - julianday(w.fdate) AS days_to_done,
    CASE WHEN w.fdate = min(w.fdate) OVER (PARTITION BY w.fcustno) THEN 'New' ELSE 'Returning' END
      AS customer_type
  FROM work_orders w
  LEFT JOIN customers c ON c.cnumber = w.fcustno
  LEFT JOIN (SELECT wo_no, SUM(cost) AS known_cost, SUM(list_price - coalesce(discount, 0)) AS known_net
             FROM work_order_components_current WHERE kind IN ('Moulding', 'Mat', 'Fillet')
             GROUP BY wo_no) k ON k.wo_no = w.fwkorderno;
DROP VIEW IF EXISTS comp_facts;
CREATE VIEW comp_facts AS
  SELECT f.*, c.kind AS comp_kind, c.name AS comp_name, coalesce(c.vendor, '(none)') AS comp_vendor,
    c.feet AS comp_feet, c.quantity AS comp_qty, c.list_price AS comp_list_price,
    CASE WHEN c.kind IN ('Moulding', 'Mat', 'Fillet') THEN c.cost END AS comp_cost,
    c.list_price - coalesce(c.discount, 0) AS comp_net_price
  FROM work_order_components_current c JOIN wo_facts f ON f.wo_no = c.wo_no
  -- Glazing/Mounting/Stretching/ArtCondition rows exist on every WO even when unused
  WHERE c.kind != 'ArtCondition' AND coalesce(c.list_price, 0) + coalesce(c.cost, 0) > 0;
CREATE INDEX IF NOT EXISTS ix_rev_wo ON work_order_revisions(fwkorderno, frevision);
CREATE INDEX IF NOT EXISTS ix_comp_wo ON work_order_components(wo_no, revision);
"""


def extract(src: str | Path, out: str | Path) -> dict[str, int]:
    """Build ``out`` from a folder holding the NEEDED files. Returns rows per table."""
    src, out = Path(src), Path(out)
    out.unlink(missing_ok=True)
    tickets = _read(src, "ticket.dbf")

    # Every save of a WO appends a new row with FREVISION+1; latest revision = current state.
    by_wo = defaultdict(list)
    for r in tickets:
        by_wo[r["FWKORDERNO"]].append(r)
    latest_ids = {id(max(v, key=lambda r: r["FREVISION"])) for v in by_wo.values()}

    drop = lambda k: bool(UNUSED.match(k)) or k in EMPTY_MEMOS
    revs = []
    for r in tickets:
        row = dict(r)
        row["IS_LATEST"] = id(r) in latest_ids
        row["REV_COUNT"] = len(by_wo[r["FWKORDERNO"]])
        row.update({k.upper(): v for k, v in parse_ticket_header(r).items()})
        revs.append(row)

    con = sqlite3.connect(out)
    try:
        counts = {
            "work_order_revisions": _write(con, "work_order_revisions", revs, drop),
            "work_order_components": _write(
                con, "work_order_components", [c for r in tickets for c in parse_components(r)]),
            "customers": _write(con, "customers", _read(src, "lifecust.dbf")),
            "invoices": _write(con, "invoices", _read(src, "billsum.dbf")),
            "invoice_lines": _write(con, "invoice_lines", _read(src, "bill.dbf")),
            "payments": _write(con, "payments", _read(src, "payment.dbf"),
                               lambda k: k.startswith(("PYMX", "PYEV", "PYCCACCTNM"))),  # card-processor fields
            "payment_detail": _write(con, "payment_detail", _read(src, "paydetail.dbf")),
            "mouldings": _write(con, "mouldings", _read(src, "moulding.dbf")),
            "mats": _write(con, "mats", _read(src, "mat.dbf")),
        }
        con.executescript(VIEWS)
        con.commit()
    finally:
        con.close()
    return counts


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", help="LifeSaver folder, or a zip of one")
    ap.add_argument("out", help="SQLite file to (re)build")
    args = ap.parse_args(argv)
    src = Path(args.src)
    with tempfile.TemporaryDirectory() as tmp:
        if src.suffix.lower() == ".zip":
            src = unpack_needed(src, tmp)
        for table, n in extract(src, args.out).items():
            print(f"{table:28s} {n:>9,} rows")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
