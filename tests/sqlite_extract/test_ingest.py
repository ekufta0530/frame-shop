"""LifeSaver .dbf -> lifesaver.sqlite conversion and the Drive-folder ingest around it.

The fixtures are tiny FoxPro tables written by ``write_dbf`` (real data is
customer PII and never committed), laid out like the owner's Drive zip: the
whole install folder under ``LifeSaver/``, needed tables mixed in with junk.
"""

import shutil
import sqlite3
import struct
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from sqlite_extract import drive as drive_mod
from sqlite_extract import ingest
from sqlite_extract.drive import Drive, Upload, find_uploads
from sqlite_extract.extract import NEEDED, SourceError, extract, find_needed, unpack_needed

# ---- a minimal dBase/FoxPro writer --------------------------------------------


def write_dbf(path: Path, fields: list[tuple], rows: list[dict]) -> None:
    """fields: (name, type, length, decimals); type C/N/D/M. M fields go to a sibling .fpt."""
    memos, block = [], 64
    def memo(text):
        if not text:
            return struct.pack("<I", 0)
        memos.append(text.encode())
        return struct.pack("<I", 512 // block + sum(-(-(8 + len(m)) // block) for m in memos[:-1]))

    def cell(f, v):
        name, typ, length, dec = f
        if typ == "M":
            return memo(v)
        if typ == "D":
            return (v.strftime("%Y%m%d") if v else "").ljust(8).encode()
        if typ == "N":
            return ("" if v is None else f"{v:.{dec}f}").rjust(length).encode()
        return (v or "").ljust(length)[:length].encode()

    recs = [b" " + b"".join(cell(f, r.get(f[0])) for f in fields) for r in rows]
    reclen = 1 + sum(f[2] for f in fields)
    head = struct.pack("<BBBBLHH20x", 0x30, 126, 9, 1, len(rows), 32 + 32 * len(fields) + 1, reclen)
    descs = b"".join(struct.pack("<11scLBB14x", n.encode(), t.encode(), 0, ln, d) for n, t, ln, d in fields)
    path.write_bytes(head + descs + b"\r" + b"".join(recs) + b"\x1a")
    if any(f[1] == "M" for f in fields):
        body = b"".join(struct.pack(">LL", 1, len(m)) + m + b"\0" * (-(8 + len(m)) % block) for m in memos)
        nxt = (512 + len(body)) // block
        path.with_suffix(".fpt").write_bytes(struct.pack(">LHH504x", nxt, 0, block) + body)


TICKET_FIELDS = [
    ("FWKORDERNO", "N", 8, 0), ("FREVISION", "N", 4, 0), ("FINVOICENO", "N", 8, 0), ("FCUSTNO", "N", 8, 0),
    ("FDATE", "D", 8, 0), ("FDATEPR", "D", 8, 0), ("FDONEDATE", "D", 8, 0), ("FDELIVDATE", "D", 8, 0),
    ("FSTATUSTXT", "C", 12, 0), ("FSP", "C", 20, 0), ("FFNAME", "C", 12, 0), ("FLNAME", "C", 12, 0),
    ("FCOMPANY", "C", 12, 0), ("FDESC", "C", 20, 0), ("FCOMMENT", "C", 20, 0),
    ("FSUBT", "N", 10, 2), ("FTAX", "N", 10, 2), ("FTOTAL", "N", 10, 2), ("FLISTSUBT", "N", 10, 2),
    ("FDISCAMT", "N", 10, 2), ("FDISC", "C", 4, 0), ("FMOULDPR", "N", 10, 2), ("FMATSPR", "N", 10, 2),
    ("FGLASSPR", "N", 10, 2), ("FMOUNTPR", "N", 10, 2), ("FLABORPR", "N", 10, 2),
    ("FSPECIALPR", "N", 10, 2), ("FUNUSED1", "C", 2, 0), ("FULFIELDS", "M", 4, 0),
]
FULL = ("LsVersion: 5.2\rTicket: finishedHeight=20;finishedWidth=16\r"
        "Moulding: name=M1;vendor=Larson;prediscountprice=100;cost=40;discount=10;feet=6\r"
        "Mat: name=White;vendor=Crescent;prediscountprice=30;cost=5\r"
        "Glazing: name=Clear;prediscountprice=25;cost=25")


def make_lifesaver(folder: Path, *, wo_count: int = 2, last_date: date = date(2026, 9, 14)) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    tickets = []
    for n in range(1, wo_count + 1):
        base = {"FWKORDERNO": n, "FCUSTNO": 10 + n % 2, "FDATE": last_date - timedelta(days=wo_count - n),
                "FSUBT": 150.0, "FSTATUSTXT": "Delivered", "FSP": "ECP - Christ", "FULFIELDS": FULL}
        tickets += [{**base, "FREVISION": 1, "FSUBT": 99.0}, {**base, "FREVISION": 2}]  # rev 2 is current
    write_dbf(folder / "ticket.dbf", TICKET_FIELDS, tickets)
    write_dbf(folder / "lifecust.dbf",
              [("CNUMBER", "N", 8, 0), ("FNAME", "C", 10, 0), ("LNAME", "C", 10, 0), ("COMPANY", "C", 10, 0),
               ("CITY", "C", 10, 0), ("ZIP5", "C", 5, 0)],
              [{"CNUMBER": 10, "FNAME": "Ann", "LNAME": "Lee"}, {"CNUMBER": 11, "COMPANY": "Acme"}])
    for name in ("billsum", "bill", "payment", "Paydetail", "moulding", "mat"):  # mixed case, as shipped
        write_dbf(folder / f"{name}.dbf", [("ID", "N", 4, 0), ("PYMXTOKEN", "C", 4, 0)], [{"ID": 1}])
    (folder / "Prisma.exe").write_bytes(b"MZ junk")
    write_dbf(folder / "suspend.dbf", [("ID", "N", 4, 0)], [{"ID": 1}])
    return folder


def zip_dir(folder: Path, out: Path, prefix: str = "LifeSaver/", extra: dict | None = None) -> Path:
    with zipfile.ZipFile(out, "w") as z:
        for p in folder.iterdir():
            z.write(p, prefix + p.name)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return out


# ---- extract ------------------------------------------------------------------


def test_extract_builds_wo_facts_from_latest_revision(tmp_path):
    src = make_lifesaver(tmp_path / "ls")
    counts = extract(src, tmp_path / "out.sqlite")
    assert counts["work_order_revisions"] == 4
    c = sqlite3.connect(tmp_path / "out.sqlite")
    rows = c.execute("SELECT wo_no, subtotal, salesperson, customer_name, order_date, known_cost, "
                     "known_cost_sales FROM wo_facts ORDER BY wo_no").fetchall()
    assert rows == [(1, 150.0, "ECP", "Acme", "2026-09-13", 45.0, 120.0),
                    (2, 150.0, "ECP", "Ann Lee", "2026-09-14", 45.0, 120.0)]
    # glazing "cost" is list price, so it's excluded from known cost; comp_facts keeps it as a component
    kinds = {k for (k,) in c.execute("SELECT DISTINCT comp_kind FROM comp_facts")}
    assert kinds == {"Moulding", "Mat", "Glazing"}
    cols = {r[1] for r in c.execute("PRAGMA table_info(work_order_revisions)")}
    assert "funused1" not in cols and "finished_h" in cols
    assert "pymxtoken" not in {r[1] for r in c.execute("PRAGMA table_info(payments)")}


def test_find_needed_picks_shallowest_ticket_folder_case_insensitively():
    names = [f"LifeSaver/{n}" for n in NEEDED if n != "paydetail.dbf"] + ["LifeSaver/Paydetail.dbf"]
    names += ["LifeSaver/Old/ticket.dbf", "LifeSaver/Old/lifecust.dbf"]
    found = find_needed(names)
    assert found["paydetail.dbf"] == "LifeSaver/Paydetail.dbf"
    assert found["ticket.dbf"] == "LifeSaver/ticket.dbf"


def test_find_needed_rejects_incomplete_or_unrelated_uploads():
    with pytest.raises(SourceError, match="no ticket.dbf"):
        find_needed(["photos/a.jpg"])
    with pytest.raises(SourceError, match="ticket.fpt"):
        find_needed(["L/ticket.dbf", "L/lifecust.dbf"])


def test_unpack_only_copies_needed_files_and_ignores_archive_paths(tmp_path):
    src = make_lifesaver(tmp_path / "ls")
    evil = {"../../escape.txt": "x"}
    z = zip_dir(src, tmp_path / "up.zip", prefix="../../", extra=evil)  # every member tries to escape
    dest = unpack_needed(z, tmp_path / "deep" / "raw")
    assert sorted(p.name for p in dest.iterdir()) == sorted(NEEDED)
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "ticket.dbf").exists()


def test_extract_cli_accepts_drive_style_zip(tmp_path, capsys):
    from sqlite_extract.extract import main
    z = zip_dir(make_lifesaver(tmp_path / "ls"), tmp_path / "LifeSaver-20260928T190947Z-1-001.zip")
    main([str(z), str(tmp_path / "o.sqlite")])
    assert "work_order_revisions" in capsys.readouterr().out
    assert ingest.metrics(tmp_path / "o.sqlite") == {
        "work_orders": 2, "customers": 2, "data_through": "2026-09-14"}


# ---- checks -------------------------------------------------------------------

LIVE = {"work_orders": 100, "customers": 50, "data_through": "2026-09-14"}


@pytest.mark.parametrize("new, expect", [
    ({"work_orders": 120, "customers": 55, "data_through": "2026-10-01"}, []),
    ({**LIVE}, []),                                                       # same data re-uploaded
    ({**LIVE, "work_orders": 90}, ["work_orders dropped"]),
    ({**LIVE, "customers": 49}, ["customers dropped"]),
    ({**LIVE, "data_through": "2025-12-31"}, ["back in time"]),
    ({"work_orders": 0, "customers": 0, "data_through": ""}, ["no work orders", "work_orders", "customers",
                                                             "back in time"]),
])
def test_problems(new, expect):
    got = ingest.problems(new, LIVE)
    assert len(got) == len(expect) and all(e in g for e, g in zip(expect, got))


def test_problems_without_live_db_only_needs_work_orders():
    assert ingest.problems({"work_orders": 1, "customers": 0, "data_through": "x"}, None) == []


# ---- Drive --------------------------------------------------------------------

ROOT = "root"
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def dfile(fid, name, parent, *, at=T0, mime="application/octet-stream", md5=None):
    ts = at.isoformat().replace("+00:00", "Z")
    return {"id": fid, "name": name, "mimeType": mime, "parents": [parent], "modifiedTime": ts,
            "createdTime": ts, "md5Checksum": md5 or f"md5-{fid}"}


class FakeDrive(Drive):
    """Drive with files in memory: ``tree`` maps folder id -> parent id, ``blobs`` id -> local path."""

    def __init__(self, files, tree, blobs=None):
        super().__init__("token")
        self.files, self.tree, self.blobs = files, tree, blobs or {}

    def search(self, q):
        if "mimeType = 'application/zip'" in q:
            return [f for f in self.files if f["mimeType"] in drive_mod.ZIP_TYPES]
        if "name contains 'ticket'" in q:
            return [f for f in self.files if "ticket" in f["name"].lower()]
        folder = q.split("'")[1]
        return [f for f in self.files if folder in f["parents"]]

    def _get(self, path, **params):
        class R:
            def __init__(s, d): s.d = d
            def json(s): return s.d
        return R({"parents": [self.tree[path.split("/")[1]]] if path.split("/")[1] in self.tree else []})

    def download(self, file_id, dest):
        shutil.copy(self.blobs[file_id], dest)


def folder_upload(folder_id, *, at=T0, names=NEEDED, prefix="f"):
    return [dfile(f"{prefix}{i}", n, folder_id, at=at) for i, n in enumerate(names)]


def test_find_uploads_newest_first_and_only_under_root():
    files = (folder_upload("ls", at=T0)
             + [dfile("z1", "LifeSaver-1-001.zip", ROOT, at=T0 + timedelta(days=1), mime="application/zip"),
                dfile("z2", "elsewhere.zip", "other", at=T0 + timedelta(days=9), mime="application/zip")]
             + folder_upload("stranger", prefix="s", at=T0 + timedelta(days=9)))
    ups = find_uploads(FakeDrive(files, {"ls": ROOT, "other": "x", "stranger": "x"}), ROOT)
    assert [u.label for u in ups] == ["LifeSaver-1-001.zip", "folder ls"]


def test_folder_upload_keeps_newest_duplicate_and_skips_incomplete_folders():
    files = folder_upload("ls") + [dfile("dup", "TICKET.DBF", "ls", at=T0 + timedelta(hours=1))]
    files += folder_upload("half", names=("ticket.dbf", "ticket.fpt"), prefix="h")
    [up] = find_uploads(FakeDrive(files, {"ls": ROOT, "half": ROOT}), ROOT)
    assert up.files["ticket.dbf"]["id"] == "dup"
    assert up.changed_at == T0 + timedelta(hours=1)


def test_upload_key_tracks_content_not_location():
    a = Upload("folder", "a", {f["name"]: f for f in folder_upload("ls")})
    b = Upload("folder", "b", {f["name"]: {**f, "id": "moved"} for f in folder_upload("ls")})
    assert a.key == b.key
    changed = {f["name"]: f for f in folder_upload("ls")}
    changed["ticket.dbf"] = {**changed["ticket.dbf"], "md5Checksum": "new"}
    assert Upload("folder", "c", changed).key != a.key


def test_check_waits_for_upload_to_settle_and_skips_processed(capsys):
    drive = FakeDrive(folder_upload("ls"), {"ls": ROOT})
    assert ingest.check(drive, ROOT, {}, now=T0 + timedelta(minutes=5)) is None
    assert "still be uploading" in capsys.readouterr().out
    up = ingest.check(drive, ROOT, {}, now=T0 + timedelta(hours=1))
    assert up is not None
    seen = {"key": up.key, "outcome": "rejected", "detail": "work_orders dropped"}
    assert ingest.check(drive, ROOT, seen, now=T0 + timedelta(hours=1)) is None
    assert "already processed (rejected" in capsys.readouterr().out
    assert ingest.check(drive, ROOT, seen, force=True, now=T0 + timedelta(hours=1)) is not None


# ---- end to end: main() check + run --------------------------------------------


def _cli(tmp_path, drive, monkeypatch, *extra_run):
    monkeypatch.setenv("DRIVE_TOKEN", "t")
    monkeypatch.setattr(ingest, "Drive", lambda token: drive)
    monkeypatch.setattr(ingest, "_now", lambda: T0 + timedelta(days=30))
    st, cand = tmp_path / "state.json", tmp_path / "cand.json"
    assert ingest.main(["check", "--folder", ROOT, "--state", str(st), "--candidate", str(cand)]) == 0
    rc = ingest.main(["run", "--state", str(st), "--candidate", str(cand),
                      "--work", str(tmp_path / "work"), *extra_run])
    return rc, ingest._load(st)


def test_zip_upload_publishes(tmp_path, monkeypatch):
    z = zip_dir(make_lifesaver(tmp_path / "ls", wo_count=3), tmp_path / "up.zip")
    drive = FakeDrive([dfile("z", "up.zip", ROOT, mime="application/zip")], {}, {"z": z})
    rc, state = _cli(tmp_path, drive, monkeypatch)
    assert rc == 0 and state["outcome"] == "published"
    assert state["metrics"]["work_orders"] == 3
    assert sorted(p.name for p in (tmp_path / "work" / "raw").iterdir()) == sorted(NEEDED)
    assert (tmp_path / "work" / "lifesaver.sqlite").exists()


def test_folder_upload_older_than_live_is_rejected_unless_forced(tmp_path, monkeypatch):
    live = tmp_path / "live.sqlite"
    extract(make_lifesaver(tmp_path / "live_ls", wo_count=5), live)
    src = make_lifesaver(tmp_path / "ls", wo_count=2, last_date=date(2025, 1, 1))
    files = [dfile(f"f{i}", p.name, "ls") for i, p in enumerate(sorted(src.iterdir()))]
    drive = FakeDrive(files, {"ls": ROOT}, {f["id"]: src / f["name"] for f in files})

    rc, state = _cli(tmp_path, drive, monkeypatch, "--current", str(live))
    assert rc == 1 and state["outcome"] == "rejected"
    assert "work_orders dropped 5 -> 2" in state["detail"] and "back in time" in state["detail"]
    assert not (tmp_path / "work" / "lifesaver.sqlite").exists()

    (tmp_path / "state.json").unlink()
    rc, state = _cli(tmp_path, drive, monkeypatch, "--current", str(live), "--force")
    assert rc == 0 and "forced past" in state["detail"]


def test_corrupt_upload_is_rejected_not_crashed(tmp_path, monkeypatch):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    drive = FakeDrive([dfile("z", "bad.zip", ROOT, mime="application/zip")], {}, {"z": bad})
    rc, state = _cli(tmp_path, drive, monkeypatch)
    assert rc == 1 and state["outcome"] == "rejected" and "BadZipFile" in state["detail"]
