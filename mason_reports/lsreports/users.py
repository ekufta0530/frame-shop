"""User accounts for the web app: a JSON file of {username: {name, hash}} kept next to the data.

USERS_URI is a local path or gs://bucket/users.json. Passwords are stored as scrypt hashes.
Manage accounts with manage_users.py; running services re-read the file every minute.
"""
import base64
import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path

USERS_URI = os.environ.get('USERS_URI', str(Path(__file__).resolve().parent.parent / 'users.json'))
REFRESH_SECONDS = 60
MIN_PASSWORD_LENGTH = 10

_N, _R, _P = 2 ** 14, 8, 1
_lock = threading.Lock()
_cache = {'users': None, 'checked': 0.0}


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f'scrypt${_N}${_R}${_P}$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()


# Verified against when the username doesn't exist, so response time doesn't reveal valid usernames.
_DUMMY_HASH = hash_password(base64.b64encode(os.urandom(12)).decode())


def verify_password(password, stored):
    try:
        _, n, r, p, salt, digest = stored.split('$')
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(digest)))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except Exception:
        return False


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


def authenticate(username, password):
    """Returns the user record, or None. Always does one scrypt, whether or not the user exists."""
    user = all_users().get((username or '').strip().lower())
    ok = verify_password(password or '', user['hash'] if user else _DUMMY_HASH)
    return user if user and ok else None
