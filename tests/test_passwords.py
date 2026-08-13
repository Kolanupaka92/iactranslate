"""Password change and reset (ADR 0030).

The recurring theme: a password change must *evict* anyone already holding a
session. A reset that leaves the attacker's cookie working is not a recovery.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from iactranslate.api import delivery
from iactranslate.api.accounts import AccountStore

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "an-entirely-different-passphrase"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "pw.db"))
    monkeypatch.setenv("IACTRANSLATE_COOKIE_SECURE", "0")
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    import iactranslate.api.main as main
    importlib.reload(main)
    yield TestClient(main.app)
    monkeypatch.undo()
    importlib.reload(main)


@pytest.fixture()
def captured_links(monkeypatch):
    """Capture reset links instead of logging them."""
    sent = []
    delivery.set_link_delivery(lambda email, url: sent.append((email, url)))
    yield sent
    delivery.set_link_delivery(None)


def _token_from(url: str) -> str:
    return url.split("token=", 1)[1]


# -- token mechanics -------------------------------------------------------


def test_reset_token_is_stored_hashed(tmp_path):
    store = AccountStore(str(tmp_path / "a.db"))
    user = store.create_user("a@example.com", PASSWORD)
    token = store.create_reset_token(user.id)

    rows = store._conn.execute("SELECT token_hash FROM password_resets").fetchall()
    assert rows and token not in [r[0] for r in rows]


def test_reset_token_is_single_use(tmp_path):
    store = AccountStore(str(tmp_path / "a.db"))
    user = store.create_user("a@example.com", PASSWORD)
    token = store.create_reset_token(user.id)

    assert store.consume_reset_token(token) == user.id
    assert store.consume_reset_token(token) is None  # replay refused


def test_expired_reset_token_is_refused_and_burned(tmp_path):
    store = AccountStore(str(tmp_path / "a.db"))
    user = store.create_user("a@example.com", PASSWORD)
    token = store.create_reset_token(user.id, ttl_seconds=-1)

    assert store.consume_reset_token(token) is None
    # Burned even though it was expired — it can never come back.
    assert store._conn.execute("SELECT COUNT(*) FROM password_resets").fetchone()[0] == 0


def test_requesting_a_new_token_invalidates_the_previous_one(tmp_path):
    """A stale link in an older email must stop working."""
    store = AccountStore(str(tmp_path / "a.db"))
    user = store.create_user("a@example.com", PASSWORD)
    first = store.create_reset_token(user.id)
    second = store.create_reset_token(user.id)

    assert store.consume_reset_token(first) is None
    assert store.consume_reset_token(second) == user.id


def test_unknown_token_resolves_to_nobody(tmp_path):
    store = AccountStore(str(tmp_path / "a.db"))
    assert store.consume_reset_token("not-a-real-token") is None
    assert store.consume_reset_token("") is None


# -- the reset flow --------------------------------------------------------


def test_forgot_password_does_not_reveal_whether_an_account_exists(api, captured_links):
    api.post("/auth/register", json={"email": "real@example.com", "password": PASSWORD})
    api.post("/auth/logout")

    known = api.post("/auth/forgot-password", json={"email": "real@example.com"})
    unknown = api.post("/auth/forgot-password", json={"email": "ghost@example.com"})

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    # Only the real account actually got a link.
    assert [e for e, _ in captured_links] == ["real@example.com"]


def test_reset_lets_the_user_sign_in_with_the_new_password(api, captured_links):
    api.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    api.post("/auth/logout")
    api.post("/auth/forgot-password", json={"email": "a@example.com"})

    token = _token_from(captured_links[-1][1])
    assert api.post(
        "/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}
    ).status_code == 204

    assert api.post("/auth/login", json={"email": "a@example.com", "password": PASSWORD}).status_code == 401
    assert api.post("/auth/login", json={"email": "a@example.com", "password": NEW_PASSWORD}).status_code == 200


def test_reset_signs_out_every_existing_session(api, captured_links):
    """The point of a reset: an attacker holding a stolen cookie gets evicted."""
    api.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    attacker = TestClient(api.app)
    attacker.post("/auth/login", json={"email": "a@example.com", "password": PASSWORD})
    assert attacker.get("/auth/me").status_code == 200  # attacker is in

    api.post("/auth/forgot-password", json={"email": "a@example.com"})
    token = _token_from(captured_links[-1][1])
    api.post("/auth/reset-password", json={"token": token, "password": NEW_PASSWORD})

    assert attacker.get("/auth/me").status_code == 401  # and now out


def test_reset_rejects_a_bad_token_and_a_weak_password(api, captured_links):
    api.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    api.post("/auth/logout")
    api.post("/auth/forgot-password", json={"email": "a@example.com"})
    token = _token_from(captured_links[-1][1])

    assert api.post("/auth/reset-password", json={"token": "bogus", "password": NEW_PASSWORD}).status_code == 400
    assert api.post("/auth/reset-password", json={"token": token, "password": "short"}).status_code == 400
    # The weak-password attempt consumed the token; the user must request again.
    assert api.post("/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}).status_code == 400


# -- changing your own password --------------------------------------------


def test_change_password_requires_the_current_one(api):
    api.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    r = api.post(
        "/auth/change-password",
        json={"current_password": "not-the-right-password", "new_password": NEW_PASSWORD},
    )
    assert r.status_code == 403
    # Unchanged: the old password still works.
    api.post("/auth/logout")
    assert api.post("/auth/login", json={"email": "a@example.com", "password": PASSWORD}).status_code == 200


def test_change_password_keeps_you_signed_in_but_evicts_everyone_else(api):
    api.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    attacker = TestClient(api.app)
    attacker.post("/auth/login", json={"email": "a@example.com", "password": PASSWORD})
    assert attacker.get("/auth/me").status_code == 200

    assert api.post(
        "/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    ).status_code == 204

    assert api.get("/auth/me").status_code == 200      # you stay signed in
    assert attacker.get("/auth/me").status_code == 401  # the other session dies


def test_change_password_requires_a_session(api):
    r = api.post(
        "/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert r.status_code == 401


def test_change_password_enforces_the_length_rule(api):
    api.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
    r = api.post(
        "/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "tooshort"},
    )
    assert r.status_code == 400


# -- delivery seam ---------------------------------------------------------


def test_delivery_failure_never_leaks_account_existence(api, monkeypatch):
    """A throwing backend must not become a 500 that distinguishes real
    accounts from unknown ones."""
    def _explode(email, url):
        raise RuntimeError("smtp is down")

    delivery.set_link_delivery(_explode)
    try:
        api.post("/auth/register", json={"email": "a@example.com", "password": PASSWORD})
        api.post("/auth/logout")
        known = api.post("/auth/forgot-password", json={"email": "a@example.com"})
        unknown = api.post("/auth/forgot-password", json={"email": "ghost@example.com"})
        assert known.status_code == unknown.status_code == 202
    finally:
        delivery.set_link_delivery(None)


def test_reset_url_uses_the_app_origin(monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_APP_URL", "https://app.example.com/")
    assert delivery.reset_url("abc123") == "https://app.example.com/reset-password?token=abc123"
