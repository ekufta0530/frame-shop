"""Sign in with Google (OpenID Connect, authorization-code flow with PKCE) and signed session cookies.

There is no password form. /api/login/google sends the browser to Google; Google sends it back to
/auth/callback with a one-time code, which is exchanged (server to server, with the client secret) for
an ID token. The signed-in email must be on the allowlist in users.json (see users.py / manage_users.py);
everyone on it sees every store.

The session cookie holds {email, expiry} signed with SESSION_SECRET, so no server-side session store is
needed. Removing someone from users.json ends their session within a minute.

Env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, SESSION_SECRET, and optionally OAUTH_REDIRECT_URI (else
derived from the request, which needs uvicorn --proxy-headers behind Cloud Run).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import users

COOKIE = 'lsr_session'
STATE_COOKIE = 'lsr_oauth'  # short-lived: carries state/nonce/PKCE verifier across the trip to Google
SESSION_DAYS = 14
STATE_SECONDS = 10 * 60
# Set AUTH_DISABLED=1 only for local development against a copy of the data.
AUTH_DISABLED = os.environ.get('AUTH_DISABLED') == '1'
SECRET = os.environ.get('SESSION_SECRET', '').encode()
if not SECRET:
    if not AUTH_DISABLED and os.environ.get('K_SERVICE'):  # K_SERVICE is set on Cloud Run
        raise RuntimeError('SESSION_SECRET must be set')
    SECRET = secrets.token_bytes(32)  # local dev: sessions reset on restart

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET) and not AUTH_DISABLED and os.environ.get('K_SERVICE'):
    raise RuntimeError('GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set')

AUTHORIZE_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
ISSUERS = ('https://accounts.google.com', 'accounts.google.com')

PUBLIC_PATHS = {'/login', '/api/login/google', '/auth/callback', '/api/logout', '/health', '/favicon.svg', '/privacy'}
PUBLIC_PREFIXES = ('/assets/',)  # the built frontend's JS/CSS -- no data in it


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def _sign(data, max_age):
    payload = _b64(json.dumps({**data, 'exp': int(time.time()) + max_age}).encode())
    sig = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    return f'{payload}.{sig}'


def _unsign(token):
    """The signed dict, or None if it's malformed, tampered with or expired."""
    try:
        payload, sig = token.split('.')
        want = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return None
        data = json.loads(_unb64(payload))
        return data if data['exp'] >= time.time() else None
    except Exception:
        return None


def make_token(email):
    return _sign({'u': email}, SESSION_DAYS * 86400)


def read_token(token):
    data = _unsign(token)
    user = users.lookup(data['u']) if data else None
    if not user:
        return None
    return {'username': data['u'], 'name': user.get('name') or data['u']}


def _secure(request):
    return request.url.scheme == 'https' or bool(os.environ.get('K_SERVICE'))


def _redirect_uri(request):
    return os.environ.get('OAUTH_REDIRECT_URI') or str(request.url_for('google_callback'))


def _safe_next(path):
    """Only same-site paths: this value ends up in a redirect."""
    if path and path.startswith('/') and not path.startswith(('//', '/\\', '/login', '/auth/', '/api/')):
        return path
    return '/'


def _login_error(code):
    r = RedirectResponse(f'/login?error={code}', status_code=303)
    r.delete_cookie(STATE_COOKIE, path='/')
    return r


def _exchange_code(code, verifier, redirect_uri):
    body = urllib.parse.urlencode({
        'code': code, 'client_id': GOOGLE_CLIENT_ID, 'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri': redirect_uri, 'grant_type': 'authorization_code', 'code_verifier': verifier,
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=10) as resp:
        return json.loads(resp.read())


def _id_token_claims(id_token):
    # The token came straight from Google's token endpoint over TLS, in exchange for our client secret,
    # so (per OIDC Core 3.1.3.7) its signature need not be re-checked; its claims still are.
    return json.loads(_unb64(id_token.split('.')[1]))


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


@router.get('/api/login/google')
def google_login(request: Request, next: str = '/'):
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        return _login_error('not_configured')
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    url = AUTHORIZE_URL + '?' + urllib.parse.urlencode({
        'client_id': GOOGLE_CLIENT_ID, 'redirect_uri': _redirect_uri(request), 'response_type': 'code',
        'scope': 'openid email profile', 'state': state, 'nonce': nonce,
        'code_challenge': challenge, 'code_challenge_method': 'S256', 'prompt': 'select_account',
    })
    r = RedirectResponse(url, status_code=303)
    r.set_cookie(STATE_COOKIE, _sign({'s': state, 'n': nonce, 'v': verifier, 'next': _safe_next(next)}, STATE_SECONDS),
                 max_age=STATE_SECONDS, httponly=True, secure=_secure(request), samesite='lax', path='/')
    return r


@router.get('/auth/callback')
def google_callback(request: Request, code: str = '', state: str = '', error: str = ''):
    saved = _unsign(request.cookies.get(STATE_COOKIE, ''))
    if error or not code:
        return _login_error('cancelled')
    if not saved or not hmac.compare_digest(saved['s'], state):
        return _login_error('expired')  # stale tab, or a forged callback
    try:
        claims = _id_token_claims(_exchange_code(code, saved['v'], _redirect_uri(request))['id_token'])
    except (urllib.error.URLError, KeyError, ValueError, IndexError):
        return _login_error('google_failed')
    if (claims.get('aud') != GOOGLE_CLIENT_ID or claims.get('iss') not in ISSUERS
            or claims.get('nonce') != saved['n'] or claims.get('exp', 0) < time.time()
            or not claims.get('email_verified')):
        return _login_error('google_failed')
    email = users.normalize(claims.get('email'))
    if not users.lookup(email):
        return _login_error('not_allowed')
    r = RedirectResponse(saved['next'], status_code=303)
    r.set_cookie(COOKIE, make_token(email), max_age=SESSION_DAYS * 86400, httponly=True,
                 secure=_secure(request), samesite='lax', path='/')
    r.delete_cookie(STATE_COOKIE, path='/')
    return r


@router.post('/api/logout')
def logout():
    r = JSONResponse({'ok': True})
    r.delete_cookie(COOKIE, path='/')
    return r


@router.get('/api/me')
def me(request: Request):
    return request.state.user
