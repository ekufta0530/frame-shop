"""Who may sign in: a JSON file of {email: {name}} kept next to the data.

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


def lookup(email):
    """The allowlist record for a Google account's email, or None."""
    return all_users().get(normalize(email))
