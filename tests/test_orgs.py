"""Organisations, membership and invitations (ADR 0080).

Most tests here are about who can *not* do something: an outsider seeing an
org project, a squatter accepting someone's invitation, an admin promoting
themselves, the last owner walking away.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from iactranslate.api import delivery
from iactranslate.api.orgs import OrgError, OrgStore, validate_org_name
from iactranslate.api.roles import Role, SqlMembership
from iactranslate.api.sql import Database

PASSWORD = "correct-horse-battery"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "orgs.db"))
    monkeypatch.setenv("IACTRANSLATE_COOKIE_SECURE", "0")
    monkeypatch.setenv("IACTRANSLATE_RATE_AUTH", "0")
    monkeypatch.setenv("IACTRANSLATE_RATE_WRITE", "0")
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    import iactranslate.api.main as main
    importlib.reload(main)
    yield main
    monkeypatch.undo()
    importlib.reload(main)


@pytest.fixture()
def mail():
    sent = {"verify": {}, "invite": []}
    delivery.set_verification_delivery(lambda e, url: sent["verify"].__setitem__(e, url))
    delivery.set_invitation_delivery(lambda e, url, **ctx: sent["invite"].append((e, url, ctx)))
    yield sent
    delivery.set_verification_delivery(None)
    delivery.set_invitation_delivery(None)


def _token(url: str) -> str:
    return url.split("token=", 1)[1]


def _person(api, mail, address, verified=True) -> TestClient:
    client = TestClient(api.app)
    assert client.post("/auth/register", json={"email": address, "password": PASSWORD}).status_code == 201
    if verified:
        assert client.post("/auth/verify-email",
                           json={"token": _token(mail["verify"][address])}).status_code == 204
    return client


def _uid(client) -> str:
    return client.get("/auth/me").json()["id"]


def _org(client, name="Acme Migration") -> str:
    r = client.post("/orgs", json={"name": name})
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "owner"
    return r.json()["id"]


def _invite_and_join(owner, member, mail, org_id, address, role="member"):
    r = owner.post(f"/orgs/{org_id}/invitations", json={"email": address, "role": role})
    assert r.status_code == 201, r.text
    _, url, _ = mail["invite"][-1]
    r = member.post("/invitations/accept", json={"token": _token(url)})
    assert r.status_code == 200, r.text
    return r.json()


def _project(client, org_id=None) -> str:
    body = {"name": "estate", "target": "aws"}
    if org_id:
        body["org_id"] = org_id
    r = client.post("/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- creating and joining --------------------------------------------------------------


def test_creating_an_org_makes_you_its_owner(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    assert alice.get("/orgs").json() == [{"id": oid, "name": "Acme Migration",
                                          "created_at": pytest.approx(alice.get("/orgs").json()[0]["created_at"]),
                                          "role": "owner"}]


def test_an_invitation_link_joins_the_org(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    r = alice.post(f"/orgs/{oid}/invitations", json={"email": "Bob@Example.com"})
    assert r.status_code == 201 and r.json()["email"] == "bob@example.com"
    address, url, ctx = mail["invite"][0]
    assert address == "bob@example.com" and "/invite?token=" in url
    assert ctx == {"org": "Acme Migration", "inviter": "alice@example.com", "role": "member"}

    bob = _person(api, mail, "bob@example.com", verified=False)
    assert bob.post("/invitations/accept", json={"token": _token(url)}).json()["role"] == "member"
    # The link came to the inbox, so accepting it proves the address too.
    assert bob.get("/auth/me").json()["email_verified"] is True
    members = {m["email"]: m["role"] for m in alice.get(f"/orgs/{oid}").json()["members"]}
    assert members == {"alice@example.com": "owner", "bob@example.com": "member"}


def test_an_invitation_only_works_for_its_address_and_only_once(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    alice.post(f"/orgs/{oid}/invitations", json={"email": "bob@example.com"})
    token = _token(mail["invite"][0][1])
    mallory = _person(api, mail, "mallory@example.com")
    r = mallory.post("/invitations/accept", json={"token": token})
    assert r.status_code == 403 and "bob@example.com" in r.json()["detail"]
    bob = _person(api, mail, "bob@example.com")
    assert bob.post("/invitations/accept", json={"token": token}).status_code == 200
    assert bob.post("/invitations/accept", json={"token": token}).status_code == 400


def test_a_squatter_cannot_see_or_accept_invitations_in_the_app(api, mail):
    """Whoever registered bob@ before Bob did, without proving the address,
    neither learns about Bob's invitations nor accepts them."""
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    squatter = _person(api, mail, "bob@example.com", verified=False)
    inv_id = alice.post(f"/orgs/{oid}/invitations", json={"email": "bob@example.com"}).json()["id"]
    assert squatter.get("/invitations").json() == []
    r = squatter.post(f"/invitations/{inv_id}/accept")
    assert r.status_code == 403 and "confirm your email" in r.json()["detail"]
    assert squatter.get(f"/orgs/{oid}").status_code == 404


def test_a_verified_invitee_accepts_or_declines_in_the_app(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    other = _org(alice, "Other Org")
    bob = _person(api, mail, "bob@example.com")
    alice.post(f"/orgs/{oid}/invitations", json={"email": "bob@example.com"})
    alice.post(f"/orgs/{other}/invitations", json={"email": "bob@example.com"})
    pending = {i["org_name"]: i["id"] for i in bob.get("/invitations").json()}
    assert set(pending) == {"Acme Migration", "Other Org"}
    assert bob.post(f"/invitations/{pending['Acme Migration']}/accept").status_code == 200
    assert bob.post(f"/invitations/{pending['Other Org']}/decline").status_code == 204
    assert [o["name"] for o in bob.get("/orgs").json()] == ["Acme Migration"]
    assert alice.get(f"/orgs/{other}/invitations").json() == []


def test_inviting_rules(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    carol = _person(api, mail, "carol@example.com")
    _invite_and_join(alice, carol, mail, oid, "carol@example.com", role="admin")
    # An admin can't invite an owner, and members can't invite at all.
    assert carol.post(f"/orgs/{oid}/invitations",
                      json={"email": "x@example.com", "role": "owner"}).status_code == 403
    dave = _person(api, mail, "dave@example.com")
    _invite_and_join(alice, dave, mail, oid, "dave@example.com")
    assert dave.post(f"/orgs/{oid}/invitations", json={"email": "y@example.com"}).status_code == 403
    assert alice.post(f"/orgs/{oid}/invitations", json={"email": "carol@example.com"}).status_code == 409
    assert alice.post(f"/orgs/{oid}/invitations",
                      json={"email": "z@example.com", "role": "boss"}).status_code == 400


def test_a_withdrawn_invitation_cannot_be_accepted(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    inv = alice.post(f"/orgs/{oid}/invitations", json={"email": "bob@example.com"}).json()
    assert alice.delete(f"/orgs/{oid}/invitations/{inv['id']}").status_code == 204
    bob = _person(api, mail, "bob@example.com")
    assert bob.post("/invitations/accept", json={"token": _token(mail["invite"][0][1])}).status_code == 400


# --- project access ------------------------------------------------------------------


def test_org_projects_follow_org_roles(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    bob = _person(api, mail, "bob@example.com")
    _invite_and_join(alice, bob, mail, oid, "bob@example.com")
    pid = _project(alice, oid)

    listed = {p["id"]: p for p in bob.get("/projects").json()}
    assert listed[pid]["role"] == "editor" and listed[pid]["org_id"] == oid
    assert bob.get(f"/projects/{pid}").status_code == 200
    assert bob.delete(f"/projects/{pid}").status_code == 403  # deleting needs admin

    outsider = _person(api, mail, "eve@example.com")
    assert outsider.get(f"/projects/{pid}").status_code == 404
    assert pid not in {p["id"] for p in outsider.get("/projects").json()}

    # Members create projects in the org too, and the owner sees them.
    bobs = _project(bob, oid)
    assert {p["id"]: p["role"] for p in alice.get("/projects").json()}[bobs] == "admin"


def test_a_project_grant_can_raise_but_not_lower_org_access(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    bob = _person(api, mail, "bob@example.com")
    _invite_and_join(alice, bob, mail, oid, "bob@example.com")
    pid = _project(alice, oid)
    alice.post(f"/projects/{pid}/members", json={"email": "bob@example.com", "role": "approver"})
    assert {p["id"]: p["role"] for p in bob.get("/projects").json()}[pid] == "approver"
    alice.post(f"/projects/{pid}/members", json={"email": "bob@example.com", "role": "viewer"})
    assert {p["id"]: p["role"] for p in bob.get("/projects").json()}[pid] == "editor"


def test_removing_a_member_ends_access_immediately(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    bob = _person(api, mail, "bob@example.com")
    _invite_and_join(alice, bob, mail, oid, "bob@example.com")
    pid = _project(alice, oid)
    assert alice.delete(f"/orgs/{oid}/members/{_uid(bob)}").status_code == 200
    assert bob.get(f"/projects/{pid}").status_code == 404
    assert bob.get(f"/orgs/{oid}").status_code == 404


def test_creating_a_project_in_someone_elses_org_is_not_found(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    eve = _person(api, mail, "eve@example.com")
    r = eve.post("/projects", json={"name": "x", "target": "aws", "org_id": oid})
    assert r.status_code == 404


def test_moving_a_project_in_and_out(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    bob = _person(api, mail, "bob@example.com")
    _invite_and_join(alice, bob, mail, oid, "bob@example.com", role="admin")
    pid = _project(alice)
    assert bob.get(f"/projects/{pid}").status_code == 404
    assert alice.put(f"/projects/{pid}/org", json={"org_id": oid}).json()["org_id"] == oid
    assert bob.get(f"/projects/{pid}").status_code == 200
    # An org admin who isn't the project's owner can't take it out.
    assert bob.put(f"/projects/{pid}/org", json={"org_id": None}).status_code == 403
    assert alice.put(f"/projects/{pid}/org", json={"org_id": None}).status_code == 200
    assert bob.get(f"/projects/{pid}").status_code == 404
    eve = _person(api, mail, "eve@example.com")
    eve_org = _org(eve, "Eve Org")
    assert alice.put(f"/projects/{pid}/org", json={"org_id": eve_org}).status_code == 404


# --- roles and owners --------------------------------------------------------------


def test_only_owners_make_or_unmake_owners(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    carol = _person(api, mail, "carol@example.com")
    _invite_and_join(alice, carol, mail, oid, "carol@example.com", role="admin")
    r = carol.patch(f"/orgs/{oid}/members/{_uid(carol)}", json={"role": "owner"})
    assert r.status_code == 403
    assert carol.patch(f"/orgs/{oid}/members/{_uid(alice)}", json={"role": "member"}).status_code == 403
    assert carol.delete(f"/orgs/{oid}/members/{_uid(alice)}").status_code == 403
    assert alice.patch(f"/orgs/{oid}/members/{_uid(carol)}", json={"role": "owner"}).status_code == 200


def test_the_last_owner_cannot_leave_or_step_down(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    me = _uid(alice)
    r = alice.patch(f"/orgs/{oid}/members/{me}", json={"role": "admin"})
    assert r.status_code == 400 and "at least one owner" in r.json()["detail"]
    assert alice.delete(f"/orgs/{oid}/members/{me}").status_code == 400
    bob = _person(api, mail, "bob@example.com")
    _invite_and_join(alice, bob, mail, oid, "bob@example.com", role="owner")
    assert alice.delete(f"/orgs/{oid}/members/{me}").status_code == 200  # now she can leave


def test_deleting_an_org_returns_projects_to_their_owners(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    bob = _person(api, mail, "bob@example.com")
    _invite_and_join(alice, bob, mail, oid, "bob@example.com", role="admin")
    alices, bobs = _project(alice, oid), _project(bob, oid)
    assert bob.delete(f"/orgs/{oid}").status_code == 403  # owners only
    assert alice.delete(f"/orgs/{oid}").status_code == 204
    assert alice.get(f"/projects/{alices}").json()["org_id"] is None
    assert bob.get(f"/projects/{bobs}").json()["org_id"] is None
    assert bob.get(f"/projects/{alices}").status_code == 404
    assert alice.get(f"/projects/{bobs}").status_code == 404


def test_account_deletion_waits_for_an_ownership_handover(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    bob = _person(api, mail, "bob@example.com")
    _invite_and_join(alice, bob, mail, oid, "bob@example.com")
    r = alice.request("DELETE", "/auth/me", json={"current_password": PASSWORD})
    assert r.status_code == 409 and "Acme Migration" in r.json()["detail"]
    alice.patch(f"/orgs/{oid}/members/{_uid(bob)}", json={"role": "owner"})
    assert alice.request("DELETE", "/auth/me", json={"current_password": PASSWORD}).status_code == 204
    assert [m["email"] for m in bob.get(f"/orgs/{oid}").json()["members"]] == ["bob@example.com"]


def test_an_org_nobody_else_is_in_goes_with_its_owner(api, mail):
    alice = _person(api, mail, "alice@example.com")
    oid = _org(alice)
    assert alice.request("DELETE", "/auth/me", json={"current_password": PASSWORD}).status_code == 204
    assert api.orgs.get(oid) is None


# --- storage and validation --------------------------------------------------------


def test_project_grants_are_not_cached_per_instance(tmp_path):
    """Two API instances on one database: a grant or revocation on one is
    seen by the other at once. (This used to need a restart.)"""
    path = str(tmp_path / "shared.db")
    one, two = SqlMembership(Database("sqlite", path)), SqlMembership(Database("sqlite", path))
    one.grant("p1", "u1", Role.EDITOR)
    assert two.role_for("p1", "u1", owner_id="owner") == Role.EDITOR
    assert two.projects_for("u1") == ["p1"]
    one.revoke("p1", "u1")
    assert two.role_for("p1", "u1", owner_id="owner") is None


@pytest.mark.parametrize("name", ["", "   ", "x" * 81, "Acme\r\nBcc: victim@example.com", "tab\there"])
def test_bad_org_names_are_refused(name):
    with pytest.raises(ValueError):
        validate_org_name(name)


def test_an_expired_invitation_is_refused(tmp_path):
    store = OrgStore(Database("sqlite", str(tmp_path / "o.db")))
    org = store.create_org("Acme", "owner")
    from iactranslate.api.orgs import OrgRole
    inv, _ = store.invite(org.id, "bob@example.com", OrgRole.MEMBER, "owner", ttl_seconds=-1)
    with pytest.raises(OrgError, match="expired"):
        store.accept(inv, "bob")


def test_the_invitation_email():
    msg = delivery.invitation_message("bob@example.com", "https://app.example/invite?token=t",
                                      "no-reply@example.com", org="Acme Migration",
                                      inviter="alice@example.com", role="admin")
    assert msg["Subject"] == "You're invited to Acme Migration on IaCTranslate"
    body = msg.get_content()
    assert "alice@example.com invited bob@example.com to join Acme Migration" in body
    assert "as admin" in body and "within 7 days" in body
