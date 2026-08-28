"""A job queue that survives a restart, retries, and gives up honestly.

The in-memory queue loses every queued and running job when the process dies —
a deploy, a crash, an OOM kill — and the caller polls `GET /jobs/{id}` for work
they were told was accepted. These tests mostly exercise the failure paths,
since a queue is only worth persisting if it behaves under failure.
"""
import importlib
import time

import pytest

from iactranslate.api.events import EventBus
from iactranslate.api.jobs import JobQueue
from iactranslate.api.jobs_sqlite import SqliteJobQueue, create_job_queue


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "jobs.db")


def _queue(db, **kw):
    q = SqliteJobQueue(EventBus(), db, poll_interval=0.01, **kw)
    return q


def _wait(q, job_id, *states, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = q.get(job_id)
        if job and job.status.value in states:
            return job
        time.sleep(0.01)
    return q.get(job_id)


# --- the point of the exercise ----------------------------------------------

def test_a_job_survives_the_process_that_accepted_it(db):
    """The bug this fixes: restart and the work is simply gone."""
    first = _queue(db, max_workers=1)          # workers never started
    job = first.submit("proj-1")
    assert job.status.value == "queued"

    second = _queue(db, max_workers=1)          # a fresh process, same file
    assert second.get(job.id) is not None, "the job should have survived"

    ran = []
    second.register("run", lambda pid: ran.append(pid))
    second.start()
    assert _wait(second, job.id, "completed").status.value == "completed"
    assert ran == ["proj-1"]
    second.stop()


def test_workers_do_not_start_before_handlers_are_registered(db):
    """Found by testing restart: a fresh process claimed a surviving job, saw no
    handler, and dead-lettered it — losing exactly what durability protects."""
    q = _queue(db, max_workers=2)
    job = q.submit("proj-1")
    time.sleep(0.05)
    assert q.get(job.id).status.value == "queued", "nothing should consume it yet"
    q.stop()


def test_a_running_job_from_a_dead_worker_is_reclaimed(db):
    """Otherwise it stays `running` for ever — nothing is left to finish it."""
    q = _queue(db, max_workers=0)
    job = q.submit("proj-1")
    # Simulate a worker that claimed the job and then died: running, lease past.
    q._conn.execute(
        "UPDATE jobs SET status='running', lease_until=? WHERE id=?",
        (time.time() - 1, job.id),
    )
    assert q.get(job.id).status.value == "running"

    revived = _queue(db, max_workers=1)   # startup reclaims
    assert revived.get(job.id).status.value == "queued"
    revived.stop()


def test_a_live_lease_is_not_stolen(db):
    """Reclaiming everything `running` would double-run legitimate work."""
    q = _queue(db, max_workers=0)
    job = q.submit("proj-1")
    q._conn.execute(
        "UPDATE jobs SET status='running', lease_until=? WHERE id=?",
        (time.time() + 600, job.id),
    )
    other = _queue(db, max_workers=0)
    assert other.get(job.id).status.value == "running", "still leased, leave it alone"


# --- retry and dead-lettering ------------------------------------------------

def test_a_transient_failure_is_retried(db, monkeypatch):
    import iactranslate.api.jobs_sqlite as mod
    monkeypatch.setattr(mod, "BACKOFF_BASE_SECONDS", 0.01)  # keep the test quick

    calls = {"n": 0}

    def flaky(pid):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")

    q = _queue(db, max_workers=1)
    q.register("run", flaky)
    q.start()
    job = q.submit("proj-1")
    assert _wait(q, job.id, "completed").status.value == "completed"
    assert calls["n"] == 2, "should have failed once then succeeded"
    q.stop()


def test_a_poison_job_dead_letters_instead_of_churning(db, monkeypatch):
    """A job that fails because its input is malformed fails identically for
    ever; retrying it without limit hides the problem and burns CPU."""
    import iactranslate.api.jobs_sqlite as mod
    monkeypatch.setattr(mod, "BACKOFF_BASE_SECONDS", 0.01)

    attempts = {"n": 0}

    def always_fails(pid):
        attempts["n"] += 1
        raise ValueError("malformed inventory")

    q = _queue(db, max_workers=1, max_attempts=3)
    q.register("run", always_fails)
    q.start()
    job = q.submit("proj-1")
    assert _wait(q, job.id, "failed").status.value == "failed"
    assert attempts["n"] == 3, "exactly max_attempts, then stop"

    dead = q.dead_letters()
    assert [d.id for d in dead] == [job.id]
    assert "malformed inventory" in (dead[0].error or "")
    q.stop()


def test_the_last_error_is_kept(db, monkeypatch):
    import iactranslate.api.jobs_sqlite as mod
    monkeypatch.setattr(mod, "BACKOFF_BASE_SECONDS", 0.01)
    q = _queue(db, max_workers=1, max_attempts=2)
    q.register("run", lambda pid: (_ for _ in ()).throw(RuntimeError("boom-42")))
    q.start()
    job = q.submit("proj-1")
    assert "boom-42" in (_wait(q, job.id, "failed").error or "")
    q.stop()


def test_an_unknown_kind_is_retried_then_dead_lettered(db, monkeypatch):
    """Not instantly fatal: 'not registered yet' and 'never will be' look the
    same at that moment, and guessing fatally discards real work."""
    import iactranslate.api.jobs_sqlite as mod
    monkeypatch.setattr(mod, "BACKOFF_BASE_SECONDS", 0.01)
    q = _queue(db, max_workers=1, max_attempts=2)
    q.register("run", lambda pid: None)
    q.start()
    job = q.submit("proj-1", kind="nonexistent")
    result = _wait(q, job.id, "failed")
    assert result.status.value == "failed"
    assert "no handler registered" in (result.error or "")
    q.stop()


# --- concurrency -------------------------------------------------------------

def test_two_workers_never_run_the_same_job_twice(db):
    """The claim must be atomic, or a translation runs twice and the artifacts
    of one overwrite the other."""
    seen = []
    q = _queue(db, max_workers=4)
    q.register("run", lambda pid: seen.append(pid))
    q.start()
    ids = [q.submit(f"proj-{i}").id for i in range(25)]
    for job_id in ids:
        _wait(q, job_id, "completed", "failed")
    q.stop()
    assert sorted(seen) == sorted(f"proj-{i}" for i in range(25)), "each exactly once"


def test_in_flight_counts_queued_and_running(db):
    q = _queue(db, max_workers=0)
    for i in range(3):
        q.submit(f"proj-{i}")
    assert q.in_flight() == 3


# --- selection ---------------------------------------------------------------

def test_the_queue_follows_the_store_setting(monkeypatch, tmp_path):
    """Persisting projects while losing their jobs is a half-durable deployment
    nobody asked for, so both follow one switch."""
    monkeypatch.setenv("IACTRANSLATE_STORE", "memory")
    assert isinstance(create_job_queue(EventBus()), JobQueue)

    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "q.db"))
    durable = create_job_queue(EventBus())
    assert isinstance(durable, SqliteJobQueue)
    durable.stop()


def test_both_queues_expose_the_same_surface():
    """Callers must not have to branch on which implementation they got."""
    for name in ("submit", "get", "in_flight", "register", "start"):
        assert hasattr(JobQueue, name), f"JobQueue.{name}"
        assert hasattr(SqliteJobQueue, name), f"SqliteJobQueue.{name}"


def test_the_api_still_runs_jobs_end_to_end(tmp_path, monkeypatch):
    """The durable queue wired into the app, not just exercised directly."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(tmp_path))
    from iactranslate.api import main as api_main
    importlib.reload(api_main)
    try:
        client = TestClient(api_main.app)
        pid = client.post("/v1/projects", json={"name": "durable", "target": "aws"}).json()["id"]
        with open("tests/fixtures/rvtools_sample.xlsx", "rb") as f:
            client.post(f"/v1/projects/{pid}/upload",
                        files={"file": ("i.xlsx", f, "application/vnd.openxmlformats-"
                                                     "officedocument.spreadsheetml.sheet")})
        job_id = client.post(f"/v1/projects/{pid}/jobs").json()["id"]

        deadline = time.time() + 30
        while time.time() < deadline:
            state = client.get(f"/v1/jobs/{job_id}").json()["status"]
            if state in ("completed", "failed"):
                break
            time.sleep(0.05)
        assert state == "completed", client.get(f"/v1/jobs/{job_id}").json()
    finally:
        monkeypatch.undo()
        importlib.reload(api_main)
