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

from .views import VIEWS

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
