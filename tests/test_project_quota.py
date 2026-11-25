"""Nobody's projects are deleted to make room for someone else's (ADR 0083).

The store used to keep the newest N projects on the whole deployment and delete
the rest, files included. With open registration, one account creating N
projects erased every other customer's.
"""
import importlib

import pytest
from fastapi.testclient import TestClient

from iactranslate.api import oidc
from iactranslate.api.store import ProjectStore, SqlProjectStore

PASSWORD = "correct-horse-battery"


@pytest.fixture(params=["memory", "sql"])
def store(request, tmp_path, monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(tmp_path / "ws"))
    if request.param == "memory":
        return ProjectStore(max_projects=3)
    return SqlProjectStore(str(tmp_path / "q.db"), max_projects=3)


def test_one_account_cannot_evict_another(store):
    kept = [store.create(f"customer-{i}", owner_id="customer") for i in range(2)]
    for i in range(10):
        store.create(f"noise-{i}", owner_id="stranger")
    assert {p.id for p in store.list_for_owner("customer")} == {p.id for p in kept}
    assert all(p.workspace.exists() for p in kept)
    assert store.count_for_owner("stranger") == 10


def test_unowned_projects_do_not_evict_owned_ones(store):
    kept = store.create("customer", owner_id="customer")
    for i in range(6):
        store.create(f"single-tenant-{i}")
    assert store.get(kept.id) is not None and kept.workspace.exists()
    assert len(store.list_for_owner(None)) == 3  # the cap still bounds single-tenant mode


def test_an_account_at_its_limit_is_refused_and_keeps_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("IACTRANSLATE_COOKIE_SECURE", "0")
    monkeypatch.setenv("IACTRANSLATE_RATE_AUTH", "0")
    monkeypatch.setenv("IACTRANSLATE_MAX_PROJECTS_PER_ACCOUNT", "2")
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    import iactranslate.api.main as main
    importlib.reload(main)
    try:
        client = TestClient(main.app)
        client.post("/auth/register", json={"email": "pat@example.com", "password": PASSWORD})
        ids = [client.post("/projects", json={"name": f"p{i}", "target": "aws"}).json()["id"] for i in range(2)]
        r = client.post("/projects", json={"name": "p3", "target": "aws"})
        assert r.status_code == 409 and "2 projects" in r.json()["detail"]
        assert {p["id"] for p in client.get("/projects").json()} == set(ids)
        assert client.delete(f"/projects/{ids[0]}").status_code in (200, 204)
        assert client.post("/projects", json={"name": "p3", "target": "aws"}).status_code == 201
        other = TestClient(main.app)  # the limit is each account's own
        other.post("/auth/register", json={"email": "sam@example.com", "password": PASSWORD})
        assert other.post("/projects", json={"name": "x", "target": "aws"}).status_code == 201
    finally:
        monkeypatch.undo()
        importlib.reload(main)


def _config(issuer):
    return oidc.OidcConfig(issuer=issuer, client_id="c", client_secret="s", redirect_uri="https://x/cb")


def test_the_provider_name_on_the_button(monkeypatch):
    monkeypatch.delenv("IACTRANSLATE_OIDC_NAME", raising=False)
    assert oidc.provider_name(None) is None
    assert oidc.provider_name(_config("https://accounts.google.com")) == "Google"
    assert oidc.provider_name(_config("https://accounts.google.com.evil.test")) is None
    monkeypatch.setenv("IACTRANSLATE_OIDC_NAME", "Acme ID")
    assert oidc.provider_name(_config("https://idp.acme.test")) == "Acme ID"
