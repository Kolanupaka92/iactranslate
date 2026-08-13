# 0026 — A persistent audit trail and Prometheus metrics, both off the event bus

**Status:** Accepted

## Context

Two operability gaps survived [ADR 0025](0025-persistent-store-and-bearer-auth.md).

The first was a self-contradiction in our own code. `api/audit.py` described
itself as "an append-only record of every consequential action" and noted that
"banks and regulated shops require this" — while being a bounded in-memory
`deque` that a process restart erases completely. An audit trail that does not
outlive the process is not an audit trail; enlarging the ring does not fix it.

The second: the service emitted no runtime metrics at all. `pipeline-trace.json`
records per-stage timing *within a single run*, which is useful for explaining
one translation but tells an operator nothing about the service — how many jobs
failed, how many are in flight, whether upload volume is climbing. External
review flagged observability as a P1, and the v2.4 roadmap line
(Prometheus/Grafana/OTel) had no shipped substrate underneath it.

## Decision

Both are built as **event-bus subscribers**, not as instrumentation sprinkled
through request handlers. The bus (ADR 0012) already fans out lifecycle events
to the audit log; metrics is simply a second subscriber. Adding a metric means
handling an event, not editing a route — and the pipeline stays a pure function
with no observability concerns compiled into it.

1. **`SqliteAuditLog`** (`api/audit.py`), selected by the *same*
   `IACTRANSLATE_STORE=sqlite` switch that selects the project store — one
   operator decision, not two — writing to its own table in the
   `IACTRANSLATE_DB_PATH` file. Append-only by construction: the class issues
   no `UPDATE` and no `DELETE` except a capacity trim that drops only the
   oldest rows.
   - **Verified with a real restart, not just a unit test**: a live `uvicorn`
     process created a project, was killed with `pkill`, and the fresh process
     returned the pre-restart event from `GET /audit` with a byte-identical
     timestamp — proof it was read back from disk rather than recreated.
   - **Honest boundary**: SQLite is a real step past "gone on restart"; it is
     *not* a tamper-evident compliance store. That needs Postgres with
     append-only grants, or shipping to a SIEM. The module docstring says so.
2. **`Metrics`** (`api/metrics.py`) exposed at `GET /metrics` in Prometheus
   text-exposition format: seven counters (projects created/deleted, uploads,
   jobs queued/started/completed/failed) and one `jobs_in_flight` gauge.
   - **Stdlib-only, deliberately.** The exposition format is a stable line
     protocol; emitting it directly avoids taking a `prometheus_client`
     dependency for eight numbers. A test asserts the output parses as valid
     exposition (every non-comment line is exactly `name value`) so the format
     claim is enforced, not assumed.
   - **Unauthenticated, like `/health`.** Prometheus scrapers do not send
     bearer tokens, and the payload is aggregate counts only — no project
     names, paths, or inventory data. This is a deliberate exception to
     ADR 0025's auth gate, justified by the payload carrying nothing sensitive.
   - **Counters are process-local and reset on restart.** This is correct
     Prometheus semantics — `rate()` handles counter resets — and is stated
     rather than papered over. Metrics deliberately did *not* get the SQLite
     treatment the audit log did: persisting counters across restarts would
     produce *worse* data by hiding the restart from Prometheus.

## Consequences

- The audit contradiction is gone: with `sqlite` selected, the trail genuinely
  outlives the process, demonstrated by a kill-and-restart rather than asserted.
- Operators get a real Grafana-scrapable signal today, and the v2.4 roadmap
  item now has substrate under it instead of being a green-field promise.
- The bus-subscriber pattern is now proven twice over. A third consumer —
  Slack/Teams notifications, a webhook emitter — attaches the same way, with no
  changes to publishers or to any route.
- Still explicitly not done, and not claimed: distributed tracing
  (OpenTelemetry spans across services), per-endpoint latency histograms, and
  tamper-evident audit storage. This ADR ships counters and durable history,
  which is a different and smaller thing than full observability.
