# 0040. Structured logging and request correlation

**Status:** Accepted
**Date:** 2026-08-27

## Context

An adversarial architecture review scored observability **2/10** — the lowest of
fourteen categories. The specifics:

* Four modules imported `logging`; the rest of 12,050 lines emitted nothing.
* No handler was ever configured, so in most deployments the records that *did*
  exist went nowhere.
* Messages were human prose (`"project %s generated %d instances"`), unparseable
  by any log aggregator.
* Nothing connected an HTTP request to the pipeline run it triggered.

`/metrics` could say *how many* translations failed and the audit trail could say
*who* asked, but neither could answer **"what happened to this one run"** — the
only question that matters during an incident. A customer reporting "the plan for
our estate came out wrong last Tuesday" was unanswerable.

(The review also flagged 69 `print()` calls. On inspection all 69 are in
`cli.py`, where printing to stdout is correct. That part of the finding was
wrong and is recorded here so it is not re-litigated.)

## Decision

**JSON logs, one object per line**, with field names taken from OpenTelemetry
semantic conventions (`trace_id`, `http.method`, `http.status_code`,
`duration_ms`).

**No OpenTelemetry SDK dependency.** Adding one to a tool whose selling point is
that it runs offline with no API key is a poor trade. Borrowing the field names
keeps a later collector integration a config change rather than a rewrite, while
the wire format stays stdlib `logging`, which every aggregator already ingests.

**A correlation id in a `ContextVar`**, set once by middleware and read by every
record beneath it — rather than threading a request id through twenty function
signatures. An inbound `X-Request-Id` is honoured so a trace started at the load
balancer stays one trace, and is echoed on the response so a user can quote it.
The header is attacker-controlled input that lands in log files, so it is
filtered to `[A-Za-z0-9_-]` and truncated to 64 characters.

**`configure_logging()` is scoped to the `iactranslate` logger with
`propagate = False`**, and is called by the API app module — never on package
import. Importing this project as a library must not reconfigure the host
application's logging.

## Consequences

Two real defects surfaced while building this, both now fixed:

**Context did not reach worker threads.** `ThreadPoolExecutor` does not inherit
`ContextVars`, so every asynchronous translation would have logged under a blank
trace id — the request that queued the job could never be tied to the run that
did the work, in exactly the code path where that link is hardest to reconstruct
by hand. `JobQueue.submit` now copies the context explicitly.

**A failing stage recorded no timing.** The pipeline's `stage()` helper appended
its timing after the `yield` with no `try`/`finally`, so when a stage raised, the
trace stopped silently at the last stage that *succeeded* and said nothing about
the one that broke. Now the timing is recorded and logged on both paths.

`propagate = False` means pytest's `caplog`, whose handler sits on the root
logger, does not see our records. Rather than weaken the production behaviour,
the test suite has an `iac_caplog` fixture that attaches caplog's handler to our
logger for the duration of a test.

Still open: no distributed tracing spans, no log sampling, no SLOs or error
budgets, and no shipping configuration. Those build on this rather than
replacing it.
