"""Keeps a local, current copy of every store's data and the pooled all-stores warehouse.

Per store (``stores.toml``):
- **cloud**: ``data`` is the store's ``warehouse.db`` (written by refresh.yml). Used as is.
- **on-prem**: ``data`` is the published ``lifesaver.sqlite``. Its KPI warehouse is derived
  here with ``sqlite_extract.importer`` (under a second), and the report builder queries the
  sqlite itself.

GCS objects are re-checked at most every REFRESH_SECONDS and downloaded only when their
generation changes; anything derived from them is rebuilt only then. A store whose data can't
be read is reported as unavailable and left out of the all-stores figures -- one broken store
never takes the site down.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from core.aggregate import build_combined
from core.store import DATA_THROUGH
from core.stores import Store, load_stores
from sqlite_extract.importer import import_store

log = logging.getLogger(__name__)

REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "600"))
CACHE_DIR = Path(os.environ.get("CACHE_DIR") or Path(tempfile.gettempdir()) / "frameshop-cache")


@dataclass
class StoreData:
    store: Store
    generation: str | None = None  # changes whenever the source file does
    source_path: Path | None = None  # the downloaded file (warehouse.db or lifesaver.sqlite)
    warehouse_path: Path | None = None  # the core KPI warehouse
    data_through: date | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.warehouse_path is not None


@dataclass
class Snapshot:
    stores: list[StoreData]
    combined_path: Path | None = None
    combined_through: date | None = None
    version: str = ""  # changes whenever any store's data does
    loaded_at: float = field(default_factory=time.time)

    def get(self, store_id: str) -> StoreData | None:
        return next((s for s in self.stores if s.store.id == store_id), None)


def _gs_split(uri: str) -> tuple[str, str]:
    bucket, _, name = uri[len("gs://"):].partition("/")
    return bucket, name


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


class DataManager:
    def __init__(self, stores: list[Store] | None = None, cache_dir: Path = CACHE_DIR):
        self._stores = stores
        self._cache = cache_dir
        self._lock = threading.Lock()
        self._snap: Snapshot | None = None
        self._checked = 0.0
        self._gcs = None
        self.background = False  # the server turns this on after warming (see server.lifespan)
        self._refreshing = False
        self._flag = threading.Lock()
        # files replaced by a refresh are deleted one refresh later, so a request still
        # holding the previous snapshot never finds its file gone
        self._retired: list[Path] = []
        self._retiring: list[Path] = []

    # --- public -------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        """The current data. With ``background`` on, a stale snapshot is served as is while a
        worker thread refreshes it, so no request waits on a GCS check or a database rebuild.
        Reads never take ``_lock``; it only serialises refreshes."""
        snap = self._snap
        if snap is not None and not self._due():
            return snap
        if snap is not None and self.background:
            with self._flag:
                start = not self._refreshing
                self._refreshing = True
            if start:
                threading.Thread(target=self._refresh_bg, daemon=True).start()
            return snap
        with self._lock:
            if self._snap is None or self._due():
                self._do_refresh()
            return self._snap

    def _due(self) -> bool:
        return time.time() - self._checked > REFRESH_SECONDS

    def _do_refresh(self) -> None:  # call with the lock held
        self._checked = time.time()
        try:
            self._snap = self._refresh(self._snap)
        except Exception:  # keep serving what we have
            log.exception("data refresh failed")
            if self._snap is None:
                raise

    def _refresh_bg(self) -> None:
        try:
            with self._lock:
                if self._due():
                    self._do_refresh()
        finally:
            with self._flag:
                self._refreshing = False

    # --- refresh ------------------------------------------------------------

    def _retire(self, p: Path | None) -> None:
        if p is not None and p.is_relative_to(self._cache):
            self._retiring.append(p)

    def _refresh(self, prev: Snapshot | None) -> Snapshot:
        self._cache.mkdir(parents=True, exist_ok=True)
        for p in self._retired:
            _unlink_db(p)
        self._retired, self._retiring = self._retiring, []
        stores = self._stores if self._stores is not None else load_stores()
        old = {s.store.id: s for s in prev.stores} if prev else {}
        out = []
        for store in stores:
            before = old.get(store.id)
            try:
                out.append(self._load_store(store, before))
            except Exception as e:
                log.exception("store %s: data unavailable", store.id)
                # a transient read error keeps the last good copy
                if before is not None and before.ok and before.store == store:
                    out.append(before)
                else:
                    out.append(StoreData(store=store, error=f"{type(e).__name__}: {e}"))

        ok = [s for s in out if s.ok]
        version = hashlib.sha1(
            "|".join(f"{s.store.id}:{s.generation}" for s in ok).encode()
        ).hexdigest()[:12]
        snap = Snapshot(stores=out, version=version)
        if ok:
            through = min(s.data_through for s in ok)
            dest = self._cache / f"combined-{version}.db"
            if prev and prev.version == version and prev.combined_path and prev.combined_path.exists():
                dest = prev.combined_path
            else:
                build_combined([(s.store.id, s.warehouse_path) for s in ok], dest, through=through)
                if prev and prev.combined_path != dest:
                    self._retire(prev.combined_path)
            snap.combined_path, snap.combined_through = dest, through
        return snap

    def _load_store(self, store: Store, before: StoreData | None) -> StoreData:
        path, generation = self._fetch(store.data, before)
        if (before is not None and before.ok and before.generation == generation
                and before.store == store):
            return before
        sd = StoreData(store=store, generation=generation, source_path=path)
        if store.on_prem:
            wh = self._cache / f"{_safe(store.id)}-{_safe(generation)}.warehouse.db"
            if not wh.exists():
                import_store(path, wh)
            sd.warehouse_path = wh
        else:
            sd.warehouse_path = path
        sd.data_through = read_data_through(sd.warehouse_path)
        if before is not None and before.warehouse_path != sd.warehouse_path:
            self._retire(before.warehouse_path)
        return sd

    def _fetch(self, uri: str, before: StoreData | None) -> tuple[Path, str]:
        """Local path + generation for ``uri``, downloading a gs:// object when it changed."""
        if not uri.startswith("gs://"):
            p = Path(uri)
            if not p.exists():
                raise FileNotFoundError(uri)
            return p, str(int(p.stat().st_mtime_ns))
        if self._gcs is None:
            from google.cloud import storage  # only needed in the cloud
            self._gcs = storage.Client()
        bucket, name = _gs_split(uri)
        blob = self._gcs.bucket(bucket).get_blob(name)
        if blob is None:
            raise FileNotFoundError(uri)
        generation = str(blob.generation)
        if before is not None and before.generation == generation and before.source_path \
                and before.source_path.exists():
            return before.source_path, generation
        dest = self._cache / f"{_safe(bucket)}-{_safe(name)}-{generation}"
        if not dest.exists():
            tmp = dest.with_suffix(".part")
            blob.download_to_filename(tmp)
            tmp.replace(dest)
        if before is not None and before.source_path != dest:
            self._retire(before.source_path)
        return dest, generation


def read_data_through(warehouse: Path) -> date:
    """The store's "today": its data_through checkpoint, else the best evidence there is
    (the last report pull for an older cloud warehouse, else the newest visit)."""
    c = sqlite3.connect(f"file:{warehouse}?mode=ro", uri=True)
    try:
        r = c.execute("SELECT value FROM sync_checkpoints WHERE name = ?", (DATA_THROUGH,)).fetchone()
        if r:
            return date.fromisoformat(r[0][:10])
        tables = {t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "raw_pulls" in tables:
            r = c.execute("SELECT MAX(pulled_at) FROM raw_pulls").fetchone()
            if r and r[0]:
                return date.fromisoformat(r[0][:10])
        r = c.execute("SELECT MAX(visit_date) FROM visits").fetchone()
        return date.fromisoformat(r[0]) if r and r[0] else date.today()
    finally:
        c.close()


def _unlink_db(p: Path) -> None:
    for x in (p, Path(f"{p}-wal"), Path(f"{p}-shm")):
        x.unlink(missing_ok=True)


manager = DataManager()
