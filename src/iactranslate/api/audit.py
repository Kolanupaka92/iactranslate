"""Audit trail — an append-only record of every consequential action.

Subscribes to the event bus and records who-did-what-when: project created,
uploaded, job started/completed/failed, project deleted.

Two implementations behind one interface, selected by `IACTRANSLATE_STORE`
(the same switch that selects the project store — one decision, not two):

- `AuditLog` (default) — a bounded in-memory ring. Fast, zero-setup, and lost
  on restart.
- `SqliteAuditLog` — appended to a local SQLite file, so the trail survives a
  restart. Regulated shops need history that outlives a process; an in-memory
  ring cannot provide that no matter how large the ring is.

Neither is a substitute for shipping audit to an external, tamper-evident log
(Postgres with append-only grants, or a SIEM). SQLite here is a real, testable
step past "gone on restart" — not a compliance claim.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
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


class SqliteAuditLog:
    """Same interface as `AuditLog`, appended to a local SQLite file.

    Append-only by construction: this class issues no UPDATE or DELETE except
    the capacity trim, which drops the *oldest* rows only.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            action TEXT NOT NULL,
            project_id TEXT,
            job_id TEXT,
            detail TEXT NOT NULL
        )
    """

    def __init__(self, db_path: str, capacity: int = 5000) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(self._SCHEMA)
        self._conn.execute("CREATE INDEX IF NOT EXISTS audit_project ON audit (project_id)")
        self._conn.commit()

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(self._on_event)

    def _on_event(self, event: Event) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit (timestamp, action, project_id, job_id, detail) VALUES (?, ?, ?, ?, ?)",
                (event.timestamp, event.type.value, event.project_id, event.job_id,
                 json.dumps(event.detail)),
            )
            self._conn.commit()
            self._trim_locked()

    def recent(self, project_id: Optional[str] = None, limit: int = 100) -> List[AuditEvent]:
        sql = "SELECT timestamp, action, project_id, job_id, detail FROM audit"
        params: tuple = ()
        if project_id is not None:
            sql += " WHERE project_id = ?"
            params = (project_id,)
        sql += " ORDER BY id DESC LIMIT ?"  # newest first, matching AuditLog
        params += (limit,)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            AuditEvent(timestamp=r[0], action=r[1], project_id=r[2], job_id=r[3],
                       detail=json.loads(r[4]))
            for r in rows
        ]

    def _trim_locked(self) -> None:
        """Drop the oldest rows beyond capacity. Caller must hold the lock."""
        count = self._conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        overflow = count - self._capacity
        if overflow <= 0:
            return
        self._conn.execute(
            "DELETE FROM audit WHERE id IN (SELECT id FROM audit ORDER BY id ASC LIMIT ?)",
            (overflow,),
        )
        self._conn.commit()


def create_audit_log(capacity: int = 5000) -> "AuditLog | SqliteAuditLog":
    """Resolve the configured audit log: `IACTRANSLATE_STORE` (default `memory`).

    `sqlite` appends to `IACTRANSLATE_DB_PATH` (default `./iactranslate.db`) —
    the same file the project store uses, in its own table.
    """
    backend = os.getenv("IACTRANSLATE_STORE", "memory").strip().lower()
    if backend == "sqlite":
        db_path = os.getenv("IACTRANSLATE_DB_PATH", "./iactranslate.db")
        return SqliteAuditLog(db_path, capacity=capacity)
    return AuditLog(capacity=capacity)
