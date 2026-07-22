"""Audit trail — an append-only record of every consequential action.

Subscribes to the event bus and records who-did-what-when: project created,
uploaded, job started/completed/failed, project deleted. At single-node this is a
bounded in-memory ring; in production the same subscriber writes to Postgres for
permanent, queryable history (banks and regulated shops require this).
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import List, Optional

from .events import Event, EventBus


@dataclass
class AuditEvent:
    timestamp: float
    action: str
    project_id: Optional[str]
    job_id: Optional[str]
    detail: dict

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "project_id": self.project_id,
            "job_id": self.job_id,
            "detail": self.detail,
        }


class AuditLog:
    def __init__(self, capacity: int = 5000) -> None:
        self._events: deque = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(self._on_event)

    def _on_event(self, event: Event) -> None:
        with self._lock:
            self._events.append(AuditEvent(
                timestamp=event.timestamp,
                action=event.type.value,
                project_id=event.project_id,
                job_id=event.job_id,
                detail=event.detail,
            ))

    def recent(self, project_id: Optional[str] = None, limit: int = 100) -> List[AuditEvent]:
        with self._lock:
            events = list(self._events)
        if project_id is not None:
            events = [e for e in events if e.project_id == project_id]
        return events[-limit:][::-1]  # newest first
