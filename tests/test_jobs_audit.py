"""Async job queue, event bus, and audit trail (the v2.1 runtime seam)."""
import time

from fastapi.testclient import TestClient

from iactranslate.api.events import Event, EventBus, EventType
from iactranslate.api.jobs import JobQueue, JobStatus
from iactranslate.api.main import app


def _poll_job(client, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


def _upload(client, target="aws", policy=None):
    body = {"name": "job", "target": target}
    if policy:
        body["policy"] = policy
    pid = client.post("/projects", json=body).json()["id"]
    with open("tests/fixtures/rvtools_sample.xlsx", "rb") as f:
        client.post(f"/projects/{pid}/upload", files={"file": ("rvtools_sample.xlsx", f)})
    return pid


# --- unit: event bus + job queue --------------------------------------------

def test_event_bus_fanout_and_isolation():
    bus = EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(e.type))
    bus.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))  # bad subscriber
    bus.publish(Event(EventType.PROJECT_CREATED, project_id="p1"))
    assert seen == [EventType.PROJECT_CREATED]  # bad subscriber didn't break the good one


def test_job_queue_runs_and_completes():
    bus = EventBus()
    events = []
    bus.subscribe(lambda e: events.append(e.type))
    q = JobQueue(bus, max_workers=1)
    done = []
    job = q.submit("p1", lambda: done.append(True))
    deadline = time.time() + 5
    while time.time() < deadline and q.get(job.id).status != JobStatus.COMPLETED:
        time.sleep(0.02)
    assert q.get(job.id).status == JobStatus.COMPLETED
    assert done == [True]
    assert EventType.JOB_QUEUED in events and EventType.JOB_COMPLETED in events


def test_job_queue_captures_failure():
    bus = EventBus()
    q = JobQueue(bus, max_workers=1)

    def boom():
        raise ValueError("kaboom")

    job = q.submit("p1", boom)
    deadline = time.time() + 5
    while time.time() < deadline and q.get(job.id).status not in (JobStatus.FAILED, JobStatus.COMPLETED):
        time.sleep(0.02)
    j = q.get(job.id)
    assert j.status == JobStatus.FAILED and "kaboom" in j.error


# --- API: async run + audit -------------------------------------------------

def test_async_job_end_to_end():
    client = TestClient(app)
    pid = _upload(client)
    r = client.post(f"/projects/{pid}/jobs")
    assert r.status_code == 202
    job_id = r.json()["id"]
    body = _poll_job(client, job_id)
    assert body["status"] == "completed"
    assert body["project"]["result"]["vm_count"] == 7
    # The generated project is downloadable.
    assert client.get(f"/projects/{pid}/download").status_code == 200


def test_async_job_policy_denial_fails_job():
    client = TestClient(app)
    pid = _upload(client, policy={"max_vcpu": {"max": 8}})
    job_id = client.post(f"/projects/{pid}/jobs").json()["id"]
    body = _poll_job(client, job_id)
    assert body["status"] == "failed"
    assert body["error"]
    assert body["project"]["status"] == "failed"


def test_audit_records_lifecycle():
    client = TestClient(app)
    pid = _upload(client)
    job_id = client.post(f"/projects/{pid}/jobs").json()["id"]
    _poll_job(client, job_id)
    events = client.get(f"/audit?project_id={pid}").json()
    actions = {e["action"] for e in events}
    assert "project.created" in actions
    assert "project.uploaded" in actions
    assert "job.queued" in actions
    assert "job.completed" in actions
    # Newest-first ordering.
    assert events == sorted(events, key=lambda e: e["timestamp"], reverse=True)
