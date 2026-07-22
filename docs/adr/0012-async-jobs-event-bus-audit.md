# 0012 — Async jobs, an event bus, and an audit trail

**Status:** Accepted

## Context

The synchronous `POST /run` generates in the request thread. That's fine for a
demo but doesn't scale: long jobs tie up request threads, a restart loses
in-flight work, and there's no record of who did what. The roadmap's v2.1
(Postgres + object storage + a background job queue) is the fix — but building
all of that requires infrastructure (Redis, Celery, Postgres, S3) that isn't
warranted, or testable, at the current stage. We still want the *shape* now, so
the production backends drop in later without an API rewrite.

## Decision

Add a runtime orchestration layer, **at single-node, behind the interfaces the
production backends implement**:

- **Event bus** (`api/events.py`) — an in-process publish/subscribe. Lifecycle
  events (`project.created/uploaded/deleted`, `job.queued/started/completed/
  failed`) are published; a failing subscriber never breaks publishing. This is
  the event-driven seam; a broker (Redis/Kafka) replaces it without touching
  publishers/subscribers.
- **Async job queue** (`api/jobs.py`) — `POST …/jobs` returns a `job_id`
  immediately; the pipeline runs on a `ThreadPoolExecutor` worker; the client
  polls `GET /jobs/{id}`. Celery/Dramatiq workers replace the executor behind the
  same contract.
- **Audit trail** (`api/audit.py`) — subscribes to the bus and records every
  consequential action. In-memory (bounded ring) now; the same subscriber writes
  to Postgres for permanent history in production.

The **pipeline stays a pure function** — events, jobs, and audit live in the
API/runtime layer, not the translation engine (keeping the engine free of the
"god object" the reviews warned against).

## Consequences

- The API is now job-based and event-sourced *in shape*: `POST /jobs` → poll →
  download, with an audit log — the enterprise contract, today, on a laptop.
- Swapping to production is implementing three interfaces (bus → broker, queue →
  Celery+Redis, audit/store → Postgres) — no endpoint or client changes.
- **Honest limits:** in-memory jobs/audit do *not* survive a restart; that
  durability comes with the persistent backend (see
  [deployment › reference architecture](../deployment.md#reference-architecture)).
  We shipped the seam and a working single-node implementation, not the
  distributed system.
