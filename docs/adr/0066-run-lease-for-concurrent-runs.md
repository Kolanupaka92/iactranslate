# 0066. A run lease, claimed in one statement, for concurrent runs

**Status:** Accepted
**Date:** 2026-09-15

## Context

`POST /projects/{id}/run` regenerates a project in place: it clears the output
directory, runs the pipeline, writes the new plan and package. Two of those
overlapping on the same project produce a directory that is half one run and
half the other, and a `MigrationPlan` on disk that matches neither. The API
had no guard. Two browser tabs, a retried request after a slow response, or an
async job racing a synchronous run were all enough.

A process-local lock would have fixed the single-node case and silently failed
the case that matters: Cloud Run serves the API from more than one instance,
and an in-memory lock on instance A does not exist on instance B.

## Decision

**The project row is the lock.** A run begins by claiming it with a single
conditional `UPDATE`:

```sql
UPDATE projects SET status='running', run_started_at=?
 WHERE id=? AND (status != 'running'
                 OR run_started_at IS NULL
                 OR run_started_at < ?)   -- now - RUN_LEASE_SECONDS
```

One row changed means the caller holds the run; zero means someone else does,
and the endpoint answers `409` (the async job path reports the same conflict
instead of crashing). The claim and the check are one statement, so two
instances racing it cannot both win — the database serialises them, on SQLite
and PostgreSQL alike, through the one SQL layer of [ADR 0063](0063-one-sql-layer-for-sqlite-and-postgresql.md).
The in-memory store implements the same contract under its own lock so the
default configuration behaves identically.

**The claim is a lease, not a flag.** `run_started_at` makes it expire:
a project that has been `running` for longer than `RUN_LEASE_SECONDS` (900 s,
well past any run the `MAX_VMS` cap allows) may be claimed by a new run. That
is what recovers from an instance that died mid-run and never released the
lock — without it, that project would be stuck `running` until someone edited
the database by hand.

**Release is unconditional.** `end_run()` sits in a `finally`; a successful
run, a pipeline failure and an unanticipated exception all release the lease.
The failure path additionally marks the project `failed` so the status the
console shows is true.

## Consequences

- Concurrent runs of one project yield exactly one pipeline execution; the
  others get `409` with a message that says to wait. Seven tests in
  `tests/test_run_lock.py` cover the conflict, the three release paths, the
  stale-lease takeover and the async path, on both the memory and SQL stores.
- A run that legitimately takes longer than the lease could be claimed over.
  Today nothing can: the inventory cap bounds runs to well under a minute.
  Raising `MAX_VMS` substantially means revisiting `RUN_LEASE_SECONDS`, and
  the constant's comment says so.
- No new infrastructure. The alternative — a Redis lock or a Cloud Tasks
  queue — would have added a service to run and a second failure mode to
  reason about, for a guarantee the database already gives.
