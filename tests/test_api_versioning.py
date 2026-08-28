"""`/v1` prefix, with the unversioned paths kept working and marked.

Adding a version prefix costs nothing before clients exist. Adding it after
means breaking every one of them, and the first breaking change to an
unversioned API is the expensive one.
"""
import pytest
from fastapi.testclient import TestClient

from iactranslate.api.main import API_VERSION, app


@pytest.fixture
def client():
    return TestClient(app)


def _paths():
    """Every routed path, from the app itself rather than a hand-kept list —
    a new endpoint added only to the legacy mount would otherwise go unnoticed."""
    prefixes = ("/v1/", "/projects", "/auth", "/jobs", "/audit", "/targets", "/policies")
    return {
        r.path for r in app.routes
        if getattr(r, "path", "").startswith(prefixes)
    }


def test_every_route_is_reachable_under_v1():
    versioned = {p for p in _paths() if p.startswith(f"/{API_VERSION}/")}
    legacy = {p for p in _paths() if not p.startswith(f"/{API_VERSION}/")}
    assert legacy, "the legacy mount should still exist"
    for path in legacy:
        assert f"/{API_VERSION}{path}" in versioned, f"{path} has no /v1 equivalent"


def test_v1_and_legacy_return_the_same_thing(client):
    a = client.get(f"/{API_VERSION}/targets")
    b = client.get("/targets")
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()


def test_legacy_responses_are_marked_deprecated(client):
    """A client should discover the move without reading a changelog."""
    r = client.get("/targets")
    assert r.headers["Deprecation"] == "true"
    assert "299" in r.headers["Warning"]
    assert r.headers["Link"] == f'</{API_VERSION}/targets>; rel="successor-version"'


def test_v1_responses_are_not_marked_deprecated(client):
    r = client.get(f"/{API_VERSION}/targets")
    assert "Deprecation" not in r.headers
    assert "Link" not in r.headers


@pytest.mark.parametrize("path", ["/health", "/metrics"])
def test_infrastructure_endpoints_stay_versionless(client, path):
    """Probes and scrapers point at fixed paths; versioning them would break
    every deployment's liveness check for no benefit."""
    r = client.get(path)
    assert r.status_code == 200
    assert "Deprecation" not in r.headers


def test_a_full_workflow_runs_entirely_under_v1(client):
    r = client.post(f"/{API_VERSION}/projects", json={"name": "versioned", "target": "aws"})
    assert r.status_code == 201
    pid = r.json()["id"]

    with open("tests/fixtures/rvtools_sample.xlsx", "rb") as f:
        assert client.post(
            f"/{API_VERSION}/projects/{pid}/upload",
            files={"file": ("i.xlsx", f, "application/vnd.openxmlformats-"
                                         "officedocument.spreadsheetml.sheet")},
        ).status_code == 200

    run = client.post(f"/{API_VERSION}/projects/{pid}/run")
    assert run.status_code == 200, run.text
    assert client.get(f"/{API_VERSION}/projects/{pid}").status_code == 200
    assert client.get(f"/{API_VERSION}/projects/{pid}/download").status_code == 200


def test_a_project_created_under_v1_is_visible_from_the_legacy_path(client):
    """One service, two mounts — not two namespaces."""
    pid = client.post(f"/{API_VERSION}/projects", json={"name": "shared", "target": "aws"}).json()["id"]
    assert client.get(f"/projects/{pid}").status_code == 200
