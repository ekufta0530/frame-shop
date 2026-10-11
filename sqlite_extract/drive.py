"""Find and download the owner's latest LifeSaver upload from a shared Google Drive folder.

The owner drops data into one Drive folder shared (Viewer) with the CI service
account. Either shape works, anywhere under that folder:

- a **zip** of the LifeSaver folder (what Drive's own "Download" produces), or
- the **LifeSaver folder itself**, uploaded or synced as plain files.

The newest one wins. Only the files ``extract.NEEDED`` lists are downloaded from
a folder upload; a zip is downloaded whole (Drive can't read inside it).

Auth is a bearer token with the ``drive.readonly`` scope: CI gets one from
``google-github-actions/auth`` (``token_format: access_token``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests

from sqlite_extract.extract import NEEDED, SourceError, find_needed

API = "https://www.googleapis.com/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
ZIP_TYPES = ("application/zip", "application/x-zip-compressed", "application/x-zip")
FIELDS = "id,name,mimeType,parents,modifiedTime,createdTime,md5Checksum,size"


@dataclass
class Upload:
    """One candidate data drop: a zip, or the NEEDED files of a LifeSaver folder."""

    kind: str                        # "zip" | "folder"
    label: str                       # human-readable, for logs/state
    files: dict[str, dict]           # local name -> Drive file resource
    changed_at: datetime = field(init=False)
    key: str = field(init=False)     # content identity: changes iff the bytes do

    def __post_init__(self):
        self.changed_at = max(_changed(f) for f in self.files.values())
        h = hashlib.sha256()
        for name, f in sorted(self.files.items()):
            h.update(f"{name}:{f.get('md5Checksum') or f['id'] + f['modifiedTime']}\n".encode())
        self.key = h.hexdigest()[:16]


def _changed(f: dict) -> datetime:
    # a browser upload can carry the file's old mtime; createdTime is when it reached Drive
    return max(datetime.fromisoformat(f[k].replace("Z", "+00:00")) for k in ("modifiedTime", "createdTime"))


class Drive:
    def __init__(self, token: str, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"
        self._parents: dict[str, list[str]] = {}

    def _get(self, path: str, **params) -> requests.Response:
        r = self.s.get(f"{API}/{path}", params={"supportsAllDrives": "true", **params}, timeout=60)
        r.raise_for_status()
        return r

    def search(self, q: str) -> list[dict]:
        out, token = [], None
        while True:
            page = self._get("files", q=f"({q}) and trashed = false", pageSize=1000,
                             fields=f"nextPageToken,files({FIELDS})", corpora="allDrives",
                             includeItemsFromAllDrives="true",
                             **({"pageToken": token} if token else {})).json()
            out += page.get("files", [])
            if not (token := page.get("nextPageToken")):
                return out

    def is_under(self, f: dict, root_id: str) -> bool:
        """True if ``f`` sits anywhere below folder ``root_id`` (walks up the parent chain)."""
        todo, seen = list(f.get("parents", [])), set()
        while todo:
            pid = todo.pop()
            if pid == root_id:
                return True
            if pid in seen:
                continue
            seen.add(pid)
            if pid not in self._parents:
                try:
                    self._parents[pid] = self._get(f"files/{pid}", fields="parents").json().get("parents", [])
                except requests.HTTPError:  # above what's shared with us
                    self._parents[pid] = []
            todo += self._parents[pid]
        return False

    def download(self, file_id: str, dest: Path) -> None:
        with self.s.get(f"{API}/files/{file_id}", params={"alt": "media", "supportsAllDrives": "true"},
                        stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(dest, "wb") as out:
                for chunk in r.iter_content(1 << 20):
                    out.write(chunk)


def find_uploads(drive: Drive, root_id: str) -> list[Upload]:
    """Every usable data drop under ``root_id``, newest first."""
    uploads = []
    zips = drive.search(" or ".join(f"mimeType = '{t}'" for t in ZIP_TYPES))
    uploads += [Upload("zip", z["name"], {"upload.zip": z}) for z in zips if drive.is_under(z, root_id)]

    tickets = [t for t in drive.search("name contains 'ticket'") if t["name"].lower() == "ticket.dbf"]
    for folder_id in {p for t in tickets for p in t.get("parents", [])}:
        folder = {"id": folder_id, "parents": [folder_id]}
        if not drive.is_under(folder, root_id):
            continue
        listing = drive.search(f"'{folder_id}' in parents and mimeType != '{FOLDER}'")
        # Drive allows duplicate names (re-uploads): keep the newest of each
        newest: dict[str, dict] = {}
        for f in sorted(listing, key=_changed):
            if f["name"].lower() in NEEDED:
                newest[f["name"].lower()] = f
        try:
            find_needed(list(newest))
        except SourceError as e:
            print(f"skipping Drive folder {folder_id}: {e}")
            continue
        uploads.append(Upload("folder", f"folder {folder_id}", newest))
    return sorted(uploads, key=lambda u: u.changed_at, reverse=True)


def fetch(drive: Drive, upload: Upload, dest: Path) -> Path:
    """Download ``upload`` into ``dest``; returns the zip path or the folder of NEEDED files."""
    dest.mkdir(parents=True, exist_ok=True)
    for name, f in upload.files.items():
        drive.download(f["id"], dest / name)
    return dest / "upload.zip" if upload.kind == "zip" else dest
