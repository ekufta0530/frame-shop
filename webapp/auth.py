"""Username/password login with signed session cookies.

The cookie holds {user, expiry, password fingerprint} signed with SESSION_SECRET, so no server-side
session store is needed. Changing a user's password or removing them ends their sessions.
Accounts live in users.json (see users.py / manage_users.py). Everyone signed in sees every store.

Google sign-in is the planned replacement: it would add a /api/login/google flow that sets the
same cookie, so nothing past this module has to change.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import users

COOKIE = 'lsr_session'
SESSION_DAYS = 14
# Set AUTH_DISABLED=1 only for local development against a copy of the data.
AUTH_DISABLED = os.environ.get('AUTH_DISABLED') == '1'
SECRET = os.environ.get('SESSION_SECRET', '').encode()
if not SECRET:
    if not AUTH_DISABLED and os.environ.get('K_SERVICE'):  # K_SERVICE is set on Cloud Run
        raise RuntimeError('SESSION_SECRET must be set')
    SECRET = secrets.token_bytes(32)  # local dev: sessions reset on restart

PUBLIC_PATHS = {'/login', '/api/login', '/api/logout', '/health', '/favicon.svg'}
PUBLIC_PREFIXES = ('/assets/',)  # the built frontend's JS/CSS -- no data in it

# Lockout within a 15-minute window: 5 failures for one username, or 20 from one IP (looser, because
# everyone at the shop shares an IP and one person's typos shouldn't lock out the rest).
WINDOW_SECONDS = 15 * 60
LIMITS = {'u': 5, 'ip': 20}
_failures = defaultdict(deque)
_fail_lock = threading.Lock()


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def _fingerprint(user):
    return hashlib.sha256(user['hash'].encode()).hexdigest()[:16]


def make_token(username, user):
    payload = _b64(json.dumps({'u': username, 'exp': int(time.time()) + SESSION_DAYS * 86400,
                               'pw': _fingerprint(user)}).encode())
    sig = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    return f'{payload}.{sig}'


def read_token(token):
    try:
        payload, sig = token.split('.')
        want = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return None
        data = json.loads(_unb64(payload))
        user = users.all_users().get(data['u'])
        if not user or data['exp'] < time.time() or data['pw'] != _fingerprint(user):
            return None
        return {'username': data['u'], 'name': user.get('name') or data['u']}
    except Exception:
        return None


def _client_ip(request):
    fwd = request.headers.get('x-forwarded-for')
    return fwd.split(',')[0].strip() if fwd else (request.client.host if request.client else '?')


def _locked(keys):
    now = time.time()
    with _fail_lock:
        for k in keys:
            q = _failures[k]
            while q and now - q[0] > WINDOW_SECONDS:
                q.popleft()
            if len(q) >= LIMITS[k.split(':', 1)[0]]:
                return True
    return False


def _record_failure(keys):
    with _fail_lock:
        for k in keys:
            _failures[k].append(time.time())


async def auth_middleware(request: Request, call_next):
    if AUTH_DISABLED:
        request.state.user = {'username': 'dev', 'name': 'Local dev'}
        return await call_next(request)
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return await call_next(request)
    user = read_token(request.cookies.get(COOKIE, ''))
    if not user:
        if path.startswith('/api/'):
            return JSONResponse({'detail': 'Not signed in'}, status_code=401)
        return RedirectResponse('/login', status_code=303)
    request.state.user = user
    return await call_next(request)


router = APIRouter()


class LoginIn(BaseModel):
    username: str
    password: str


@router.post('/api/login')
def login(body: LoginIn, request: Request, response: Response):
    username = body.username.strip().lower()
    keys = [f'u:{username}', f'ip:{_client_ip(request)}']
    if _locked(keys):
        raise HTTPException(429, 'Too many failed attempts. Try again in 15 minutes.')
    user = users.authenticate(username, body.password)
    if not user:
        _record_failure(keys)
        raise HTTPException(401, 'Wrong username or password.')
    response.set_cookie(COOKIE, make_token(username, user), max_age=SESSION_DAYS * 86400, httponly=True,
                        secure=request.url.scheme == 'https' or bool(os.environ.get('K_SERVICE')),
                        samesite='lax', path='/')
    return {'username': username, 'name': user.get('name') or username}


@router.post('/api/logout')
def logout(response: Response):
    response.delete_cookie(COOKIE, path='/')
    return {'ok': True}


@router.get('/api/me')
def me(request: Request):
    return request.state.user
