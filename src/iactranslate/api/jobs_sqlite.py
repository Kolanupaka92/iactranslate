"""A job queue that survives a restart, retries, and gives up honestly.

The in-memory `JobQueue` holds jobs in a dict and runs them on a
`ThreadPoolExecutor`. Restart the process — a deploy, a crash, an OOM kill — and
every queued and running job is gone. The caller polled `GET /jobs/{id}` and
gets a 404 for work they were told was accepted. There is no retry, so a
transient failure is permanent, and no dead-letter queue, so a poisonous input
is indistinguishable from a flaky one.

This is the same trade `SqliteProjectStore` makes (ADR 0025): SQLite is in the
standard library, so durability costs no service, no container and no
operational burden, while the interface is what a Redis- or Postgres-backed
queue would implement.

**Work is described as data, not as a closure.** The in-memory queue accepts
`lambda: _execute_run(project)`, which cannot be written to a table — you cannot
serialize a closure. Since every job this product runs is "translate project X",
the row stores a *kind* and a project id, and a registered handler does the work.
That constraint is what makes durability possible at all, and it is the reason
this is a separate class rather than a flag on the old one.

Three failure modes the naive version gets wrong:

**Orphaned running jobs.** A job marked `running` when the process died stays
`running` for ever, because nothing is left to finish it. Startup reclaims rows
whose lease has expired rather than trusting that a previous worker is still
alive.

**Retry storms.** Immediate retry of a failure caused by an overloaded
dependency makes the overload worse. Attempts back off exponentially.

**Infinite retry.** A job that fails because its input is malformed will fail
identically for ever. After `max_attempts` it moves to `dead`, keeping the last
error, so a poison message is visible rather than churning in the background.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Dict, List, Optional

from ..observability import get_logger
from .events import Event, EventBus, EventType
from .jobs import Job, JobStatus
from .sql import Database, backend, create_database

logger = get_logger("iactranslate.api.jobs")

#: Attempts before a job is considered poisonous and moved to `dead`.
DEFAULT_MAX_ATTEMPTS = 3

#: Base for exponential backoff, in seconds: 5s, 25s, 125s.
BACKOFF_BASE_SECONDS = 5.0

#: How long a claimed job may run before another worker may reclaim it. Long
#: enough for a 5,000-VM translation (measured at ~12s) with wide margin; short
#: enough that a crashed worker's jobs come back the same day.
LEASE_SECONDS = 900.0


class DeadLettered(RuntimeError):
    pass


class SqlJobQueue:
    """Durable job queue. Same surface as `JobQueue`, plus retry and recovery."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'run',
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            created_at {REAL} NOT NULL,
            started_at {REAL},
            finished_at {REAL},
            visible_at {REAL} NOT NULL DEFAULT 0,
            lease_until {REAL},
            error TEXT
        )
    """

    #: Every column, in order. Replaces `sqlite3.Row`, which has no psycopg
    #: equivalent — rows become plain dicts so `row["id"]` reads the same on
    #: either engine, and a `SELECT *` cannot silently reorder them.
    _COLUMNS = (
        "id", "project_id", "kind", "status", "attempts", "max_attempts",
        "created_at", "started_at", "finished_at", "visible_at", "lease_until", "error",
    )

    @classmethod
    def _to_dict(cls, row) -> Optional[dict]:
        return dict(zip(cls._COLUMNS, row)) if row is not None else None

    @classmethod
    def _select(cls) -> str:
        return ", ".join(cls._COLUMNS)

    def __init__(
        self,
        bus: EventBus,
        db: "Database | str",
        max_workers: int = 2,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        poll_interval: float = 0.05,
    ) -> None:
        self._bus = bus
        self._max_attempts = max_attempts
        self._poll = poll_interval
        self._handlers: Dict[str, Callable[[str], None]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

        # A path string opens SQLite, which is how this was always constructed.
        self._db = db if isinstance(db, Database) else Database("sqlite", db)
        self._db.execute(self._db.schema(self._SCHEMA))
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS jobs_claimable ON jobs (status, visible_at)"
        )

        reclaimed = self.reclaim_expired()
        if reclaimed:
            logger.warning(
                "reclaimed %d job(s) left running by a previous process",
                reclaimed, extra={"reclaimed": reclaimed},
            )

        # Workers are NOT started here. Starting them in the constructor races
        # `register()`: a fresh process would claim a durable job, find no
        # handler bound yet, and dead-letter it — losing on restart exactly the
        # work this class exists to preserve. Caller calls `start()` once
        # handlers are in place.
        self._max_workers = max_workers
        self._workers: List[threading.Thread] = []

    def start(self) -> None:
        """Begin consuming. Idempotent, and safe to call after `register()`."""
        if self._workers:
            return
        self._workers = [
            threading.Thread(target=self._worker_loop, name=f"iac-worker-{i}", daemon=True)
            for i in range(self._max_workers)
        ]
        for worker in self._workers:
            worker.start()

    # -- registration ---------------------------------------------------------

    def register(self, kind: str, handler: Callable[[str], None]) -> None:
        """Bind a job kind to the function that performs it.

        Handlers live in the process, not the database: the row records *what*
        to do, the code records *how*. A job written by an older version whose
        kind no longer has a handler fails loudly rather than being silently
        dropped.
        """
        self._handlers[kind] = handler

    # -- submission -----------------------------------------------------------

    def submit(self, project_id: str, work: Optional[Callable] = None, kind: str = "run") -> Job:
        """Enqueue work for `project_id`.

        `work` is accepted and ignored so this is a drop-in for the in-memory
        queue's signature; a closure cannot be persisted, which is the whole
        reason this class describes work as data. Passing one is not an error —
        the caller simply gets the registered handler instead.
        """
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        self._db.execute(
            "INSERT INTO jobs (id, project_id, kind, status, max_attempts, created_at, visible_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (job_id, project_id, kind, JobStatus.QUEUED.value, self._max_attempts, now, now),
        )
        self._bus.publish(Event(EventType.JOB_QUEUED, project_id=project_id, job_id=job_id))
        return self._to_job(self._row(job_id))

    # -- reads ----------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Job]:
        row = self._row(job_id)
        return self._to_job(row) if row else None

    def in_flight(self) -> int:
        row = self._db.query_one(
            "SELECT COUNT(*) FROM jobs WHERE status IN (?,?)",
            (JobStatus.QUEUED.value, JobStatus.RUNNING.value),
        )
        return row[0] if row else 0

    def dead_letters(self, limit: int = 100) -> List[Job]:
        """Jobs that exhausted their attempts. Visible, not silently discarded."""
        rows = self._db.query(
            f"SELECT {self._select()} FROM jobs WHERE status='dead' "  # nosec B608
            "ORDER BY finished_at DESC LIMIT ?",
            (limit,),
        )
        return [self._to_job(self._to_dict(r)) for r in rows]

    # -- recovery -------------------------------------------------------------

    def reclaim_expired(self) -> int:
        """Return jobs whose lease lapsed to the queue.

        A worker that died mid-job leaves the row `running` for ever. The lease
        is what distinguishes "still working" from "never coming back" — without
        it, recovery would have to assume every running job is abandoned, which
        would double-run whatever is legitimately in progress.
        """
        return self._db.execute(
            "UPDATE jobs SET status=?, lease_until=NULL, visible_at=?"
            " WHERE status=? AND (lease_until IS NULL OR lease_until < ?)",
            (JobStatus.QUEUED.value, time.time(), JobStatus.RUNNING.value, time.time()),
        )

    # -- worker ---------------------------------------------------------------

    def _claim(self) -> Optional[dict]:
        """Atomically take one due job. None when there is nothing to do.

        The one place the two engines want genuinely different SQL rather than
        different spelling, so the statement comes from `Database.claim_sql`.

        On SQLite, `BEGIN IMMEDIATE` takes the write lock before the `SELECT`,
        so the read and the update are one transaction. Without it two workers —
        or two *processes*, which an in-process lock does not cover — could
        select the same row and both believe they claimed it. It deliberately
        avoids `UPDATE ... RETURNING`, which needs SQLite 3.35+ and is not
        something to assume of every machine that can run Python 3.9.

        On PostgreSQL, `FOR UPDATE SKIP LOCKED` does the same job better: a
        worker skips rows another worker is already holding instead of waiting
        behind them, so claims stop serialising as workers are added.

        The `status='queued'` guard on the `UPDATE` is the belt to those braces.
        If another writer got there first, the affected count is 0 and this
        worker looks for different work.
        """
        now = time.time()
        with self._db.transaction() as tx:
            row = tx.query_one(self._db.claim_sql("jobs"), (now,))
            if row is None:
                return None
            job_id = row[0]
            tx.execute(
                "UPDATE jobs SET status=?, attempts=attempts+1, started_at=?, lease_until=?"
                " WHERE id=? AND status=?",
                (JobStatus.RUNNING.value, now, now + LEASE_SECONDS,
                 job_id, JobStatus.QUEUED.value),
            )
            claimed = tx.query_one(
                f"SELECT {self._select()} FROM jobs WHERE id=?", (job_id,)  # nosec B608
            )
        return self._to_dict(claimed)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                row = self._claim()
            except Exception:  # noqa: BLE001 — a worker must not die on a db blip
                logger.exception("job claim failed")
                self._stop.wait(self._poll)
                continue
            if row is None:
                self._stop.wait(self._poll)
                continue
            self._run(row)

    def _run(self, row: dict) -> None:
        job_id, project_id, kind = row["id"], row["project_id"], row["kind"]
        self._bus.publish(Event(EventType.JOB_STARTED, project_id=project_id, job_id=job_id))
        handler = self._handlers.get(kind)
        if handler is None:
            # Retryable rather than immediately fatal. "Not registered yet"
            # (a worker racing startup) and "will never be registered" (an
            # unknown kind) are indistinguishable at this moment, and guessing
            # wrong in the fatal direction discards real work. The attempt limit
            # bounds the other case: a genuinely unknown kind still dead-letters,
            # just after a few tries instead of instantly.
            self._on_failure(row, RuntimeError(f"no handler registered for job kind '{kind}'"))
            return
        try:
            handler(project_id)
        except Exception as exc:  # noqa: BLE001 — any failure is the job's failure
            self._on_failure(row, exc)
            return
        self._db.execute(
            "UPDATE jobs SET status=?, finished_at=?, lease_until=NULL WHERE id=?",
            (JobStatus.COMPLETED.value, time.time(), job_id),
        )
        self._bus.publish(Event(EventType.JOB_COMPLETED, project_id=project_id, job_id=job_id))

    def _on_failure(self, row: dict, exc: Exception) -> None:
        job_id, project_id = row["id"], row["project_id"]
        attempts, max_attempts = row["attempts"], row["max_attempts"]
        if attempts >= max_attempts:
            self._finish_dead(job_id, project_id, str(exc))
            return
        # Exponential backoff: retrying a failure caused by an overloaded
        # dependency immediately makes the overload worse.
        delay = BACKOFF_BASE_SECONDS ** attempts
        self._db.execute(
            "UPDATE jobs SET status=?, visible_at=?, lease_until=NULL, error=? WHERE id=?",
            (JobStatus.QUEUED.value, time.time() + delay, str(exc), job_id),
        )
        logger.warning(
            "job failed, retrying", extra={"job_id": job_id, "attempt": attempts,
                                           "retry_in_s": delay, "error_type": type(exc).__name__},
        )

    def _finish_dead(self, job_id: str, project_id: str, error: str) -> None:
        self._db.execute(
            "UPDATE jobs SET status='dead', finished_at=?, lease_until=NULL, error=? WHERE id=?",
            (time.time(), error, job_id),
        )
        logger.error("job dead-lettered", extra={"job_id": job_id, "error": error})
        self._bus.publish(Event(
            EventType.JOB_FAILED, project_id=project_id, job_id=job_id, detail={"error": error},
        ))

    # -- plumbing -------------------------------------------------------------

    def _row(self, job_id: str) -> Optional[dict]:
        return self._to_dict(
            self._db.query_one(
                f"SELECT {self._select()} FROM jobs WHERE id=?", (job_id,)  # nosec B608
            )
        )

    @staticmethod
    def _to_job(row) -> Job:
        # `dead` is a durable-queue concept the shared `Job` shape has no member
        # for; it is reported as `failed`, which is what it is from the caller's
        # point of view, with the distinction kept in the database for operators.
        status = row["status"]
        job = Job(
            id=row["id"],
            project_id=row["project_id"],
            status=JobStatus.FAILED if status == "dead" else JobStatus(status),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
        )
        return job

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        for worker in self._workers:
            worker.join(timeout=timeout)


#: The engine-specific name used before the two were unified.
SqliteJobQueue = SqlJobQueue


def create_job_queue(bus: EventBus, max_workers: int = 2):
    """The configured queue. `sqlite` or `postgres` gets the durable one.

    Tied to the same switch as the project store rather than a separate flag:
    persisting projects while losing their jobs on restart is a half-durable
    deployment nobody asked for.
    """
    from .jobs import JobQueue

    if backend() in {"sqlite", "postgres"}:
        # Not started here — the caller registers handlers, then calls `start()`.
        return SqlJobQueue(bus, create_database(), max_workers=max_workers)
    return JobQueue(bus, max_workers=max_workers)
