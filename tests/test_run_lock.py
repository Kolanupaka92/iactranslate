"""One run per project at a time.

Five concurrent POST /run on one project used to run five pipelines over the
same workspace. State stayed consistent, because every run writes the same
deterministic output — but it was five times the CPU for one result, and a
hostile caller got that multiplier for free.

The tests that matter are the ones about *release*: a lock that is not let go
on every exit turns one crash into a project nobody can run again.
"""
import importlib
import threading
import time

import pytest
from fastapi.testclient import TestClient

from iactranslate.api import store as store_mod


@pytest.fixture(params=["memory", "sqlite"])
def api(request, tmp_path, monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("IACTRANSLATE_STORE", request.param)
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "t.db"))
    from iactranslate.api import main as api_main
    importlib.reload(api_main)
    yield TestClient(api_main.app), api_main
    monkeypatch.undo()
    importlib.reload(api_main)


def _ready_project(client):
    pid = client.post("/v1/projects", json={"name": "lock"}).json()["id"]
    with open("tests/fixtures/rvtools_sample.xlsx", "rb") as f:
        client.post(f"/v1/projects/{pid}/upload", files={"file": ("i.xlsx", f)})
    return pid


# --- the claim ----------------------------------------------------------------

def test_a_second_run_while_one_is_in_flight_is_a_409(api):
    client, m = api
    pid = _ready_project(client)
    assert m.store.try_begin_run(pid)          # simulate a run holding the lock
    r = client.post(f"/v1/projects/{pid}/run")
    assert r.status_code == 409
    assert "already in progress" in r.text
    m.store.end_run(pid)


def test_concurrent_runs_yield_exactly_one_pipeline(api):
    """The property, not the status code: N racers, one winner."""
    client, m = api
    pid = _ready_project(client)
    statuses = []
    barrier = threading.Barrier(5)

    def race():
        barrier.wait()
        statuses.append(client.post(f"/v1/projects/{pid}/run").status_code)

    threads = [threading.Thread(target=race) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(statuses) == [200, 409, 409, 409, 409], statuses
    assert m.store.get(pid).status == "completed"


# --- the release --------------------------------------------------------------

def test_the_lock_is_released_after_a_successful_run(api):
    client, m = api
    pid = _ready_project(client)
    assert client.post(f"/v1/projects/{pid}/run").status_code == 200
    assert client.post(f"/v1/projects/{pid}/run").status_code == 200, "second sequential run must work"


def test_the_lock_is_released_after_a_failed_run(api):
    """A project whose run failed must be runnable again once the file is
    fixed. Left locked, the customer's only remedy is to wait for the lease."""
    client, m = api
    pid = client.post("/v1/projects", json={"name": "bad"}).json()["id"]
    client.post(f"/v1/projects/{pid}/upload", files={"file": ("i.csv", b"who,what\na,b\n")})
    assert client.post(f"/v1/projects/{pid}/run").status_code == 400
    assert m.store.get(pid).status == "failed"
    assert m.store.get(pid).run_started_at is None
    assert m.store.try_begin_run(pid), "must be claimable again"
    m.store.end_run(pid)


def test_the_lock_is_released_on_an_unanticipated_exception(api, monkeypatch):
    """The case that turns one crash into a permanently stuck project."""
    client, m = api
    pid = _ready_project(client)
    monkeypatch.setattr(m, "run_pipeline", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    # TestClient re-raises server exceptions by default; we want the response
    # the API actually sends, which is what a real caller sees.
    quiet = TestClient(m.app, raise_server_exceptions=False)
    quiet.cookies = client.cookies
    r = quiet.post(f"/v1/projects/{pid}/run")
    assert r.status_code == 500
    project = m.store.get(pid)
    assert project.status == "failed", "must not be left reading 'running'"
    assert "unexpectedly" in (project.error or "")
    assert project.run_started_at is None


def test_a_stale_claim_from_a_dead_instance_can_be_taken_over(api, monkeypatch):
    """An instance that crashed mid-run never calls end_run. The lease is what
    keeps that from locking the project forever."""
    client, m = api
    pid = _ready_project(client)
    assert m.store.try_begin_run(pid)
    assert not m.store.try_begin_run(pid)
    # Age the claim past the lease.
    monkeypatch.setattr(store_mod, "RUN_LEASE_SECONDS", 0.0)
    time.sleep(0.01)
    assert m.store.try_begin_run(pid), "a claim older than the lease must be takeable"
    m.store.end_run(pid)


def test_the_async_path_reports_the_conflict_not_a_crash(api):
    """The job queue turns exceptions into failed jobs; the message must say
    what happened rather than exposing a project id."""
    client, m = api
    pid = _ready_project(client)
    assert m.store.try_begin_run(pid)
    with pytest.raises(m.RunInProgress) as e:
        m._execute_run(m.store.get(pid))
    assert "already in progress" in str(e.value)
    m.store.end_run(pid)
