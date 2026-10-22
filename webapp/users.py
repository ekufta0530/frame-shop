"""Who may sign in, and what they may see: a JSON file of {email: {name, role, stores}} kept next to the data.

    {"boss@gmail.com":  {"name": "Boss",  "role": "admin"},
     "owner@gmail.com": {"name": "Owner", "role": "owner", "stores": ["wheatons"]}}

An admin sees every store, the all-stores overview and the pooled reports. An owner sees only the
stores listed -- never the overview or "all", which carry the other stores' numbers. Anything else (no
role, an unknown one, an owner with no stores) is denied: that account can't sign in.

USERS_URI is a local path or gs://bucket/users.json. There are no passwords -- people prove who they are
with Google, and this file is the allowlist of Google accounts that may see the site.
Manage it with manage_users.py; running services re-read the file every minute.
"""
import json
import os
import threading
import time
from pathlib import Path

USERS_URI = os.environ.get('USERS_URI', str(Path(__file__).resolve().parent.parent / 'users.json'))
REFRESH_SECONDS = 60
ADMIN, OWNER = 'admin', 'owner'

_lock = threading.Lock()
_cache = {'users': None, 'checked': 0.0}


def normalize(email):
    return (email or '').strip().lower()


def _read():
    if USERS_URI.startswith('gs://'):
        from google.cloud import storage

        bucket, _, name = USERS_URI[len('gs://'):].partition('/')
        blob = storage.Client().bucket(bucket).get_blob(name)
        return json.loads(blob.download_as_text()) if blob else {}
    p = Path(USERS_URI)
    return json.loads(p.read_text()) if p.exists() else {}


def all_users():
    with _lock:
        if _cache['users'] is None or time.time() - _cache['checked'] > REFRESH_SECONDS:
            try:
                _cache['users'] = _read()
                _cache['checked'] = time.time()
            except Exception:
                if _cache['users'] is None:
                    raise  # nothing cached to fall back on
        return _cache['users']


def grants_access(rec):
    """Whether a users.json record lets its account in at all (default deny)."""
    if not isinstance(rec, dict):
        return False
    return rec.get('role') == ADMIN or (rec.get('role') == OWNER and bool(rec.get('stores')))


def lookup(email):
    """The allowlist record for a Google account's email, or None if it's missing or grants nothing."""
    rec = all_users().get(normalize(email))
    return rec if grants_access(rec) else None


def is_admin(user):
    return user.get('role') == ADMIN


def can_see(user, store_id):
    """Whether a signed-in user (request.state.user) may see a store's data; "all" is admins only."""
    return is_admin(user) or store_id in user.get('stores', ())
