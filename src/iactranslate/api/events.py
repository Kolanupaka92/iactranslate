"""A minimal in-process event bus — the event-driven seam.

Runtime components (jobs, audit, and later notifications/webhooks) publish and
subscribe to lifecycle events instead of calling each other directly. This is the
single-node realization of the event-driven workflow; a production deployment
swaps the in-process bus for a real broker (Redis/Kafka) without changing
publishers or subscribers.

The pipeline itself stays a pure function — events are emitted by the API/runtime
layer around it, not by the translation engine.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

logger = logging.getLogger("iactranslate.events")


class EventType(str, Enum):
    PROJECT_CREATED = "project.created"
    PROJECT_UPLOADED = "project.uploaded"
    PROJECT_DELETED = "project.deleted"
    JOB_QUEUED = "job.queued"
    JOB_STARTED = "job.started"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"


@dataclass
class Event:
    type: EventType
    project_id: Optional[str] = None
    job_id: Optional[str] = None
    detail: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


Subscriber = Callable[[Event], None]


class EventBus:
    """Fan-out publish/subscribe. A failing subscriber never breaks publishing."""

    def __init__(self) -> None:
        self._subscribers: List[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(fn)

    def publish(self, event: Event) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for fn in subscribers:
            try:
                fn(event)
            except Exception:  # noqa: BLE001 — a bad subscriber must not break others
                logger.exception("event subscriber failed for %s", event.type)
