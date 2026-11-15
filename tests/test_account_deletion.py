"""Deleting your own account.

The happy path is one call. Almost everything here is about what must NOT
happen: a stolen session deleting an account, a deletion that leaves the
estate on disk, an audit trail that keeps the email of someone who asked to
be forgotten, or a grant that survives its holder.
"""
import importlib

import pytest
from fastapi.testclient import TestClient

PW = "correct horse battery staple"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("IACTRANSLATE_COOKIE_SECURE", "0")  # TestClient speaks http
    from iactranslate.api import main as api_main
    importlib.reload(api_main)
    yield TestClient(api_main.app), api_main, tmp_path
    monkeypatch.undo()
    importlib.reload(api_main)


def _signup(client, email):
    """Register and sign in — registering alone does not start a session."""
    r = client.post("/v1/auth/register", json={"email": email, "password": PW})
    assert r.status_code == 201, r.text
    assert client.post("/v1/auth/login", json={"email": email, "password": PW}).status_code == 200
    return r.json()["id"]


def _project(client, name="estate"):
    pid = client.post("/v1/projects", json={"name": name}).json()["id"]
    with open("tests/fixtures/rvtools_sample.xlsx", "rb") as f:
        client.post(f"/v1/projects/{pid}/upload", files={"file": ("i.xlsx", f)})
    return pid


# --- what must not be possible ------------------------------------------------

def test_a_session_alone_cannot_delete_the_account(api):
    """A stolen cookie can already read the estate. It must not also be able
    to make the loss permanent."""
    client, _, _ = api
    _signup(client, "a@acme.test")
    r = client.request("DELETE", "/v1/auth/me", json={"current_password": "not it"})
    assert r.status_code == 403
    assert client.get("/v1/auth/me").json()["authenticated"] is True


def test_signed_out_callers_are_refused(api):
    client, _, _ = api
    r = client.request("DELETE", "/v1/auth/me", json={"current_password": PW})
    assert r.status_code == 401


# --- what must happen ---------------------------------------------------------

def test_deletion_removes_the_account_its_sessions_and_its_projects(api):
    client, m, root = api
    uid = _signup(client, "gone@acme.test")
    pid = _project(client)
    workspace = m.store.get(pid).workspace
    assert workspace.exists()

    r = client.request("DELETE", "/v1/auth/me", json={"current_password": PW})
    assert r.status_code == 204

    # The session is dead, the account is gone, the login no longer works.
    assert client.get("/v1/auth/me").status_code == 401
    assert client.post("/v1/auth/login", json={"email": "gone@acme.test", "password": PW}).status_code == 401
    assert m.accounts.get_user(uid) is None
    # The project row and its files on disk are both gone — "deleted" means deleted.
    assert m.store.get(pid) is None
    assert not workspace.exists()


def test_grants_the_user_held_on_others_projects_are_revoked(api):
    """A grant without a holder is a dangling authorisation. If the id were
    ever reused it would silently confer access."""
    client, m, _ = api
    owner = _signup(client, "owner@acme.test")
    pid = _project(client, "shared")
    client.post("/v1/auth/logout")

    guest = _signup(client, "guest@acme.test")
    client.post("/v1/auth/logout")

    # Owner grants the guest access.
    client.post("/v1/auth/login", json={"email": "owner@acme.test", "password": PW})
    m.memberships.grant(pid, guest, m.Role.VIEWER)
    assert guest in [x["user_id"] for x in m.memberships.members(pid, owner)]
    client.post("/v1/auth/logout")

    # Guest deletes themselves.
    client.post("/v1/auth/login", json={"email": "guest@acme.test", "password": PW})
    assert client.request("DELETE", "/v1/auth/me", json={"current_password": PW}).status_code == 204
    assert guest not in [x["user_id"] for x in m.memberships.members(pid, owner)]
    # The owner's project is untouched.
    assert m.store.get(pid) is not None


def test_the_audit_trail_records_the_deletion_but_not_the_email(api):
    """Retaining who it was, after they asked to be removed, is the thing
    deletion exists to prevent. That it happened is legitimate to keep."""
    client, m, _ = api
    uid = _signup(client, "forgetme@acme.test")
    client.request("DELETE", "/v1/auth/me", json={"current_password": PW})
    events = [e for e in m.audit.recent(limit=50) if e.action == "account.deleted"]
    assert len(events) == 1
    assert events[0].detail["user_id"] == uid
    assert "forgetme" not in repr(events[0])


def test_deleting_twice_is_not_an_error_the_second_time(api):
    """Idempotent: a retry after an interrupted deletion must not 500."""
    client, m, _ = api
    uid = _signup(client, "twice@acme.test")
    assert m.accounts.delete_user(uid) is True
    assert m.accounts.delete_user(uid) is False
