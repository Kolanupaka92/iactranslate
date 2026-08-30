"""One SQL layer over SQLite and PostgreSQL.

The durable components — project store, accounts, roles, audit and the job queue
— were written directly against `sqlite3`. SQLite was the right first choice
(ADR 0025/0053): it is in the standard library, needs no service, and removed
data loss on restart for a single node. It stops being the right choice the
moment the API runs on more than one instance, or on a platform whose filesystem
is not a real block device — Cloud Run's storage provides no POSIX locking, which
SQLite requires, and Google documents SQLite on it as unsuitable for production.

The alternative to this module is five parallel PostgreSQL implementations beside
the five SQLite ones. That is the hand-maintained-duplication shape the
architecture review flagged (AR-01), and it would drift: a column added to one
and forgotten in the other is invisible until a customer hits it.

So each store is written **once**, against this layer.

**How little actually differs.** The SQL here is simple — create, insert, select,
update, delete, a handful of indexes. PostgreSQL and SQLite agree on most of it,
including `ON CONFLICT ... DO UPDATE`. Four things do not:

* *Placeholders.* SQLite binds `?`, psycopg binds `%s`. Statements are written
  with `?` throughout and translated on the way out, so the stores read the way
  they always did.
* *Types.* `REAL` and SQLite's `INTEGER PRIMARY KEY AUTOINCREMENT` become
  `DOUBLE PRECISION` and `BIGSERIAL PRIMARY KEY`. Schemas name these as
  `{REAL}` and `{SERIAL_PK}` rather than hardcoding either.
* *Introspection.* `PRAGMA table_info` versus `information_schema.columns`,
  needed by the additive migrations.
* *Claiming a row.* See `Database.claim_sql` — the one place where the two
  engines want genuinely different SQL rather than different spelling.

**Not an ORM.** The queries stay visible and hand-written; this only removes the
dialect differences. Adding SQLAlchemy would pull a large dependency into a
project that deliberately runs on the standard library.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Sequence, Tuple

#: Selects the engine. `sqlite` (default) keeps the single-node behaviour;
#: `postgres` reads `IACTRANSLATE_DATABASE_URL`.
ENV_BACKEND = "IACTRANSLATE_STORE"
ENV_DSN = "IACTRANSLATE_DATABASE_URL"

_PLACEHOLDER = re.compile(r"\?")
#: A table name may only ever be a plain SQL identifier. Table names cannot be
#: bound as parameters, so the one statement that interpolates one validates it
#: instead of trusting the caller.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DatabaseUnavailable(RuntimeError):
    """PostgreSQL was selected but cannot be used."""


class Database:
    """A connection to one of the two supported engines.

    Deliberately thin: `execute` for writes, `query` for reads, `transaction`
    for the few places that need several statements to be atomic. Every store
    goes through it, so adding a third engine later is one class, not five.
    """

    def __init__(self, backend: str, dsn: str) -> None:
        self.backend = backend
        self.is_postgres = backend == "postgres"
        self._dsn = dsn
        self._lock = threading.Lock()
        if self.is_postgres:
            self._pool = _postgres_pool(dsn)
            self._conn = None
        else:
            # `check_same_thread=False` because FastAPI runs sync handlers in a
            # threadpool; the lock below serialises access, as it always did.
            self._pool = None
            self._conn = sqlite3.connect(dsn, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")

    # -- dialect ---------------------------------------------------------------

    @property
    def REAL(self) -> str:
        return "DOUBLE PRECISION" if self.is_postgres else "REAL"

    @property
    def SERIAL_PK(self) -> str:
        return "BIGSERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"

    def schema(self, ddl: str) -> str:
        """Fill the type placeholders in a schema string."""
        return ddl.format(REAL=self.REAL, SERIAL_PK=self.SERIAL_PK)

    def sql(self, statement: str) -> str:
        """`?` placeholders to whatever the driver binds."""
        return _PLACEHOLDER.sub("%s", statement) if self.is_postgres else statement

    # -- the one genuine behavioural difference --------------------------------

    def claim_sql(self, table: str) -> str:
        """SQL that hands exactly one queued row to exactly one worker.

        The engines want different mechanisms rather than different spelling.
        SQLite has no `SKIP LOCKED`; `BEGIN IMMEDIATE` takes the write lock
        before the read so two workers cannot select the same row (ADR 0053).
        PostgreSQL has the primitive built in, and it is strictly better — it
        skips contended rows instead of serialising every claim.
        """
        if not _IDENTIFIER.match(table):
            raise ValueError(f"not a valid table name: {table!r}")
        # `table` is now known to be a bare identifier, and the only value that
        # varies is bound as a parameter. A table name cannot be passed as a
        # bind parameter in either engine, so validating it is the available
        # control. (nosec: B608 flags the f-string shape, not a reachable
        # injection.)
        ordering = "ORDER BY created_at LIMIT 1"
        if self.is_postgres:
            ordering += " FOR UPDATE SKIP LOCKED"
        return f"SELECT id FROM {table} WHERE status = 'queued' AND visible_at <= ? {ordering}"  # nosec B608

    # -- operations ------------------------------------------------------------

    @contextmanager
    def _cursor(self, commit: bool) -> Iterator[Any]:
        if self.is_postgres:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    yield cur
                    # psycopg commits on clean exit of the connection block.
            return
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                if commit:
                    self._conn.commit()
            finally:
                cur.close()

    def execute(self, statement: str, params: Sequence[Any] = ()) -> int:
        """Run a write. Returns the number of rows affected.

        Callers use the count to answer "did anything actually change?" — how
        many sessions a sign-out revoked, for instance — so it is part of the
        interface rather than an incidental cursor attribute.
        """
        with self._cursor(commit=True) as cur:
            cur.execute(self.sql(statement), tuple(params))
            return cur.rowcount if cur.rowcount is not None else 0

    def query(self, statement: str, params: Sequence[Any] = ()) -> List[Tuple]:
        with self._cursor(commit=False) as cur:
            cur.execute(self.sql(statement), tuple(params))
            return list(cur.fetchall())

    def query_one(self, statement: str, params: Sequence[Any] = ()) -> Optional[Tuple]:
        rows = self.query(statement, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """Several statements, atomically.

        On SQLite this takes the write lock up front (`BEGIN IMMEDIATE`), which
        is what makes the job-queue claim safe without `SKIP LOCKED`.
        """
        if self.is_postgres:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    yield _Tx(self, cur)
            return
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                tx = _Tx(self, cur)
                yield tx
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    @property
    def integrity_errors(self) -> tuple:
        """Exceptions meaning "a constraint rejected this".

        The drivers raise unrelated classes — `sqlite3.IntegrityError` versus
        psycopg's `IntegrityError` — so a store catching only the SQLite one
        would let a duplicate-email violation escape as a 500 instead of the
        "that address is taken" it is.
        """
        if self.is_postgres:
            import psycopg

            return (psycopg.IntegrityError,)
        return (sqlite3.IntegrityError,)

    def columns(self, table: str) -> set:
        """Existing column names, for the additive migrations."""
        if self.is_postgres:
            rows = self.query(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                (table,),
            )
            return {r[0] for r in rows}
        with self._lock:
            return {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}

    def close(self) -> None:
        if self.is_postgres:
            self._pool.close()
        else:
            self._conn.close()


class _Tx:
    """Cursor handle handed to a `transaction()` body."""

    def __init__(self, db: Database, cursor: Any) -> None:
        self._db = db
        self._cur = cursor

    def execute(self, statement: str, params: Sequence[Any] = ()) -> None:
        self._cur.execute(self._db.sql(statement), tuple(params))

    def query(self, statement: str, params: Sequence[Any] = ()) -> List[Tuple]:
        self._cur.execute(self._db.sql(statement), tuple(params))
        return list(self._cur.fetchall())

    def query_one(self, statement: str, params: Sequence[Any] = ()) -> Optional[Tuple]:
        rows = self.query(statement, params)
        return rows[0] if rows else None


def _postgres_pool(dsn: str):
    try:
        from psycopg_pool import ConnectionPool
    except ImportError as exc:  # pragma: no cover - exercised by the message test
        raise DatabaseUnavailable(
            f"{ENV_BACKEND}=postgres needs psycopg. Install the extra: "
            "pip install 'iactranslate[postgres]'"
        ) from exc
    return ConnectionPool(dsn, min_size=1, max_size=10, open=True)


def backend() -> str:
    return (os.getenv(ENV_BACKEND, "memory") or "memory").strip().lower()


def create_database(default_sqlite_path: str = "./iactranslate.db") -> Database:
    """Resolve the configured engine.

    PostgreSQL fails loudly with no fallback. Silently dropping to a local
    SQLite file when a DSN is wrong would give every replica its own private
    database and look like it worked — data loss that surfaces as "my project
    disappeared" much later.
    """
    kind = backend()
    if kind == "postgres":
        dsn = (os.getenv(ENV_DSN) or "").strip()
        if not dsn:
            raise DatabaseUnavailable(
                f"{ENV_BACKEND}=postgres requires {ENV_DSN} "
                "(e.g. postgresql://user:pass@host:5432/iactranslate)"
            )
        return Database("postgres", dsn)
    path = os.getenv("IACTRANSLATE_DB_PATH", default_sqlite_path)
    from pathlib import Path

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return Database("sqlite", path)
