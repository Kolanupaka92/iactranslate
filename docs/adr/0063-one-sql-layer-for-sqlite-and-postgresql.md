# 0063. One SQL layer for SQLite and PostgreSQL

**Status:** Accepted
**Date:** 2026-08-30

## Context

Five components hold durable state — the project store, accounts, project
roles, the audit trail and the job queue — and all five were written directly
against `sqlite3`.

SQLite was the right first choice ([ADR 0025](0025-persistent-store-and-bearer-auth.md),
[0053](0053-durable-job-queue.md)): standard library, no service to run, fully
testable offline, and it removed data loss on restart for a single node. The
roadmap deliberately deferred PostgreSQL until "a deployment needs it", noting
the seams made it mechanical.

A deployment needs it. Hosting the API on Cloud Run makes SQLite unusable, and
not for a reason configuration can fix: **Cloud Storage FUSE provides no file
locking**, which SQLite requires, and Google documents SQLite on it as
experimental and unsuitable for production. The same applies to any deployment
with more than one replica — each would get its own private database while
appearing to work.

## Decision

**One implementation per component, not one per engine.**

The alternative was five PostgreSQL classes beside the five SQLite ones. That is
the hand-maintained duplication the architecture review filed as AR-01, and it
drifts in the way that hurts most: a column added to one and forgotten in the
other is invisible until a customer hits it. So `api/sql.py` reconciles the
dialects and every store is written once against it.

**How little actually differs.** The SQL here is simple, and the engines agree
on most of it — including `ON CONFLICT ... DO UPDATE`, which needed no branch at
all. Four things differ:

| | SQLite | PostgreSQL |
|---|---|---|
| Placeholders | `?` | `%s` |
| Real numbers | `REAL` | `DOUBLE PRECISION` |
| Auto ids | `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGSERIAL PRIMARY KEY` |
| Introspection | `PRAGMA table_info` | `information_schema.columns` |

Statements are written with `?` and translated on the way out, so the stores read
as they always did. Schemas name types as `{REAL}` and `{SERIAL_PK}`.

**Claiming a job is the one genuine behavioural difference**, so it lives in
`Database.claim_sql` rather than being spelled around. SQLite has no
`SKIP LOCKED`; `BEGIN IMMEDIATE` takes the write lock before the read so two
workers cannot select the same row. PostgreSQL has the primitive built in and it
is strictly better — a worker skips rows another worker holds instead of queueing
behind them, so claims stop serialising as workers are added.

**Not an ORM.** Queries stay hand-written and visible. SQLAlchemy would pull a
large dependency into a project that deliberately runs on the standard library,
to solve a problem measured at four differences.

**PostgreSQL fails loudly.** A bad DSN raises rather than falling back to a local
SQLite file. Silent fallback would give every replica its own private database
and look like it worked — data loss that surfaces as "my project disappeared"
much later.

## Consequences

`IACTRANSLATE_STORE=postgres` with `IACTRANSLATE_DATABASE_URL` runs every durable
component on PostgreSQL. SQLite remains the default and is still correct for a
single node; `memory` is still the default of the defaults.

Two problems surfaced that neither the type checker nor the SQLite suite could
have caught, and both were only visible against a real server:

**Constraint violations raise unrelated exception classes.** `except
sqlite3.IntegrityError` would have let a duplicate-email violation escape as a
500 rather than the "that address is taken" it is.

**Two locks stopped meaning anything.** Issuing and redeeming a password-reset
token were multi-statement operations guarded by an in-process lock. That covers
threads, not processes, so with two replicas two concurrent redemptions of the
same link could both read the row before either deleted it — defeating single
use, which is a security property. Both are transactions now.

Verified in CI against a real `postgres:16` service, mirroring how Redis is
already handled — including four threads racing for twenty jobs, asserting each
is claimed exactly once. None of this is claimed to work on the strength of the
SQLite suite passing.
