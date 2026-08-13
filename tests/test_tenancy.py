"""Multi-tenant accounts, session auth, and project ownership (ADR 0027).

The tenancy tests here are the security boundary: if any of them fail, one
customer can see another's infrastructure inventory.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from iactranslate.api.accounts import (
    AccountStore,
    EmailTaken,
    InvalidCredentials,
    hash_password,
    validate_email,
    validate_password,
    verify_password,
)


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """A TestClient with multi-tenant mode on and a per-test database."""
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "tenancy.db"))
    monkeypatch.setenv("IACTRANSLATE_COOKIE_SECURE", "0")  # TestClient speaks http
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    import iactranslate.api.main as main
    importlib.reload(main)  # module-level store/accounts read env at import
    yield TestClient(main.app)
    # Undo the env *before* the teardown reload: that reload re-reads the
    # environment, so reloading first would leave the module stuck in
    # multi-tenant mode and 401 every later test in the session.
    monkeypatch.undo()
    importlib.reload(main)


def _signup(client: TestClient, email: str) -> TestClient:
    r = client.post("/auth/register", json={"email": email, "password": "correct-horse-battery"})
    assert r.status_code == 201, r.text
    return client


# -- password hashing ------------------------------------------------------


def test_password_hash_is_salted_and_verifiable():
    a, b = hash_password("same-password-twice"), hash_password("same-password-twice")
    assert a != b  # per-user salt, so identical passwords hash differently
    assert verify_password("same-password-twice", a)
    assert not verify_password("wrong-password", a)


def test_password_hash_never_stores_the_plaintext():
    encoded = hash_password("hunter2-hunter2-hunter2")
    assert "hunter2" not in encoded
    assert encoded.startswith("pbkdf2_sha256$600000$")


def test_verify_password_rejects_malformed_hashes():
    for bad in ("", "garbage", "md5$1$aa$bb", "pbkdf2_sha256$notanint$aa$bb"):
        assert not verify_password("x", bad)


def test_validation_rules():
    assert validate_email("  Alice@Example.COM ") == "alice@example.com"
    for bad in ("no-at-sign", "a@b", "", "a b@c.com"):
        with pytest.raises(ValueError):
            validate_email(bad)
    with pytest.raises(ValueError):
        validate_password("tooshort")
    with pytest.raises(ValueError):
        validate_password("x" * 2000)  # PBKDF2 CPU-exhaustion guard


# -- account store ---------------------------------------------------------


def test_accounts_survive_a_restart(tmp_path):
    db = str(tmp_path / "a.db")
    user = AccountStore(db).create_user("a@example.com", "correct-horse-battery")
    reopened = AccountStore(db)  # a fresh process
    assert reopened.authenticate("a@example.com", "correct-horse-battery").id == user.id


def test_duplicate_email_is_rejected(tmp_path):
    store = AccountStore(str(tmp_path / "a.db"))
    store.create_user("dupe@example.com", "correct-horse-battery")
    with pytest.raises(EmailTaken):
        store.create_user("dupe@example.com", "a-different-password")


def test_authenticate_rejects_wrong_password_and_unknown_user(tmp_path):
    store = AccountStore(str(tmp_path / "a.db"))
    store.create_user("a@example.com", "correct-horse-battery")
    with pytest.raises(InvalidCredentials):
        store.authenticate("a@example.com", "wrong-password-here")
    with pytest.raises(InvalidCredentials):
        store.authenticate("nobody@example.com", "correct-horse-battery")


def test_session_tokens_are_stored_hashed_not_plaintext(tmp_path):
    db = str(tmp_path / "a.db")
    store = AccountStore(db)
    user = store.create_user("a@example.com", "correct-horse-battery")
    token = store.create_session(user.id)

    rows = store._conn.execute("SELECT token_hash FROM sessions").fetchall()
    assert rows and token not in [r[0] for r in rows]  # a DB leak yields no usable session
    assert store.user_for_session(token).id == user.id


def test_expired_and_invalid_sessions_resolve_to_nobody(tmp_path):
    store = AccountStore(str(tmp_path / "a.db"))
    user = store.create_user("a@example.com", "correct-horse-battery")
    assert store.user_for_session(store.create_session(user.id, ttl_seconds=-1)) is None
    assert store.user_for_session("not-a-real-token") is None
    assert store.user_for_session("") is None


def test_logout_invalidates_the_session(tmp_path):
    store = AccountStore(str(tmp_path / "a.db"))
    user = store.create_user("a@example.com", "correct-horse-battery")
    token = store.create_session(user.id)
    store.delete_session(token)
    assert store.user_for_session(token) is None


# -- the tenancy boundary --------------------------------------------------


def test_project_endpoints_401_without_a_session(api):
    assert api.post("/projects", json={"name": "x", "target": "aws"}).status_code == 401
    assert api.get("/projects").status_code == 401
    assert api.get("/projects/anything").status_code == 401


def test_one_tenant_cannot_see_anothers_project(api, tmp_path):
    _signup(api, "alice@example.com")
    pid = api.post("/projects", json={"name": "alice-estate", "target": "aws"}).json()["id"]
    assert api.get(f"/projects/{pid}").status_code == 200

    api.post("/auth/logout")
    _signup(api, "mallory@example.com")

    # 404, not 403 — Mallory must not learn that this project id exists.
    for method, path in [
        ("get", f"/projects/{pid}"),
        ("delete", f"/projects/{pid}"),
        ("post", f"/projects/{pid}/run"),
        ("post", f"/projects/{pid}/assess"),
        ("post", f"/projects/{pid}/recommend"),
        ("post", f"/projects/{pid}/report"),
        ("get", f"/projects/{pid}/download"),
        ("post", f"/projects/{pid}/jobs"),
    ]:
        assert getattr(api, method)(path).status_code == 404, f"{method} {path} leaked"


def test_project_list_is_scoped_to_the_owner(api):
    _signup(api, "alice@example.com")
    api.post("/projects", json={"name": "alice-one", "target": "aws"})
    api.post("/projects", json={"name": "alice-two", "target": "aws"})
    assert {p["name"] for p in api.get("/projects").json()} == {"alice-one", "alice-two"}

    api.post("/auth/logout")
    _signup(api, "bob@example.com")
    assert api.get("/projects").json() == []  # Bob sees none of Alice's work
    api.post("/projects", json={"name": "bob-only", "target": "aws"})
    assert [p["name"] for p in api.get("/projects").json()] == ["bob-only"]


def test_login_logout_round_trip(api):
    _signup(api, "alice@example.com")
    assert api.get("/auth/me").json()["email"] == "alice@example.com"

    api.post("/auth/logout")
    assert api.get("/auth/me").status_code == 401

    r = api.post("/auth/login", json={"email": "alice@example.com", "password": "correct-horse-battery"})
    assert r.status_code == 200
    assert api.get("/auth/me").json()["authenticated"] is True


def test_login_failures_do_not_reveal_whether_an_account_exists(api):
    _signup(api, "alice@example.com")
    api.post("/auth/logout")
    known = api.post("/auth/login", json={"email": "alice@example.com", "password": "wrong-password-x"})
    unknown = api.post("/auth/login", json={"email": "ghost@example.com", "password": "wrong-password-x"})
    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json()


def test_duplicate_registration_does_not_confirm_the_email(api):
    _signup(api, "alice@example.com")
    r = api.post("/auth/register", json={"email": "alice@example.com", "password": "another-password"})
    assert r.status_code == 400
    assert "alice@example.com" not in r.text


def test_session_cookie_is_httponly_and_samesite(api):
    r = api.post("/auth/register", json={"email": "a@example.com", "password": "correct-horse-battery"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie          # XSS cannot read it
    assert "samesite=lax" in cookie      # CSRF-resistant, but navigations still work


def test_job_ids_do_not_leak_another_tenants_project(api):
    _signup(api, "alice@example.com")
    pid = api.post("/projects", json={"name": "alice-estate", "target": "aws"}).json()["id"]
    # A job needs an upload; assert the handle is guarded even before that.
    assert api.post(f"/projects/{pid}/jobs").status_code == 400

    api.post("/auth/logout")
    _signup(api, "mallory@example.com")
    assert api.post(f"/projects/{pid}/jobs").status_code == 404


def test_audit_trail_is_scoped_to_the_callers_projects(api):
    _signup(api, "alice@example.com")
    alice_pid = api.post("/projects", json={"name": "alice-estate", "target": "aws"}).json()["id"]
    assert any(e["project_id"] == alice_pid for e in api.get("/audit").json())

    api.post("/auth/logout")
    _signup(api, "mallory@example.com")
    events = api.get("/audit").json()
    assert all(e["project_id"] != alice_pid for e in events)  # no cross-tenant activity
    assert api.get(f"/audit?project_id={alice_pid}").status_code == 404


def test_health_and_metrics_stay_open_in_multi_tenant_mode(api):
    assert api.get("/health").status_code == 200
    assert api.get("/metrics").status_code == 200
    assert api.get("/targets").status_code == 200
