"""Structured logging and request correlation.

The audit finding this module answers: the service emitted human-prose log
records from four modules, with no configured handler, no correlation between
an API request and the pipeline run it triggered, and no machine-parseable
shape. Metrics (`/metrics`) said *how many* translations failed; the audit trail
said *who* asked. Neither could answer "what happened to this one run", which is
the only question that matters at 3am.

Three pieces, deliberately small:

  * **A JSON formatter.** One object per line, stable field names borrowed from
    OpenTelemetry semantic conventions (`trace_id`, `http.method`,
    `http.status_code`, `duration_ms`) so shipping this to an OTel collector
    later is a config change, not a rewrite.
  * **A correlation id in a `ContextVar`.** Set once per request, read by every
    log record beneath it — no threading it through twenty function signatures.
  * **`stage()`**, a timing context manager, so slow stages are findable without
    a profiler.

**No OpenTelemetry dependency.** Adding an SDK to a tool whose main selling
point is that it runs offline with no API key would be a poor trade. The field
names are OTel's so the migration stays cheap; the wire format is stdlib
`logging`, which every collector already ingests.

Off by default — `configure_logging()` is called by the API and the CLI, never
on import, so importing `iactranslate` as a library never reconfigures the host
application's logging.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

#: Correlation id for the work currently in flight. Empty outside a request.
#:
#: A ``ContextVar`` (not thread-local) because it must survive ``await`` points
#: in FastAPI. It does *not* propagate into ``ThreadPoolExecutor`` workers on
#: its own — the job queue copies the context explicitly when it submits work,
#: otherwise an async translation would log under a blank id and the request
#: that started it could never be tied to the run that did the work.
correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")

#: Fields `logging` puts on every record. Anything outside this set was passed
#: by the caller via `extra=` and is merged into the JSON output.
_RESERVED = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname levelno
    lineno module msecs message msg name pathname process processName relativeCreated
    stack_info thread threadName taskName""".split()
)


def new_correlation_id() -> str:
    """A short, URL-safe id. Short because humans paste these into tickets."""
    return uuid.uuid4().hex[:16]


class JsonFormatter(logging.Formatter):
    """One JSON object per line.

    Deliberately tolerant: a log call must never raise. Values that will not
    serialize are coerced with `str()` rather than crashing the request that
    was trying to report a problem.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        cid = correlation_id.get()
        if cid:
            payload["trace_id"] = cid
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: Optional[str] = None, json_output: Optional[bool] = None) -> None:
    """Install a single stderr handler on the `iactranslate` logger.

    Scoped to our own logger rather than the root so that importing this
    package never hijacks the logging of an application embedding it. Idempotent
    — calling twice does not double every line, which matters because both the
    CLI entrypoint and the API app factory call it.

    Env: ``IACTRANSLATE_LOG_LEVEL`` (default INFO),
    ``IACTRANSLATE_LOG_FORMAT=json|text`` (default json).
    """
    level = level or os.getenv("IACTRANSLATE_LOG_LEVEL", "INFO")
    if json_output is None:
        json_output = os.getenv("IACTRANSLATE_LOG_FORMAT", "json").lower() != "text"

    root = logging.getLogger("iactranslate")
    for existing in list(root.handlers):
        root.removeHandler(existing)

    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter() if json_output
        else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Ours is the only handler that should see these records; letting them
    # propagate to root means a host app with its own basicConfig prints
    # everything twice.
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Logger under the `iactranslate` namespace, so one call configures all."""
    return logging.getLogger(name if name.startswith("iactranslate") else f"iactranslate.{name}")


@contextmanager
def stage(logger: logging.Logger, name: str, **fields: Any) -> Iterator[Dict[str, Any]]:
    """Time a unit of work and log its outcome exactly once.

    Yields a dict the caller can add to; whatever is in it at exit is logged
    alongside the duration, so a stage can report what it *found* (vm_count,
    violations) rather than only how long it took. Failures are logged with the
    duration too — how long a thing ran before dying is usually the clue.
    """
    extra: Dict[str, Any] = dict(fields)
    started = time.perf_counter()
    try:
        yield extra
    except Exception as exc:
        extra["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        extra["stage"] = name
        extra["outcome"] = "error"
        extra["error_type"] = type(exc).__name__
        logger.error("stage %s failed", name, extra=extra, exc_info=True)
        raise
    extra["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    extra["stage"] = name
    extra["outcome"] = "ok"
    logger.info("stage %s", name, extra=extra)
