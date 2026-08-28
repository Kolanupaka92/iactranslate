"""Idempotency keys, so a retry does not do the work twice.

Every HTTP client retries. Load balancers retry, SDKs retry, and a user who
watches a spinner for thirty seconds clicks the button again. Without a way to
recognise a repeat, `POST /projects` creates a second project and
`POST /projects/{id}/jobs` runs the whole pipeline a second time — burning CPU,
overwriting artifacts, and leaving the caller unable to tell which of the two
results is theirs.

The contract follows the convention Stripe popularised and most enterprise
clients already implement: send `Idempotency-Key: <token>`; the first request
with that key executes and its response is remembered; later requests with the
same key replay the stored response rather than executing again.

Three cases the naive version gets wrong, all handled here:

**A retry that arrives while the first is still running.** Replaying nothing and
executing again would defeat the purpose, so an in-flight key returns **409**
with `Retry-After`. The caller waits and retries rather than doubling the work.

**The same key with a different body.** That is a client bug — usually a key
generated once and reused across genuinely different requests — and silently
replaying the first response would hide it. Returns **422**, naming the problem.

**Keys leaking between callers.** The cache is scoped by user, method and path
as well as key, so one tenant cannot replay or collide with another's request by
guessing a key.

Records expire (24h by default) because an idempotency cache that never forgets
is a memory leak with a long fuse. Like the rest of the single-node runtime this
is in-process; the interface is what a Redis-backed version would implement, and
until then a retry that lands on a different replica will not be recognised.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

HEADER = "Idempotency-Key"

#: How long a completed result stays replayable.
DEFAULT_TTL_SECONDS = 24 * 3600

#: Bound the table: keys are caller-supplied, so an attacker rotating them would
#: otherwise grow it without limit — the same reasoning as the rate limiter's cap.
MAX_RECORDS = 10_000

#: Longest key accepted. Keys are attacker-influenced and land in memory.
MAX_KEY_LENGTH = 255


@dataclass
class _Record:
    fingerprint: str
    created_at: float
    status: Optional[int] = None
    body: Optional[bytes] = None
    media_type: str = "application/json"
    in_flight: bool = True
    headers: Dict[str, str] = field(default_factory=dict)


class Conflict(Exception):
    """A request with this key is already running."""

    def __init__(self, retry_after: int = 1) -> None:
        super().__init__("a request with this Idempotency-Key is still in progress")
        self.retry_after = retry_after


class BodyMismatch(Exception):
    """This key was used before with a different request body."""


class IdempotencyStore:
    def __init__(self, ttl_seconds: Optional[float] = None) -> None:
        self._ttl = float(ttl_seconds if ttl_seconds is not None else _ttl_from_env())
        self._records: Dict[str, _Record] = {}
        self._lock = threading.Lock()

    @staticmethod
    def cache_key(user_id: Optional[str], method: str, path: str, key: str) -> str:
        """Scope the key so it cannot collide or leak across callers or routes."""
        raw = f"{user_id or '-'}|{method}|{path}|{key}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def fingerprint(body: bytes) -> str:
        return hashlib.sha256(body).hexdigest()

    def begin(self, cache_key: str, fingerprint: str) -> Optional[_Record]:
        """Claim the key, or return the completed record to replay.

        Raises `Conflict` if another request holds it, `BodyMismatch` if the key
        was used with different content.
        """
        now = time.monotonic()
        with self._lock:
            self._evict_locked(now)
            record = self._records.get(cache_key)
            if record is not None:
                if record.fingerprint != fingerprint:
                    raise BodyMismatch(
                        "this Idempotency-Key was already used with a different request "
                        "body; generate a new key per distinct request"
                    )
                if record.in_flight:
                    raise Conflict()
                return record
            self._records[cache_key] = _Record(fingerprint=fingerprint, created_at=now)
            return None

    def complete(
        self, cache_key: str, status: int, body: bytes, media_type: str = "application/json"
    ) -> None:
        with self._lock:
            record = self._records.get(cache_key)
            if record is None:
                return
            record.status = status
            record.body = body
            record.media_type = media_type
            record.in_flight = False

    def abandon(self, cache_key: str) -> None:
        """Release a claim whose request failed.

        A failed request must not be replayable: the caller retrying after a 500
        wants another attempt, not the 500 played back forever. Only successful
        responses are worth remembering.
        """
        with self._lock:
            self._records.pop(cache_key, None)

    def _evict_locked(self, now: float) -> None:
        expired = [k for k, r in self._records.items() if now - r.created_at > self._ttl]
        for key in expired:
            del self._records[key]
        if len(self._records) >= MAX_RECORDS:
            ordered = sorted(self._records.items(), key=lambda kv: kv[1].created_at)
            for key, _ in ordered[: len(self._records) // 4]:
                del self._records[key]

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


def _ttl_from_env() -> float:
    try:
        return float(os.getenv("IACTRANSLATE_IDEMPOTENCY_TTL", str(DEFAULT_TTL_SECONDS)))
    except ValueError:
        return float(DEFAULT_TTL_SECONDS)


def read_key(headers) -> Optional[str]:
    """The caller's key, validated. None when absent — the header is optional.

    Idempotency is opt-in: requiring it would break every existing client, and a
    caller who does not send a key gets exactly the previous behaviour.
    """
    raw = (headers.get(HEADER) or "").strip()
    if not raw:
        return None
    if len(raw) > MAX_KEY_LENGTH:
        raise ValueError(f"{HEADER} must be at most {MAX_KEY_LENGTH} characters")
    if not all(c.isalnum() or c in "-_:." for c in raw):
        raise ValueError(f"{HEADER} may contain only letters, digits and - _ : .")
    return raw


def scoped(user_id: Optional[str], method: str, path: str, key: str) -> Tuple[str, str]:
    """Convenience: `(cache_key, key)` for logging."""
    return IdempotencyStore.cache_key(user_id, method, path, key), key
