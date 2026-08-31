"""Cross-site writes, when the cookie can no longer refuse them.

`SameSite=lax` blocks cross-site POSTs in the browser, so nothing here is
needed — and that is the default. A deployment whose web app and API are on
different sites has to set `SameSite=none`, which hands the browser's CSRF
protection back to us.

The endpoint that makes this worth doing properly is the upload. A custom
required header would protect the JSON routes, because a cross-site form
cannot set headers and JSON triggers a preflight — but a form *can* POST
`multipart/form-data` with no preflight at all, and that is precisely the shape
of the route carrying customer inventory.
"""
import importlib

import pytest
from fastapi.testclient import TestClient

APP_ORIGIN = "https://app.example"
EVIL = "https://attacker.example"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("IACTRANSLATE_CORS_ORIGINS", APP_ORIGIN)
    monkeypatch.setenv("IACTRANSLATE_COOKIE_SAMESITE", "none")
    # Accounts only exist in multi-tenant mode, which is the only mode where a
    # session cookie — and therefore CSRF — is in play at all.
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    from iactranslate.api import main as api_main
    importlib.reload(api_main)
    yield TestClient(api_main.app)
    monkeypatch.undo()
    importlib.reload(api_main)


@pytest.fixture
def lax_client(tmp_path, monkeypatch):
    """The default posture: the browser refuses cross-site writes itself."""
    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "l.db"))
    monkeypatch.setenv("IACTRANSLATE_CORS_ORIGINS", APP_ORIGIN)
    monkeypatch.delenv("IACTRANSLATE_COOKIE_SAMESITE", raising=False)
    from iactranslate.api import main as api_main
    importlib.reload(api_main)
    yield TestClient(api_main.app)
    monkeypatch.undo()
    importlib.reload(api_main)


# --- the default stays safe ---------------------------------------------------

def test_lax_is_the_default():
    from iactranslate.api.main import cookie_samesite

    assert cookie_samesite() == "lax"


@pytest.mark.parametrize("value", ["", "bogus", "SAMESITE"])
def test_an_unrecognised_value_falls_back_to_lax(monkeypatch, value):
    """A typo in a deployment variable must not quietly widen the cookie."""
    from iactranslate.api.main import cookie_samesite

    monkeypatch.setenv("IACTRANSLATE_COOKIE_SAMESITE", value)
    assert cookie_samesite() == "lax"


def test_none_forces_a_secure_cookie(client):
    """`SameSite=None` is only honoured on a Secure cookie; without it the
    browser drops the cookie and sign-in silently stops working."""
    r = client.post("/v1/auth/register",
                    json={"email": "a@acme.test", "password": "correct horse battery"})
    cookie = r.headers.get("set-cookie", "")
    assert "samesite=none" in cookie.lower()
    assert "secure" in cookie.lower()


# --- what the guard refuses ---------------------------------------------------

def test_a_write_from_another_origin_is_refused(client):
    r = client.post("/v1/projects", json={"name": "x"}, headers={"Origin": EVIL})
    assert r.status_code == 403
    assert "origin" in r.text.lower()


def test_a_multipart_upload_from_another_origin_is_refused(client):
    """The case a custom-header scheme would miss: a cross-site form can send
    multipart with no preflight."""
    r = client.post(
        "/v1/projects/anything/upload",
        files={"file": ("i.csv", b"VM,CPUs\nweb,2\n", "text/csv")},
        headers={"Origin": EVIL},
    )
    assert r.status_code == 403


def test_a_delete_from_another_origin_is_refused(client):
    assert client.delete("/v1/projects/x", headers={"Origin": EVIL}).status_code == 403


# --- what it must not break ---------------------------------------------------

def test_the_real_app_origin_is_allowed(client):
    """Reaching the auth layer is the pass condition. This middleware decides
    origin, not identity — a 401 here means it let the request through, which
    is exactly its job."""
    r = client.post("/v1/projects", json={"name": "ok"}, headers={"Origin": APP_ORIGIN})
    assert r.status_code != 403


def test_a_request_with_no_origin_is_allowed(client):
    """curl, CI and bearer-token integrations send no Origin, and CSRF does not
    apply to them — an attacker cannot make a browser omit the header."""
    assert client.post("/v1/projects", json={"name": "cli"}).status_code != 403


def test_reads_are_never_blocked(client):
    """GET is excluded on purpose. Anything that mutates behind a GET is a bug
    this check would only be hiding."""
    assert client.get("/v1/targets", headers={"Origin": EVIL}).status_code == 200


def test_the_guard_is_inert_under_the_default_posture(lax_client):
    """With `lax` the browser already refuses these, so the server does not
    need to second-guess a same-site deployment."""
    r = lax_client.post("/v1/projects", json={"name": "y"}, headers={"Origin": EVIL})
    assert r.status_code != 403
