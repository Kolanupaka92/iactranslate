"""The OIDC sign-in flow end to end, through the API (ADR 0082).

test_sso.py attacks token validation in isolation. This drives the browser
flow: the redirect, the state cookie, a real token endpoint over a socket
(which checks the PKCE verifier), real ID-token verification, and the redirect
back to the console. Only the provider's key download (JWKS) is stubbed.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from iactranslate.api import oidc
from iactranslate.api.sql import Database

APP = "https://app.example"
ISSUER = "https://idp.corp.test"
CLIENT_ID = "iactranslate"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _TokenEndpoint:
    """Answers the code exchange. Checks the PKCE verifier against the
    challenge the browser was sent, like a real provider."""

    def __init__(self):
        self.challenge = None
        self.id_token = None
        self.status = 200
        self.calls = []
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
                endpoint.calls.append(form)
                verifier = form.get("code_verifier", [""])[0]
                derived = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                ok = endpoint.status == 200 and derived == endpoint.challenge
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                body = {"id_token": endpoint.id_token} if ok else {"error": "invalid_grant"}
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/token"


@pytest.fixture()
def api(tmp_path, monkeypatch, key):
    token = _TokenEndpoint()
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "oidc.db"))
    monkeypatch.setenv("IACTRANSLATE_RATE_AUTH", "0")
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    monkeypatch.setenv("IACTRANSLATE_APP_URL", APP)
    monkeypatch.setenv("IACTRANSLATE_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("IACTRANSLATE_OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("IACTRANSLATE_OIDC_CLIENT_SECRET", "shh")
    monkeypatch.delenv("IACTRANSLATE_OIDC_REDIRECT_URI", raising=False)
    monkeypatch.delenv("IACTRANSLATE_SAML_IDP_ENTITY_ID", raising=False)

    class _Jwks:  # the provider's published key, without the download
        def __init__(self, *a, **k):
            pass

        def get_signing_key_from_jwt(self, token):
            class _K:
                pass
            k = _K()
            k.key = key.public_key()
            return k

    monkeypatch.setattr(jwt, "PyJWKClient", _Jwks)
    import iactranslate.api.main as main
    importlib.reload(main)
    main.sso_config._metadata = {"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize",
                                 "token_endpoint": token.url, "jwks_uri": f"{ISSUER}/jwks"}
    yield main, token
    token.server.shutdown()
    monkeypatch.undo()
    importlib.reload(main)


def _mint(key, nonce, email="pat@corp.test", **overrides):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "subject-1", "exp": now + 300, "iat": now,
              "nonce": nonce, "email": email, "email_verified": True, **overrides}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def _browser(main) -> TestClient:
    return TestClient(main.app, base_url="https://testserver")


def _start(browser, token):
    """Begin a login; returns (state, nonce) and primes the token endpoint."""
    r = browser.get("/auth/sso/login", follow_redirects=False)
    assert r.status_code == 302
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)
    token.challenge = query["code_challenge"][0]
    return query["state"][0], query["nonce"][0]


def _callback(browser, state, code="the-code", **extra):
    params = urllib.parse.urlencode({"code": code, "state": state, **extra})
    return browser.get(f"/auth/sso/callback?{params}", follow_redirects=False)


def _failed(r) -> bool:
    return r.status_code == 303 and r.headers["location"] == f"{APP}/console?sso=failed"


# --- the flow ----------------------------------------------------------------------------


def test_login_redirects_with_pkce_and_sets_a_state_cookie(api):
    main, token = api
    browser = _browser(main)
    r = browser.get("/auth/sso/login", follow_redirects=False)
    location = urllib.parse.urlsplit(r.headers["location"])
    query = urllib.parse.parse_qs(location.query)
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{ISSUER}/authorize"
    assert query["code_challenge_method"] == ["S256"] and query["response_type"] == ["code"]
    assert query["redirect_uri"] == [f"{APP}/api/v1/auth/sso/callback"]  # the default
    assert browser.cookies.get(oidc.STATE_COOKIE) == query["state"][0]
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie


def test_a_successful_sign_in_returns_to_the_console(api, key):
    main, token = api
    browser = _browser(main)
    state, nonce = _start(browser, token)
    token.id_token = _mint(key, nonce)
    r = _callback(browser, state)
    assert r.status_code == 303 and r.headers["location"] == f"{APP}/console"
    me = browser.get("/auth/me").json()
    assert me["email"] == "pat@corp.test" and me["email_verified"] is True
    assert browser.cookies.get(oidc.STATE_COOKIE) is None
    assert token.calls[0]["redirect_uri"] == [f"{APP}/api/v1/auth/sso/callback"]


def test_methods_offer_oidc(api):
    main, _ = api
    assert _browser(main).get("/auth/methods").json() == {
        "password": True, "saml": False, "oidc": True, "oidc_name": None}


# --- refused -------------------------------------------------------------------------------


def test_login_csrf_is_refused(api, key):
    """The attacker starts a login, signs in as themselves at the provider, and
    sends the victim the callback link. The victim's browser has no matching
    state cookie, so it isn't signed in as the attacker."""
    main, token = api
    attacker = _browser(main)
    state, nonce = _start(attacker, token)
    token.id_token = _mint(key, nonce, email="mallory@corp.test")
    victim = _browser(main)
    r = _callback(victim, state)
    assert _failed(r) and victim.get("/auth/me").status_code == 401


def test_a_cookie_for_another_login_is_refused(api, key):
    main, token = api
    browser = _browser(main)
    state, nonce = _start(browser, token)
    other_state, _ = _start(_browser(main), token)
    token.id_token = _mint(key, nonce)
    assert _failed(_callback(browser, other_state))


def test_a_callback_works_once(api, key):
    main, token = api
    browser = _browser(main)
    state, nonce = _start(browser, token)
    token.id_token = _mint(key, nonce)
    assert _callback(browser, state).headers["location"] == f"{APP}/console"
    replay = _browser(main)
    replay.cookies.set(oidc.STATE_COOKIE, state)
    assert _failed(_callback(replay, state))


def test_a_token_for_another_login_is_refused(api, key):
    """The nonce in the ID token must be this login's."""
    main, token = api
    browser = _browser(main)
    state, _ = _start(browser, token)
    token.id_token = _mint(key, "someone-elses-nonce")
    assert _failed(_callback(browser, state))


def test_a_token_signed_by_another_key_is_refused(api):
    main, token = api
    browser = _browser(main)
    state, nonce = _start(browser, token)
    token.id_token = _mint(rsa.generate_private_key(public_exponent=65537, key_size=2048), nonce)
    assert _failed(_callback(browser, state))


def test_a_refused_code_goes_back_to_sign_in_not_a_500(api):
    main, token = api
    browser = _browser(main)
    state, _ = _start(browser, token)
    token.status = 400
    assert _failed(_callback(browser, state))


def test_a_provider_error_goes_back_to_sign_in(api):
    main, token = api
    browser = _browser(main)
    state, _ = _start(browser, token)
    assert _failed(_callback(browser, state, code="", error="access_denied"))


# --- shared across instances ------------------------------------------------------------


def test_a_login_started_on_one_instance_finishes_on_another(tmp_path):
    path = str(tmp_path / "shared.db")
    one = oidc.SqlLoginStore(Database("sqlite", path))
    two = oidc.SqlLoginStore(Database("sqlite", path))
    login = one.begin()
    finished = two.consume(login.state)
    assert (finished.nonce, finished.verifier) == (login.nonce, login.verifier)
    with pytest.raises(oidc.OidcError, match="already-used"):
        one.consume(login.state)


def test_an_expired_login_is_refused(tmp_path):
    store = oidc.SqlLoginStore(Database("sqlite", str(tmp_path / "e.db")), ttl=-1)
    login = store.begin()
    with pytest.raises(oidc.OidcError, match="expired"):
        store.consume(login.state)
