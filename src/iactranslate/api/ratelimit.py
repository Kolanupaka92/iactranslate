"""Request rate limiting — token buckets, stdlib only.

Three surfaces need very different limits, so this is applied per-route rather
than as one blanket middleware:

- **Auth** (`/auth/login`, `/auth/register`) is the most attackable endpoint in
  the product: it accepts a password and tells you whether it was right. It gets
  the strictest limit, and is throttled **per email as well as per IP** —
  IP-only throttling does nothing against credential stuffing, which hits one
  account from many addresses.
- **Expensive work** (upload, run, jobs, report) costs real CPU and disk per
  call, so a low ceiling matters more than a generous one.
- **Everything else** gets a high ceiling that only stops runaway clients.

Token bucket rather than a fixed window: a fixed window lets a caller spend its
whole quota in the last second of one window and again in the first second of
the next, giving 2x the intended burst across the boundary.

**Honest boundary:** buckets live in this process's memory. Two API replicas
therefore allow roughly twice the configured rate, and a restart forgets all
counters. Correct enforcement across replicas needs shared state (Redis) —
this is a real limit for a single node, not a distributed rate limiter.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from fastapi import HTTPException, Request

# Bound the bucket table. Without a cap, an attacker rotating source addresses
# would grow it without limit — the rate limiter would become the memory-
# exhaustion vector it exists to prevent.
_MAX_BUCKETS = 20_000


@dataclass
class _Bucket:
    tokens: float
    last_seen: float


class RateLimiter:
    """Token bucket: `limit` requests per `period` seconds, per key.

    The limit is resolved from the environment on **every** check rather than
    at import, so an operator can retune a running deployment (or disable a
    limiter with 0) without a restart. The lookup is a dict read — immaterial
    next to the cost of the request it guards.
    """

    def __init__(
        self, env_var: str, default: int, period: float, name: str = "requests"
    ) -> None:
        self.env_var = env_var
        self.default = default
        self.period = float(period)
        self.name = name
        self._buckets: Dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    @property
    def limit(self) -> float:
        try:
            return float(int(os.getenv(self.env_var, str(self.default))))
        except ValueError:
            return float(self.default)

    def check(self, key: str) -> Tuple[bool, int]:
        """Consume one token. Returns `(allowed, retry_after_seconds)`."""
        limit = self.limit
        if limit <= 0:  # disabled
            return True, 0
        now = time.monotonic()
        refill_per_second = limit / self.period

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._evict_locked(now)
                bucket = self._buckets[key] = _Bucket(tokens=limit, last_seen=now)
            else:
                elapsed = now - bucket.last_seen
                bucket.tokens = min(limit, bucket.tokens + elapsed * refill_per_second)
                bucket.last_seen = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0

            # Seconds until one whole token is available again.
            deficit = 1.0 - bucket.tokens
            return False, max(1, int(deficit / refill_per_second) + 1)

    def _evict_locked(self, now: float) -> None:
        """Drop buckets idle for a full period; if still over capacity, drop the
        least recently seen. Caller must hold the lock."""
        if len(self._buckets) < _MAX_BUCKETS:
            return
        stale = [k for k, b in self._buckets.items() if now - b.last_seen > self.period]
        for key in stale:
            del self._buckets[key]
        if len(self._buckets) >= _MAX_BUCKETS:
            ordered = sorted(self._buckets.items(), key=lambda kv: kv[1].last_seen)
            for key, _ in ordered[: len(self._buckets) // 4]:
                del self._buckets[key]

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# Deliberately different ceilings — see the module docstring. Each is tunable
# at runtime via its environment variable; 0 disables that limiter entirely.
auth_limiter = RateLimiter("IACTRANSLATE_RATE_AUTH", 10, 60.0, "auth attempts")
write_limiter = RateLimiter("IACTRANSLATE_RATE_WRITE", 60, 60.0, "requests")
read_limiter = RateLimiter("IACTRANSLATE_RATE_READ", 240, 60.0, "requests")


def client_key(request: Request) -> str:
    """Identify the caller.

    `X-Forwarded-For` is trusted **only** when `IACTRANSLATE_TRUST_PROXY=1`,
    because any client can send that header — trusting it unconditionally would
    let an attacker bypass every limit by rotating a fake value. Set it when
    (and only when) a proxy you control rewrites the header.
    """
    if os.getenv("IACTRANSLATE_TRUST_PROXY", "0") == "1":
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _enforce(limiter: RateLimiter, key: str) -> None:
    allowed, retry_after = limiter.check(key)
    if not allowed:
        raise HTTPException(
            429,
            f"too many {limiter.name}; retry in {retry_after}s",
            headers={"Retry-After": str(retry_after)},
        )


def limit_reads(request: Request) -> None:
    _enforce(read_limiter, f"read:{client_key(request)}")


def limit_writes(request: Request) -> None:
    _enforce(write_limiter, f"write:{client_key(request)}")


def limit_auth(request: Request, email: Optional[str] = None) -> None:
    """Throttle an auth attempt by source address, and by target account.

    Both are needed: the per-IP bucket stops one host hammering many accounts,
    the per-email bucket stops many hosts hammering one account.
    """
    _enforce(auth_limiter, f"auth-ip:{client_key(request)}")
    if email:
        _enforce(auth_limiter, f"auth-email:{email.strip().lower()}")


def reset_all() -> None:
    """Clear every bucket (tests only)."""
    for limiter in (auth_limiter, write_limiter, read_limiter):
        limiter.reset()
