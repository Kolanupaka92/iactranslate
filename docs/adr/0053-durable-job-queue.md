# 0053. A job queue that survives a restart

**Status:** Accepted
**Date:** 2026-08-28

## Context

`JobQueue` held jobs in a dict and ran them on a `ThreadPoolExecutor`. Restart
the process — a deploy, a crash, an OOM kill — and every queued and running job
vanished. The client had been told 202 Accepted and now polls `GET /jobs/{id}`
for work that no longer exists.

There was also no retry, so a transient failure was permanent, and no
dead-letter queue, so a poisonous input was indistinguishable from a flaky one.

The architecture review put this behind Postgres and Redis, and I repeated that
— wrongly. **SQLite is in the standard library.** Durability here costs no
service, no container, and no operational burden, which is exactly the trade
`SqliteProjectStore` already makes (ADR 0025).

## Decision

`SqliteJobQueue`, selected by the same `IACTRANSLATE_STORE=sqlite` switch as the
project store — persisting projects while losing their jobs is a half-durable
deployment nobody asked for.

**Work is described as data, not as a closure.** The in-memory queue takes
`lambda: _execute_run(project)`, and a closure cannot be written to a table.
Since every job here is "translate project X", the row stores a *kind* and a
project id, and a registered handler does the work. That constraint is why this
is a separate class rather than a flag.

**Claiming avoids `UPDATE … RETURNING`**, which needs SQLite 3.35+ (2021) —
present everywhere we test, and not something to assume of every machine running
Python 3.9. `BEGIN IMMEDIATE` takes the write lock before the `SELECT`, so read
and update are one transaction; the `status='queued'` guard on the `UPDATE` is
the belt to that braces, and a losing writer sees `rowcount == 0` and looks
elsewhere.

**Leases distinguish "still working" from "never coming back."** A worker that
dies mid-job leaves its row `running` for ever. Startup reclaims rows whose lease
has lapsed. Without the lease, recovery would have to assume every running job is
abandoned, which would double-run whatever is legitimately in progress.

**Retries back off exponentially** (5s, 25s, 125s), because retrying a failure
caused by an overloaded dependency immediately makes the overload worse. After
`max_attempts` the job moves to `dead` with its last error, so a poison message
is visible rather than churning.

## Consequences

**Testing restart found a real bug.** The first version started workers in the
constructor, which races `register()`: a fresh process claimed a surviving job,
found no handler bound yet, and dead-lettered it — losing on restart exactly the
work the class exists to preserve. Workers now start on an explicit `start()`
after handlers are registered, and a missing handler is *retryable* rather than
instantly fatal, because "not registered yet" and "will never be registered" are
indistinguishable at that moment and guessing fatally discards real work. The
attempt limit bounds the other case.

`JobQueue` gained no-op `register()` and `start()` so the two are interchangeable
at the call site. Making callers branch on which implementation they received
would leak the difference through the whole API module.

`dead` is a durable-queue concept the shared `Job` shape has no member for, so it
is reported as `failed` — which is what it is to the caller — with the
distinction kept in the database for operators, alongside `dead_letters()`.

Still open: the queue is one SQLite file, so it is durable but not *distributed*
— two API replicas would contend on the same file, and cross-host deployment
still wants Redis or Postgres. Dead letters are queryable in Python but not over
HTTP, and there is no requeue-from-dead endpoint; both are small additions once
someone actually needs them. And a job whose process dies mid-run is retried from
the beginning, so handlers must be idempotent — `_execute_run` overwrites its
output directory, which makes it so, but nothing enforces that for a future
handler.
