"""Persistent audit trail + Prometheus metrics (ADR 0026)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from iactranslate.api.audit import AuditLog, SqliteAuditLog, create_audit_log
from iactranslate.api.events import Event, EventBus, EventType
from iactranslate.api.main import app
from iactranslate.api.metrics import Metrics


def _bus_with(log) -> EventBus:
    bus = EventBus()
    log.attach(bus)
    return bus


def test_sqlite_audit_survives_a_simulated_restart(tmp_path):
    db = str(tmp_path / "audit.db")
    bus = _bus_with(SqliteAuditLog(db))
    bus.publish(Event(EventType.PROJECT_CREATED, project_id="p1", detail={"target": "aws"}))

    # A second instance against the same file is what a restarted process sees.
    reopened = SqliteAuditLog(db)
    events = reopened.recent()
    assert len(events) == 1
    assert events[0].action == "project.created"
    assert events[0].project_id == "p1"
    assert events[0].detail == {"target": "aws"}


def test_sqlite_audit_filters_by_project_and_orders_newest_first(tmp_path):
    log = SqliteAuditLog(str(tmp_path / "audit.db"))
    bus = _bus_with(log)
    bus.publish(Event(EventType.PROJECT_CREATED, project_id="a"))
    bus.publish(Event(EventType.PROJECT_CREATED, project_id="b"))
    bus.publish(Event(EventType.PROJECT_UPLOADED, project_id="a"))

    assert [e.action for e in log.recent()] == [
        "project.uploaded", "project.created", "project.created",
    ]
    scoped = log.recent(project_id="a")
    assert len(scoped) == 2
    assert {e.project_id for e in scoped} == {"a"}


def test_sqlite_audit_trims_oldest_beyond_capacity(tmp_path):
    log = SqliteAuditLog(str(tmp_path / "audit.db"), capacity=3)
    bus = _bus_with(log)
    for i in range(5):
        bus.publish(Event(EventType.PROJECT_CREATED, project_id=f"p{i}"))

    kept = log.recent()
    assert len(kept) == 3
    # The three newest survive; the two oldest were trimmed.
    assert [e.project_id for e in kept] == ["p4", "p3", "p2"]


def test_both_audit_implementations_agree_on_shape(tmp_path):
    memory, sqlite_log = AuditLog(), SqliteAuditLog(str(tmp_path / "audit.db"))
    for log in (memory, sqlite_log):
        bus = _bus_with(log)
        bus.publish(Event(EventType.JOB_FAILED, project_id="p", job_id="j", detail={"error": "x"}))

    m, s = memory.recent()[0].to_dict(), sqlite_log.recent()[0].to_dict()
    assert m.keys() == s.keys()
    for key in ("action", "project_id", "job_id", "detail"):
        assert m[key] == s[key]


def test_create_audit_log_selects_backend_from_env(tmp_path, monkeypatch):
    monkeypatch.delenv("IACTRANSLATE_STORE", raising=False)
    assert isinstance(create_audit_log(), AuditLog)

    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "iac.db"))
    assert isinstance(create_audit_log(), SqliteAuditLog)


def test_metrics_count_only_events_they_map(tmp_path):
    m = Metrics()
    bus = _bus_with(m)
    bus.publish(Event(EventType.PROJECT_CREATED))
    bus.publish(Event(EventType.PROJECT_CREATED))
    bus.publish(Event(EventType.JOB_FAILED))

    out = m.render()
    assert "iactranslate_projects_created_total 2" in out
    assert "iactranslate_jobs_failed_total 1" in out
    assert "iactranslate_jobs_completed_total 0" in out


def test_metrics_render_is_valid_prometheus_exposition():
    out = Metrics().render(jobs_in_flight=3)
    assert "# HELP iactranslate_projects_created_total" in out
    assert "# TYPE iactranslate_projects_created_total counter" in out
    assert "# TYPE iactranslate_jobs_in_flight gauge" in out
    assert "iactranslate_jobs_in_flight 3" in out
    assert out.endswith("\n")
    # Every non-comment line must be exactly "name value".
    for line in out.splitlines():
        if not line.startswith("#"):
            name, value = line.split()
            assert name.startswith("iactranslate_")
            assert value.isdigit()


def test_metrics_endpoint_is_open_and_reflects_activity(monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_API_KEY", "secret")
    client = TestClient(app)

    before = client.get("/metrics")
    assert before.status_code == 200  # open like /health, even with auth on
    assert "text/plain" in before.headers["content-type"]

    client.post("/projects", json={"name": "metrics-probe", "target": "aws"},
                headers={"Authorization": "Bearer secret"})

    def _count(body: str) -> int:
        line = next(ln for ln in body.splitlines()
                    if ln.startswith("iactranslate_projects_created_total "))
        return int(line.split()[1])

    assert _count(client.get("/metrics").text) == _count(before.text) + 1
