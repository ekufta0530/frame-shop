"""Sign in with Google: the redirect, the callback's checks, the email allowlist and the session cookie."""
import base64
import json
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from webapp import auth, server, users

CLIENT_ID = "client-123.apps.googleusercontent.com"


def _jwt(claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"x.{body}.y"


@pytest.fixture
def client(tmp_path, monkeypatch):
    allow = tmp_path / "users.json"
    allow.write_text(json.dumps({"owner@gmail.com": {"name": "Owner"}}))
    monkeypatch.setattr(users, "USERS_URI", str(allow))
    monkeypatch.setitem(users._cache, "users", None)
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_SECRET", "shh")
    return TestClient(server.app, follow_redirects=False)


def _start(client, next="/stores/mason"):
    r = client.get("/api/login/google", params={"next": next})
    assert r.status_code == 303
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
    return r, q


def _callback(client, monkeypatch, q, **claims):
    """Finish the flow as Google would: the ID token echoes the nonce we sent."""
    token = {"aud": CLIENT_ID, "iss": "https://accounts.google.com", "exp": time.time() + 600,
             "email": "owner@gmail.com", "email_verified": True, "nonce": q["nonce"][0], **claims}
    monkeypatch.setattr(auth, "_exchange_code", lambda code, verifier, uri: {"id_token": _jwt(token)})
    return client.get("/auth/callback", params={"code": "abc", "state": q["state"][0]})


def test_login_redirects_to_google_with_state_nonce_and_pkce(client):
    r, q = _start(client)
    assert r.headers["location"].startswith("https://accounts.google.com/")
    assert q["client_id"] == [CLIENT_ID] and q["scope"] == ["openid email profile"]
    assert q["code_challenge_method"] == ["S256"] and q["state"] and q["nonce"] and q["code_challenge"]
    assert "httponly" in r.headers["set-cookie"].lower()


def test_an_allowed_account_gets_a_session_and_lands_where_it_was_headed(client, monkeypatch):
    _, q = _start(client)
    r = _callback(client, monkeypatch, q)
    assert r.status_code == 303 and r.headers["location"] == "/stores/mason"
    assert auth.COOKIE in client.cookies
    assert client.get("/api/me").json()["username"] == "owner@gmail.com"


def test_an_account_not_on_the_allowlist_is_turned_away(client, monkeypatch):
    _, q = _start(client)
    r = _callback(client, monkeypatch, q, email="stranger@gmail.com")
    assert r.headers["location"] == "/login?error=not_allowed"
    assert auth.COOKIE not in client.cookies


@pytest.mark.parametrize("bad", [{"aud": "someone-else"}, {"iss": "https://evil.example"},
                                 {"nonce": "replayed"}, {"email_verified": False},
                                 {"exp": time.time() - 5}])
def test_a_bad_id_token_is_rejected(client, monkeypatch, bad):
    _, q = _start(client)
    r = _callback(client, monkeypatch, q, **bad)
    assert r.headers["location"] == "/login?error=google_failed"
    assert auth.COOKIE not in client.cookies


def test_a_callback_without_our_state_cookie_or_with_a_wrong_state_is_rejected(client, monkeypatch):
    assert client.get("/auth/callback", params={"code": "abc", "state": "forged"}).headers["location"] == "/login?error=expired"
    _, q = _start(client)
    r = client.get("/auth/callback", params={"code": "abc", "state": "not-the-one"})
    assert r.headers["location"] == "/login?error=expired"


def test_cancelling_at_google_returns_to_the_login_page(client):
    assert client.get("/auth/callback", params={"error": "access_denied"}).headers["location"] == "/login?error=cancelled"


@pytest.mark.parametrize("next", ["//evil.example", "https://evil.example", "/\\evil.example", "/api/stores"])
def test_next_cannot_redirect_off_site(client, monkeypatch, next):
    _, q = _start(client, next=next)
    assert _callback(client, monkeypatch, q).headers["location"] == "/"


def test_removing_someone_from_the_allowlist_ends_their_session(client, monkeypatch, tmp_path):
    _, q = _start(client)
    _callback(client, monkeypatch, q)
    assert client.get("/api/me").status_code == 200
    (tmp_path / "users.json").write_text("{}")
    monkeypatch.setitem(users._cache, "users", None)  # i.e. the minute has passed
    assert client.get("/api/me").status_code == 401


def test_a_tampered_or_expired_session_cookie_is_ignored(client):
    assert auth.read_token("garbage") is None
    good = auth.make_token("owner@gmail.com")
    payload, sig = good.split(".")
    assert auth.read_token(f"{payload}.{sig[::-1]}") is None
    assert auth.read_token(auth._sign({"u": "owner@gmail.com"}, -10)) is None
    assert auth.read_token(good)["username"] == "owner@gmail.com"


def test_the_privacy_policy_is_public(client):
    r = client.get("/privacy")  # no session
    assert r.status_code == 200 and "Privacy Policy" in r.text
    assert client.get("/api/stores").status_code == 401  # everything else still needs one
