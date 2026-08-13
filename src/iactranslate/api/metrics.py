"""Runtime metrics in Prometheus text-exposition format.

Metrics are collected the same way the audit trail is — by subscribing to the
event bus — so the API handlers stay free of instrumentation calls and the
pipeline stays a pure function. Adding a metric means handling an event, not
editing a route.

Deliberately stdlib-only: the Prometheus text format is a documented, stable
line protocol, and emitting it directly avoids a `prometheus_client` dependency
for what amounts to a handful of counters. The trade is real and stated: this
gives counters and a gauge scraped by Prometheus/Grafana — it is *not*
distributed tracing. OpenTelemetry spans across services remain a v2.4 item;
`pipeline-trace.json` already covers per-stage timing within one run.
"""
from __future__ import annotations

import threading
from typing import Dict

from .events import Event, EventBus, EventType

# event type -> (metric name, help text). Only events worth a counter are here;
# an unlisted event is simply not counted.
_COUNTERS: Dict[EventType, tuple] = {
    EventType.PROJECT_CREATED: ("iactranslate_projects_created_total", "Projects created."),
    EventType.PROJECT_UPLOADED: ("iactranslate_uploads_total", "Inventory files uploaded."),
    EventType.PROJECT_DELETED: ("iactranslate_projects_deleted_total", "Projects deleted."),
    EventType.JOB_QUEUED: ("iactranslate_jobs_queued_total", "Pipeline jobs enqueued."),
    EventType.JOB_STARTED: ("iactranslate_jobs_started_total", "Pipeline jobs started."),
    EventType.JOB_COMPLETED: ("iactranslate_jobs_completed_total", "Pipeline jobs completed."),
    EventType.JOB_FAILED: ("iactranslate_jobs_failed_total", "Pipeline jobs failed."),
}


class Metrics:
    """Counts lifecycle events and renders them for a Prometheus scrape."""

    def __init__(self) -> None:
        self._counts: Dict[str, int] = {name: 0 for name, _ in _COUNTERS.values()}
        self._lock = threading.Lock()

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(self._on_event)

    def _on_event(self, event: Event) -> None:
        entry = _COUNTERS.get(event.type)
        if entry is None:
            return
        with self._lock:
            self._counts[entry[0]] += 1

    def render(self, jobs_in_flight: int = 0) -> str:
        """Prometheus text exposition (one HELP/TYPE/value block per metric)."""
        with self._lock:
            counts = dict(self._counts)

        lines = []
        for name, help_text in _COUNTERS.values():
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {counts[name]}")

        lines.append("# HELP iactranslate_jobs_in_flight Jobs queued or running right now.")
        lines.append("# TYPE iactranslate_jobs_in_flight gauge")
        lines.append(f"iactranslate_jobs_in_flight {jobs_in_flight}")
        return "\n".join(lines) + "\n"
