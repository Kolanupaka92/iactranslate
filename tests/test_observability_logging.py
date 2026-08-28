"""Structured logging, correlation ids, and their propagation into workers."""
import json
import logging

import pytest
from fastapi.testclient import TestClient

from iactranslate.api.main import app
from iactranslate.observability import (
    JsonFormatter,
    configure_logging,
    correlation_id,
    get_logger,
    new_correlation_id,
    stage,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def iac_caplog(caplog):
    """Capture records from the `iactranslate` logger.

    `configure_logging()` sets `propagate = False` so that importing this
    package never duplicates or hijacks a host application's logging — which
    also means pytest's `caplog`, whose handler lives on the *root* logger,
    never sees our records. Attaching its handler to our logger keeps the
    production behaviour intact while still making the records assertable.
    """
    log = logging.getLogger("iactranslate")
    previous_level = log.level
    log.addHandler(caplog.handler)
    log.setLevel(logging.DEBUG)
    try:
        yield caplog
    finally:
        log.removeHandler(caplog.handler)
        log.setLevel(previous_level)


def _record(msg="hello", level=logging.INFO, **extra):
    return logging.LogRecord(
        name="iactranslate.test", level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    ), extra


def test_every_line_is_one_json_object():
    rec, _ = _record()
    out = json.loads(JsonFormatter().format(rec))
    assert out["message"] == "hello"
    assert out["level"] == "INFO"
    assert out["logger"] == "iactranslate.test"
    assert out["ts"].endswith("Z")


def test_extra_fields_are_merged_not_dropped():
    rec, _ = _record()
    rec.vm_count = 25
    rec.duration_ms = 12.5
    out = json.loads(JsonFormatter().format(rec))
    assert out["vm_count"] == 25
    assert out["duration_ms"] == 12.5


def test_correlation_id_is_attached_when_set():
    token = correlation_id.set("abc123")
    try:
        rec, _ = _record()
        assert json.loads(JsonFormatter().format(rec))["trace_id"] == "abc123"
    finally:
        correlation_id.reset(token)


def test_no_trace_id_key_when_unset():
    """Absent, not empty-string — a blank id in a log index is worse than none."""
    token = correlation_id.set("")
    try:
        rec, _ = _record()
        assert "trace_id" not in json.loads(JsonFormatter().format(rec))
    finally:
        correlation_id.reset(token)


def test_a_log_call_never_raises_on_unserializable_values():
    """Logging must not be able to break the request it is reporting on."""
    class Hostile:
        def __repr__(self): return "<hostile>"

    rec, _ = _record()
    rec.thing = Hostile()
    out = json.loads(JsonFormatter().format(rec))  # must not raise
    assert "hostile" in out["thing"]


def test_configure_logging_is_idempotent():
    """Both the CLI and the API app call it; twice must not double every line."""
    configure_logging()
    configure_logging()
    assert len(logging.getLogger("iactranslate").handlers) == 1


def test_configure_logging_does_not_touch_the_root_logger():
    """Importing this package must not hijack a host application's logging."""
    before = list(logging.getLogger().handlers)
    configure_logging()
    assert logging.getLogger().handlers == before
    assert logging.getLogger("iactranslate").propagate is False


def test_stage_records_duration_and_outcome(iac_caplog):
    log = get_logger("iactranslate.test.stage")
    with stage(log, "parse", vm_count=7) as fields:
        fields["rows"] = 7
    rec = iac_caplog.records[-1]
    assert rec.stage == "parse"
    assert rec.outcome == "ok"
    assert rec.vm_count == 7
    assert rec.rows == 7
    assert rec.duration_ms >= 0


def test_stage_logs_the_duration_of_a_failure_and_reraises(iac_caplog):
    """How long something ran before dying is usually the clue."""
    log = get_logger("iactranslate.test.stage")
    with pytest.raises(ValueError):
        with stage(log, "plan"):
            raise ValueError("boom")
    rec = iac_caplog.records[-1]
    assert rec.outcome == "error"
    assert rec.error_type == "ValueError"
    assert rec.duration_ms >= 0


# --- request correlation -----------------------------------------------------

def test_request_id_is_generated_and_echoed(client):
    r = client.get("/health")
    assert len(r.headers["X-Request-Id"]) == 16


def test_inbound_request_id_is_honoured(client):
    """A trace started at the load balancer stays one trace through this service."""
    r = client.get("/health", headers={"X-Request-Id": "from-the-lb-9"})
    assert r.headers["X-Request-Id"] == "from-the-lb-9"


def test_hostile_request_id_is_sanitized_and_truncated(client):
    """The header is attacker-controlled and lands in log files."""
    r = client.get("/health", headers={"X-Request-Id": 'x"\n{"level":"FAKE"} ' + "A" * 500})
    echoed = r.headers["X-Request-Id"]
    assert len(echoed) <= 64
    assert all(c.isalnum() or c in "-_" for c in echoed)
    assert "\n" not in echoed and '"' not in echoed


def test_pipeline_logs_carry_the_requests_correlation_id(client, iac_caplog):
    """The whole point: one id ties the HTTP request to the work it caused."""
    cid = "e2e-trace-42"
    r = client.post("/projects", json={"name": "obs", "target": "aws"},
                    headers={"X-Request-Id": cid})
    pid = r.json()["id"]
    with open("tests/fixtures/rvtools_realistic.xlsx", "rb") as f:
        client.post(
            f"/projects/{pid}/upload",
            files={"file": ("i.xlsx", f, "application/vnd.openxmlformats-"
                                         "officedocument.spreadsheetml.sheet")},
            headers={"X-Request-Id": cid},
        )
    client.post(f"/projects/{pid}/run", headers={"X-Request-Id": cid})

    pipeline = [r for r in iac_caplog.records if r.name == "iactranslate.pipeline"]
    assert pipeline, "the pipeline should have logged"
    done = pipeline[-1]
    assert done.vm_count == 25
    assert set(done.stages) >= {"parse", "normalize", "plan", "validate", "policy", "package"}


def test_correlation_id_reaches_worker_threads():
    """ThreadPoolExecutor does not inherit ContextVars; the queue copies context.

    Without the copy, an async translation logs under a blank trace id and the
    request that queued it can never be tied to the run that did the work.
    """
    from iactranslate.api.events import EventBus
    from iactranslate.api.jobs import JobQueue

    seen = []
    q = JobQueue(EventBus())
    token = correlation_id.set("worker-trace-7")
    try:
        job = q.submit("proj-1", lambda: seen.append(correlation_id.get()))
    finally:
        correlation_id.reset(token)

    for _ in range(200):
        if q.get(job.id).status.value in ("completed", "failed"):
            break
        import time as _t
        _t.sleep(0.01)

    assert seen == ["worker-trace-7"]


def test_new_correlation_ids_are_unique():
    assert len({new_correlation_id() for _ in range(500)}) == 500
