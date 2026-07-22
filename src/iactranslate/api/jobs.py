"""Asynchronous job queue — the async, restart-survivable* API shape.

`POST …/jobs` returns a `job_id` immediately; the pipeline runs on a worker and
the client polls `GET /jobs/{id}`. At single-node the worker pool is a
ThreadPoolExecutor and jobs live in memory; the *interface* is what matters —
a production deployment swaps this for Redis + Celery/Dramatiq workers backed by
Postgres without changing the API contract.

(* Restart-survivability comes with the persistent backend; this in-memory queue
is the seam, not the durable store — see docs/deployment.md.)
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Optional

from .events import Event, EventBus, EventType


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    project_id: str
    status: JobStatus = JobStatus.QUEUED
    created_at: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": (
                round((self.finished_at - self.started_at) * 1000, 1)
                if self.started_at and self.finished_at else None
            ),
            "error": self.error,
        }


class JobQueue:
    def __init__(self, bus: EventBus, max_workers: int = 2, capacity: int = 500) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="iac-worker")
        self._jobs: Dict[str, Job] = {}
        self._order: list = []
        self._capacity = capacity
        self._lock = threading.Lock()
        self._bus = bus

    def submit(self, project_id: str, work: Callable[[], None]) -> Job:
        """Enqueue `work` to run on a worker; returns the queued Job immediately."""
        job = Job(id=uuid.uuid4().hex[:12], project_id=project_id, created_at=time.time())
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._evict_locked()
        self._bus.publish(Event(EventType.JOB_QUEUED, project_id=project_id, job_id=job.id))
        self._executor.submit(self._run, job, work)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: Job, work: Callable[[], None]) -> None:
        job.status = JobStatus.RUNNING
        job.started_at = time.time()
        self._bus.publish(Event(EventType.JOB_STARTED, project_id=job.project_id, job_id=job.id))
        try:
            work()
            job.status = JobStatus.COMPLETED
            self._bus.publish(Event(EventType.JOB_COMPLETED, project_id=job.project_id, job_id=job.id))
        except Exception as exc:  # noqa: BLE001 — any failure marks the job failed
            job.status = JobStatus.FAILED
            job.error = str(exc)
            self._bus.publish(Event(
                EventType.JOB_FAILED, project_id=job.project_id, job_id=job.id,
                detail={"error": str(exc)},
            ))
        finally:
            job.finished_at = time.time()

    def _evict_locked(self) -> None:
        while len(self._order) > self._capacity:
            oldest = self._order.pop(0)
            self._jobs.pop(oldest, None)
