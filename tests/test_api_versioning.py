"""`/v1` prefix, with the unversioned paths kept working and marked.

Adding a version prefix costs nothing before clients exist. Adding it after
means breaking every one of them, and the first breaking change to an
unversioned API is the expensive one.
"""
import pytest
from fastapi.testclient import TestClient

from iactranslate.api.main import API_VERSION, app, router


@pytest.fixture
def client():
    return TestClient(app)


def test_every_declared_route_is_served_under_v1():
    """Derived from the router we declare and the OpenAPI schema we publish.

    An earlier version of this test filtered `app.routes` by path prefix, which
    reads FastAPI's internal mounting behaviour — that differs across versions
    and passed on 3.9 while failing on 3.11/3.12 for reasons that had nothing to
    do with the feature. `router.routes` is our own object and `app.openapi()`
    is a public, stable contract; both say what we actually mean.
    """
    declared = {r.path for r in router.routes}
    assert declared, "routes should be declared on the router"
    schema = set(app.openapi()["paths"])
    for path in declared:
        assert f"/{API_VERSION}{path}" in schema, f"{path} is not served under /{API_VERSION}"


def test_the_legacy_mount_is_hidden_from_the_schema():
    """Generated clients and docs should describe only the versioned surface,
    while existing callers keep working."""
    declared = {r.path for r in router.routes}
    schema = set(app.openapi()["paths"])
    assert not (declared & schema), "unversioned paths must not appear in the schema"


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
