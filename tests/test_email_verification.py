"""Email verification (ADR 0079).

A verified address is one whose owner has shown they receive its mail: a
verification link, a password-reset link, or an identity provider asserting it.
Also covered: the SSO pre-account-takeover fix, where someone registers an
address before its owner's first SSO sign-in.
"""
from __future__ import annotations

import email
import email.policy
import importlib
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iactranslate.api import delivery
from iactranslate.api.accounts import AccountStore

PASSWORD = "correct-horse-battery"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "verify.db"))
    monkeypatch.setenv("IACTRANSLATE_COOKIE_SECURE", "0")
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    monkeypatch.delenv("IACTRANSLATE_REQUIRE_VERIFIED_EMAIL", raising=False)
    import iactranslate.api.main as main
    importlib.reload(main)
    yield main
    monkeypatch.undo()
    importlib.reload(main)


@pytest.fixture()
def links():
    sent = {"verify": [], "reset": []}
    delivery.set_verification_delivery(lambda e, url: sent["verify"].append((e, url)))
    delivery.set_link_delivery(lambda e, url: sent["reset"].append((e, url)))
    yield sent
    delivery.set_verification_delivery(None)
    delivery.set_link_delivery(None)


def _token(url: str) -> str:
    return url.split("token=", 1)[1]


def _register(client, address="pat@example.com"):
    r = client.post("/auth/register", json={"email": address, "password": PASSWORD})
    assert r.status_code == 201, r.text
    return r


# --- the link ------------------------------------------------------------------------


def test_registering_sends_a_link_and_starts_unverified(api, links):
    client = TestClient(api.app)
    assert _register(client).json()["email_verified"] is False
    ((address, url),) = links["verify"]
    assert address == "pat@example.com" and "/verify-email?token=" in url
    me = client.get("/auth/me").json()
    assert me["email_verified"] is False and me["verification_required"] is False


def test_the_link_verifies_once(api, links):
    client = TestClient(api.app)
    _register(client)
    token = _token(links["verify"][0][1])
    # No session needed: the mail is often opened on another device.
    assert TestClient(api.app).post("/auth/verify-email", json={"token": token}).status_code == 204
    assert client.get("/auth/me").json()["email_verified"] is True
    r = client.post("/auth/verify-email", json={"token": token})
    assert r.status_code == 400 and "invalid or has expired" in r.json()["detail"]


def test_an_expired_link_is_refused(api, links):
    client = TestClient(api.app)
    user_id = _register(client).json()["id"]
    stale = api.accounts.create_verification_token(user_id, ttl_seconds=-1)
    assert client.post("/auth/verify-email", json={"token": stale}).status_code == 400
    assert client.get("/auth/me").json()["email_verified"] is False


def test_a_new_link_replaces_the_old_one(api, links):
    client = TestClient(api.app)
    _register(client)
    first = _token(links["verify"][0][1])
    assert client.post("/auth/verify-email/resend").status_code == 202
    second = _token(links["verify"][1][1])
    assert client.post("/auth/verify-email", json={"token": first}).status_code == 400
    assert client.post("/auth/verify-email", json={"token": second}).status_code == 204


def test_resend_needs_a_session_and_stops_once_verified(api, links):
    assert TestClient(api.app).post("/auth/verify-email/resend").status_code == 401
    client = TestClient(api.app)
    _register(client)
    client.post("/auth/verify-email", json={"token": _token(links["verify"][0][1])})
    assert client.post("/auth/verify-email/resend").status_code == 202
    assert len(links["verify"]) == 1  # nothing new for a verified address


def test_a_password_reset_proves_the_address(api, links):
    client = TestClient(api.app)
    _register(client)
    client.post("/auth/forgot-password", json={"email": "pat@example.com"})
    token = _token(links["reset"][0][1])
    assert client.post("/auth/reset-password",
                       json={"token": token, "password": "a-brand-new-passphrase"}).status_code == 204
    client.post("/auth/login", json={"email": "pat@example.com", "password": "a-brand-new-passphrase"})
    assert client.get("/auth/me").json()["email_verified"] is True


# --- enforcement ----------------------------------------------------------------------


def test_projects_wait_for_verification_only_when_required(api, links, monkeypatch):
    client = TestClient(api.app)
    _register(client)
    assert client.post("/projects", json={"name": "a", "target": "aws"}).status_code == 201

    monkeypatch.setenv("IACTRANSLATE_REQUIRE_VERIFIED_EMAIL", "1")
    assert client.get("/auth/me").json()["verification_required"] is True
    r = client.post("/projects", json={"name": "b", "target": "aws"})
    assert r.status_code == 403 and "confirm your email" in r.json()["detail"]
    client.post("/auth/verify-email", json={"token": _token(links["verify"][0][1])})
    assert client.post("/projects", json={"name": "b", "target": "aws"}).status_code == 201


# --- storage --------------------------------------------------------------------------


def test_accounts_from_before_verification_come_back_unverified(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, "
                "password_hash TEXT NOT NULL, created_at REAL NOT NULL)")
    con.execute("INSERT INTO users VALUES ('u1', 'old@example.com', 'x', 1.0)")
    con.commit()
    con.close()
    store = AccountStore(str(path))
    user = store.get_user("u1")
    assert user.email == "old@example.com" and user.email_verified is False


def test_deleting_an_account_removes_its_pending_link(tmp_path):
    store = AccountStore(str(tmp_path / "d.db"))
    user = store.create_user("gone@example.com", PASSWORD)
    token = store.create_verification_token(user.id)
    store.delete_user(user.id)
    assert store.consume_verification_token(token) is None


# --- SSO ----------------------------------------------------------------------------


def _sso(api, monkeypatch, email_verified=True):
    """Drive the callback with the provider calls stubbed: these tests are about
    what happens to the account, not about token validation (test_sso.py)."""
    oidc = api._oidc
    monkeypatch.setattr(api, "sso_config", SimpleNamespace())
    monkeypatch.setattr(api.sso_logins, "consume",
                        lambda state: SimpleNamespace(verifier="v", nonce="n"))
    monkeypatch.setattr(oidc, "exchange_code", lambda cfg, code, verifier: {"id_token": "t"})
    claims = {"sub": "subject-1", "email": "pat@example.com", "email_verified": email_verified}
    monkeypatch.setattr(oidc, "verify_id_token", lambda cfg, tok, nonce: claims)
    monkeypatch.setattr(oidc, "identity_from_claims", lambda cfg, c: ("subject-1", "pat@example.com"))
    # A real browser carries the state cookie set when it started the login.
    browser = TestClient(api.app, base_url="https://testserver")
    browser.cookies.set(oidc.STATE_COOKIE, "s")
    r = browser.get("/auth/sso/callback?code=c&state=s", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/console"), r.headers.get("location")
    return r


def test_sso_takes_an_unverified_account_back_from_a_squatter(api, links, monkeypatch):
    squatter = TestClient(api.app)
    _register(squatter)  # someone else registered the address first
    assert _sso(api, monkeypatch).status_code == 303
    # The squatter's session and password no longer work.
    assert squatter.get("/auth/me").status_code == 401
    r = TestClient(api.app).post("/auth/login", json={"email": "pat@example.com", "password": PASSWORD})
    assert r.status_code == 401
    assert api.accounts.get_user_by_email("pat@example.com").email_verified is True


def test_sso_leaves_a_verified_account_alone(api, links, monkeypatch):
    owner = TestClient(api.app)
    _register(owner)
    owner.post("/auth/verify-email", json={"token": _token(links["verify"][0][1])})
    assert _sso(api, monkeypatch).status_code == 303
    assert owner.get("/auth/me").status_code == 200
    r = TestClient(api.app).post("/auth/login", json={"email": "pat@example.com", "password": PASSWORD})
    assert r.status_code == 200


def test_an_unasserted_sso_email_proves_nothing(api, links, monkeypatch):
    """With trust_unverified_email the operator accepted an address the
    provider didn't vouch for. That neither verifies nor revokes anything."""
    squatter = TestClient(api.app)
    _register(squatter)
    assert _sso(api, monkeypatch, email_verified=False).status_code == 303
    assert squatter.get("/auth/me").status_code == 200
    assert api.accounts.get_user_by_email("pat@example.com").email_verified is False


# --- by email, over SMTP ------------------------------------------------------------


@pytest.fixture()
def mailing_api(tmp_path, monkeypatch, smtp_server):
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "mail.db"))
    monkeypatch.setenv("IACTRANSLATE_COOKIE_SECURE", "0")
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    monkeypatch.setenv("IACTRANSLATE_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("IACTRANSLATE_SMTP_PORT", str(smtp_server.server_address[1]))
    monkeypatch.setenv("IACTRANSLATE_SMTP_TLS", "none")
    monkeypatch.setenv("IACTRANSLATE_RESET_EMAIL_FROM", "no-reply@example.com")
    monkeypatch.setenv("IACTRANSLATE_APP_URL", "https://app.example")
    import iactranslate.api.main as main
    importlib.reload(delivery)
    importlib.reload(main)
    yield TestClient(main.app), smtp_server
    monkeypatch.undo()
    importlib.reload(delivery)
    importlib.reload(main)


def test_the_confirmation_email_arrives_and_its_link_works(mailing_api):
    client, relay = mailing_api
    _register(client, "sam@example.com")
    assert client.get("/auth/me").json()["verification_email"] is True
    assert delivery._email.flush(10)
    (raw,) = relay.messages
    msg = email.message_from_string(raw["data"], policy=email.policy.default)
    assert msg["Subject"] == "Confirm your email for IaCTranslate" and msg["To"] == "sam@example.com"
    body = msg.get_content()
    assert "within 24 hours" in body
    url = next(line for line in body.splitlines() if "/verify-email?token=" in line).strip()
    assert url.startswith("https://app.example/verify-email?token=")
    assert client.post("/auth/verify-email", json={"token": _token(url)}).status_code == 204
    assert client.get("/auth/me").json()["email_verified"] is True
