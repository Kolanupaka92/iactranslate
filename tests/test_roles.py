"""Project roles — separation of duties, not just ownership.

Before this a user either owned a project or it did not exist for them. These
tests are mostly about what each role is *refused*, because a permission model
is only worth having if the boundaries hold.
"""
import importlib

import pytest
from fastapi.testclient import TestClient

from iactranslate.api.roles import Membership, Role, at_least, parse_role

V1 = "/v1"


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Multi-tenant app with an isolated store and workspace."""
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(tmp_path))
    # Without this the account store defaults to ./iactranslate.db — a real file
    # in the repo root, shared between tests and holding password hashes.
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "accounts.db"))
    # TestClient speaks http, and a Secure cookie is rejected over http — the
    # session would be set and then silently dropped by the client.
    monkeypatch.setenv("IACTRANSLATE_COOKIE_SECURE", "0")
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    monkeypatch.setenv("IACTRANSLATE_RATE_AUTH", "0")
    monkeypatch.setenv("IACTRANSLATE_RATE_WRITE", "0")
    monkeypatch.setenv("IACTRANSLATE_RATE_READ", "0")
    from iactranslate.api import main as api_main
    importlib.reload(api_main)
    try:
        yield api_main
    finally:
        monkeypatch.undo()
        importlib.reload(api_main)


def _signup(mod, email):
    client = TestClient(mod.app)
    r = client.post(f"{V1}/auth/register", json={"email": email, "password": "correct-horse-9!"})
    assert r.status_code in (200, 201), r.text
    return client


def _project(client, name="shared"):
    r = client.post(f"{V1}/projects", json={"name": name, "target": "aws"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload(client, pid):
    with open("tests/fixtures/rvtools_sample.xlsx", "rb") as f:
        return client.post(
            f"{V1}/projects/{pid}/upload",
            files={"file": ("i.xlsx", f, "application/vnd.openxmlformats-"
                                         "officedocument.spreadsheetml.sheet")},
        )


# --- the ordering itself -----------------------------------------------------

def test_roles_are_ordered_so_checks_can_be_compared():
    assert at_least(Role.ADMIN, Role.VIEWER)
    assert at_least(Role.EDITOR, Role.EDITOR)
    assert not at_least(Role.VIEWER, Role.EDITOR)
    assert not at_least(Role.EDITOR, Role.APPROVER)


def test_an_unknown_role_names_the_alternatives():
    """Rejecting 'Editor' with just 'invalid' costs a round trip to the docs."""
    assert parse_role("EDITOR") is Role.EDITOR
    with pytest.raises(ValueError) as e:
        parse_role("superuser")
    assert "viewer" in str(e.value) and "admin" in str(e.value)


def test_the_owner_is_always_admin():
    m = Membership()
    assert m.role_for("p1", "owner", "owner") is Role.ADMIN


def test_an_explicit_grant_cannot_demote_the_owner():
    """A project whose owner was demoted would be unadministrable."""
    m = Membership()
    m.grant("p1", "owner", Role.VIEWER)
    assert m.role_for("p1", "owner", "owner") is Role.ADMIN


def test_deleting_a_project_forgets_its_grants():
    """A recycled id must not inherit access from a project that is gone."""
    m = Membership()
    m.grant("p1", "u2", Role.EDITOR)
    m.drop_project("p1")
    assert m.role_for("p1", "u2", "owner") is None


# --- enforcement through the API --------------------------------------------

def test_a_stranger_still_gets_404_not_403(api):
    """403 confirms the id exists, which enables enumeration (ADR 0027)."""
    owner = _signup(api, "owner@acme.test")
    pid = _project(owner)
    stranger = _signup(api, "stranger@acme.test")
    assert stranger.get(f"{V1}/projects/{pid}").status_code == 404


def test_a_viewer_can_read_but_not_run(api):
    """Insufficient access is 403: they already know the project exists, so 404
    would make a permissions problem look like a missing resource."""
    owner = _signup(api, "o2@acme.test")
    pid = _project(owner)
    _upload(owner, pid)
    viewer = _signup(api, "viewer@acme.test")
    assert owner.post(f"{V1}/projects/{pid}/members",
                      json={"email": "viewer@acme.test", "role": "viewer"}).status_code == 201

    assert viewer.get(f"{V1}/projects/{pid}").status_code == 200
    r = viewer.post(f"{V1}/projects/{pid}/run")
    assert r.status_code == 403
    assert "editor" in r.json()["detail"]


def test_a_viewer_cannot_upload(api):
    owner = _signup(api, "o3@acme.test")
    pid = _project(owner)
    viewer = _signup(api, "v3@acme.test")
    owner.post(f"{V1}/projects/{pid}/members", json={"email": "v3@acme.test", "role": "viewer"})
    assert _upload(viewer, pid).status_code == 403


def test_an_editor_can_run_but_not_delete(api):
    owner = _signup(api, "o4@acme.test")
    pid = _project(owner)
    _upload(owner, pid)
    editor = _signup(api, "e4@acme.test")
    owner.post(f"{V1}/projects/{pid}/members", json={"email": "e4@acme.test", "role": "editor"})

    assert editor.post(f"{V1}/projects/{pid}/run").status_code == 200
    assert editor.delete(f"{V1}/projects/{pid}").status_code == 403


def test_an_editor_cannot_grant_themselves_more(api):
    """The whole point of separating 'may act' from 'may grant access'."""
    owner = _signup(api, "o5@acme.test")
    pid = _project(owner)
    editor = _signup(api, "e5@acme.test")
    owner.post(f"{V1}/projects/{pid}/members", json={"email": "e5@acme.test", "role": "editor"})

    r = editor.post(f"{V1}/projects/{pid}/members",
                    json={"email": "e5@acme.test", "role": "admin"})
    assert r.status_code == 403


def test_an_admin_grant_confers_full_control(api):
    owner = _signup(api, "o6@acme.test")
    pid = _project(owner)
    admin = _signup(api, "a6@acme.test")
    owner.post(f"{V1}/projects/{pid}/members", json={"email": "a6@acme.test", "role": "admin"})
    assert admin.delete(f"{V1}/projects/{pid}").status_code == 204


def test_the_owner_cannot_be_removed(api):
    owner = _signup(api, "o7@acme.test")
    pid = _project(owner)
    me = owner.get(f"{V1}/auth/me").json()
    r = owner.delete(f"{V1}/projects/{pid}/members/{me['id']}")
    assert r.status_code == 400


def test_revoking_access_takes_effect_immediately(api):
    owner = _signup(api, "o8@acme.test")
    pid = _project(owner)
    other = _signup(api, "x8@acme.test")
    owner.post(f"{V1}/projects/{pid}/members", json={"email": "x8@acme.test", "role": "viewer"})
    assert other.get(f"{V1}/projects/{pid}").status_code == 200

    uid = other.get(f"{V1}/auth/me").json()["id"]
    assert owner.delete(f"{V1}/projects/{pid}/members/{uid}").status_code == 200
    assert other.get(f"{V1}/projects/{pid}").status_code == 404, "back to invisible"


def test_shared_projects_appear_in_the_recipients_listing(api):
    """A grant the recipient cannot see is a grant that does not work."""
    owner = _signup(api, "o9@acme.test")
    pid = _project(owner, "visible")
    other = _signup(api, "x9@acme.test")
    assert other.get(f"{V1}/projects").json() == []

    owner.post(f"{V1}/projects/{pid}/members", json={"email": "x9@acme.test", "role": "viewer"})
    listing = other.get(f"{V1}/projects").json()
    assert [p["id"] for p in listing] == [pid]
    assert listing[0]["role"] == "viewer"
    assert listing[0]["owner"] is False


def test_granting_to_an_unknown_account_is_explicit(api):
    """The caller is an admin naming someone they intend to work with; hiding a
    typo behind a silent success helps nobody."""
    owner = _signup(api, "o10@acme.test")
    pid = _project(owner)
    r = owner.post(f"{V1}/projects/{pid}/members",
                   json={"email": "nobody@acme.test", "role": "viewer"})
    assert r.status_code == 404


def test_members_are_listed_with_the_owner_first(api):
    owner = _signup(api, "o11@acme.test")
    pid = _project(owner)
    _signup(api, "x11@acme.test")
    owner.post(f"{V1}/projects/{pid}/members", json={"email": "x11@acme.test", "role": "approver"})
    members = owner.get(f"{V1}/projects/{pid}/members").json()
    assert members[0]["owner"] is True
    assert members[0]["role"] == "admin"
    assert {m["role"] for m in members} == {"admin", "approver"}


def test_single_tenant_mode_is_unchanged(tmp_path, monkeypatch):
    """With IACTRANSLATE_AUTH unset the sole operator administers everything —
    the historical behaviour, which roles must not disturb."""
    monkeypatch.delenv("IACTRANSLATE_AUTH", raising=False)
    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    from iactranslate.api import main as api_main
    importlib.reload(api_main)
    try:
        client = TestClient(api_main.app)
        pid = _project(client)
        assert client.get(f"{V1}/projects/{pid}").status_code == 200
        assert client.delete(f"{V1}/projects/{pid}").status_code == 204
    finally:
        monkeypatch.undo()
        importlib.reload(api_main)


# --- durability --------------------------------------------------------------

def test_grants_survive_a_restart(tmp_path, monkeypatch):
    """A deployment with IACTRANSLATE_STORE=sqlite kept its projects across a
    restart and lost who may see them: every grant silently revoked, noticed
    only when someone reports losing access. Half-durable authorization is worse
    than none."""
    from iactranslate.api.roles import SqliteMembership

    db = str(tmp_path / "grants.db")
    first = SqliteMembership(db)
    first.grant("proj-1", "user-2", Role.EDITOR)
    first.grant("proj-1", "user-3", Role.VIEWER)

    second = SqliteMembership(db)          # a fresh process, same file
    assert second.role_for("proj-1", "user-2", "owner") is Role.EDITOR
    assert second.role_for("proj-1", "user-3", "owner") is Role.VIEWER


def test_a_revocation_survives_a_restart(tmp_path):
    """The dangerous direction: access that comes *back* after a restart."""
    from iactranslate.api.roles import SqliteMembership

    db = str(tmp_path / "grants.db")
    first = SqliteMembership(db)
    first.grant("proj-1", "user-2", Role.ADMIN)
    assert first.revoke("proj-1", "user-2")

    assert SqliteMembership(db).role_for("proj-1", "user-2", "owner") is None


def test_a_role_change_survives_a_restart(tmp_path):
    from iactranslate.api.roles import SqliteMembership

    db = str(tmp_path / "grants.db")
    first = SqliteMembership(db)
    first.grant("proj-1", "user-2", Role.ADMIN)
    first.grant("proj-1", "user-2", Role.VIEWER)   # demote
    assert SqliteMembership(db).role_for("proj-1", "user-2", "owner") is Role.VIEWER


def test_deleting_a_project_removes_its_grants_from_disk(tmp_path):
    """Otherwise a recycled id inherits access from a project that is gone."""
    from iactranslate.api.roles import SqliteMembership

    db = str(tmp_path / "grants.db")
    first = SqliteMembership(db)
    first.grant("proj-1", "user-2", Role.EDITOR)
    first.drop_project("proj-1")
    assert SqliteMembership(db).role_for("proj-1", "user-2", "owner") is None


def test_an_unreadable_role_costs_one_grant_not_the_deployment(tmp_path):
    """A row written by a newer version should not crash every request."""
    import sqlite3

    from iactranslate.api.roles import SqliteMembership

    db = str(tmp_path / "grants.db")
    first = SqliteMembership(db)
    first.grant("proj-1", "user-2", Role.EDITOR)
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute(
        "INSERT INTO project_members VALUES ('proj-1','user-9','archdruid',0)"
    )
    conn.close()

    reloaded = SqliteMembership(db)
    assert reloaded.role_for("proj-1", "user-2", "owner") is Role.EDITOR
    assert reloaded.role_for("proj-1", "user-9", "owner") is None


def test_membership_follows_the_store_setting(monkeypatch, tmp_path):
    """One switch, so a deployment cannot persist some state and lose the rest."""
    from iactranslate.api.roles import Membership, SqliteMembership, create_membership

    monkeypatch.setenv("IACTRANSLATE_STORE", "memory")
    assert type(create_membership()) is Membership

    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "m.db"))
    assert isinstance(create_membership(), SqliteMembership)
