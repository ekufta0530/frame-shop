"""Drive folder -> validated ``lifesaver.sqlite``: the Mason store's hands-off data refresh.

    python -m sqlite_extract.ingest check --folder ID --state state.json --candidate cand.json
    python -m sqlite_extract.ingest run   --candidate cand.json --state state.json \\
                                          --current live.sqlite --work DIR

``check`` looks for the newest upload in the Drive folder and writes
``--candidate`` only if it is new (its content key differs from the last one
processed) and settled (untouched for ``SETTLE``, so a half-finished upload is
left alone). ``run`` downloads it, keeps just the needed .dbf files
(``DIR/raw/``), rebuilds ``DIR/lifesaver.sqlite`` and refuses to hand it over if
it looks older or smaller than ``--current`` -- an old backup or a partial copy
must never replace good data. Either way ``--state`` records the outcome, so a
rejected upload fails once rather than on every scheduled run.

``sqlite_extract/ingest.sh`` wraps both with the GCS reads/writes.
The Drive token comes from ``$DRIVE_TOKEN`` (scope ``drive.readonly``).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlite_extract.drive import Drive, Upload, fetch, find_uploads
from sqlite_extract.extract import extract, unpack_needed

SETTLE = timedelta(minutes=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() and path.stat().st_size else {}


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def check(drive: Drive, folder_id: str, state: dict, *, force: bool = False,
          now: datetime | None = None) -> Upload | None:
    """The upload to process next, or None (nothing there, nothing new, or still uploading)."""
    uploads = find_uploads(drive, folder_id)
    if not uploads:
        print("no LifeSaver upload (zip or folder with ticket.dbf) in the Drive folder")
        return None
    up = uploads[0]
    print(f"newest upload: {up.label} ({up.kind}), changed {up.changed_at:%Y-%m-%d %H:%M} UTC, key {up.key}")
    if (now or _now()) - up.changed_at < SETTLE:
        print(f"changed under {SETTLE} ago -- may still be uploading; next run will pick it up")
        return None
    if up.key == state.get("key") and not force:
        print(f"already processed ({state.get('outcome')}: {state.get('detail')})")
        return None
    return up


def metrics(db: Path) -> dict:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        wos, through = c.execute("SELECT COUNT(*), MAX(order_date) FROM wo_facts").fetchone()
        customers = c.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    finally:
        c.close()
    return {"work_orders": wos, "customers": customers, "data_through": (through or "")[:10]}


def problems(new: dict, current: dict | None) -> list[str]:
    """Reasons ``new`` must not replace ``current`` (the live data)."""
    out = [] if new["work_orders"] else ["no work orders"]
    if current:
        for k in ("work_orders", "customers"):
            if new[k] < current[k]:
                out.append(f"{k} dropped {current[k]:,} -> {new[k]:,}")
        if new["data_through"] < current["data_through"]:
            out.append(f"data goes back in time: through {new['data_through']}, live is {current['data_through']}")
    return out


def run(drive: Drive, cand: dict, current: Path | None, work: Path, *, force: bool = False) -> dict:
    """Build ``work/lifesaver.sqlite`` from candidate ``cand``. Returns the new state record."""
    state = {"key": cand["key"], "label": cand["label"], "changed_at": cand["changed_at"],
             "at": _now().isoformat(timespec="seconds")}
    raw, out = work / "raw", work / "lifesaver.sqlite"
    shutil.rmtree(work, ignore_errors=True)
    up = Upload(cand["kind"], cand["label"], cand["files"])
    got = fetch(drive, up, work / "download")  # Drive/network errors raise: retried next run
    try:
        if up.kind == "zip":
            unpack_needed(got, raw)
        else:
            got.rename(raw)
        counts = extract(raw, work / "new.sqlite")
    except Exception as e:  # bad upload (not a zip, missing or corrupt tables): same bytes fail every time
        return {**state, "outcome": "rejected", "detail": f"{type(e).__name__}: {e}"}
    new = metrics(work / "new.sqlite")
    cur = metrics(current) if current and current.exists() else None
    state["metrics"] = {**new, "rows": counts}
    if (bad := problems(new, cur)) and not force:
        return {**state, "outcome": "rejected", "detail": "; ".join(bad) + " (re-run with force to publish anyway)"}
    (work / "new.sqlite").rename(out)
    shutil.rmtree(work / "download", ignore_errors=True)
    return {**state, "outcome": "published",
            "detail": f"{new['work_orders']:,} work orders, data through {new['data_through']}"
                      + (f" (forced past: {'; '.join(bad)})" if bad else "")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--folder", required=True, help="Drive folder ID the owner uploads into")
    r = sub.add_parser("run")
    r.add_argument("--current", type=Path, help="live lifesaver.sqlite to compare against (optional)")
    r.add_argument("--work", type=Path, required=True)
    for p in (c, r):
        p.add_argument("--state", type=Path, required=True)
        p.add_argument("--candidate", type=Path, required=True)
        p.add_argument("--force", action="store_true", help="reprocess / publish despite checks")
    args = ap.parse_args(argv)

    drive = Drive(os.environ["DRIVE_TOKEN"])
    state = _load(args.state)
    if args.cmd == "check":
        args.candidate.unlink(missing_ok=True)
        if up := check(drive, args.folder, state, force=args.force):
            _save(args.candidate, {"kind": up.kind, "label": up.label, "key": up.key,
                                   "changed_at": up.changed_at.isoformat(), "files": up.files})
        return 0

    new_state = run(drive, _load(args.candidate), args.current, args.work, force=args.force)
    _save(args.state, new_state)
    print(f"{new_state['outcome']}: {new_state['detail']}")
    return 0 if new_state["outcome"] == "published" else 1


if __name__ == "__main__":
    sys.exit(main())
