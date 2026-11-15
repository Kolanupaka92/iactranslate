"""Shared token buckets, so rate limits survive horizontal scaling.

The in-process limiter in `ratelimit.py` keeps its buckets in this replica's
memory. That is not "approximate at scale" — it is **wrong** the moment a second
replica starts, and wrong in the attacker's favour: with N replicas behind a load
balancer the effective limit is N times the configured one, and a caller who
simply reconnects until they land on a different replica gets a fresh bucket.
On `/auth/login` that is the difference between a working credential-stuffing
defence and a decorative one.

**Atomicity is the whole point.** Read-modify-write against Redis from several
replicas has exactly the race the shared store was meant to remove, so the bucket
is evaluated inside a Lua script — one round trip, executed atomically by Redis.

**One clock.** The script reads Redis's own `TIME` rather than trusting a
timestamp from the caller. Replica clocks drift, and a replica whose clock ran
fast would refill buckets faster than the limit allows; taking the server's clock
means every replica agrees by construction.

**Keys expire.** Each bucket carries a TTL of twice its period, so idle keys are
reclaimed by Redis. The in-process limiter needs an eviction policy to avoid
becoming the memory-exhaustion vector it exists to prevent; here that is free.

**Degradation is deliberate.** If Redis is unreachable the limiter falls back to
the in-process bucket rather than failing open or failing closed. Failing open
would hand an attacker an unlimited window precisely when infrastructure is
already unhealthy; failing closed would let a Redis outage take down the whole
API. Falling back preserves per-replica protection — weaker than shared, far
better than none — and logs loudly so the degradation is visible rather than
silent.
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

from ..observability import get_logger

logger = get_logger("iactranslate.api.ratelimit")

ENV_URL = "IACTRANSLATE_REDIS_URL"

#: Token-bucket refill and consume, evaluated atomically.
#:
#: KEYS[1] bucket key · ARGV[1] limit · ARGV[2] period seconds
#: returns {allowed, retry_after_seconds}
_BUCKET_LUA = """
local limit  = tonumber(ARGV[1])
local period = tonumber(ARGV[2])
local refill = limit / period

-- Redis's clock, not the caller's: replicas drift, and a fast clock would
-- refill buckets faster than the configured rate.
local t   = redis.call('TIME')
local now = tonumber(t[1]) + (tonumber(t[2]) / 1000000)

local data   = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts     = tonumber(data[2])

if tokens == nil or ts == nil then
  tokens = limit
  ts = now
end

local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(limit, tokens + elapsed * refill)

local allowed = 0
local retry = 0
if tokens >= 1.0 then
  tokens = tokens - 1.0
  allowed = 1
else
  local deficit = 1.0 - tokens
  retry = math.floor(deficit / refill) + 1
  if retry < 1 then retry = 1 end
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
-- Two periods is long enough that a bucket mid-refill is never dropped, and
-- short enough that abandoned keys do not accumulate.
redis.call('EXPIRE', KEYS[1], math.ceil(period * 2))
return {allowed, retry}
"""

_KEY_PREFIX = "iactranslate:rl:"


class RedisBuckets:
    """Shared token buckets in Redis. Returns None when unavailable."""

    def __init__(self, client) -> None:
        self._client = client
        self._script = client.register_script(_BUCKET_LUA)
        self._degraded = False

    def check(self, name: str, key: str, limit: float, period: float) -> Optional[Tuple[bool, int]]:
        """Consume a token. `None` means Redis could not answer — fall back.

        The bucket name is part of the key so the auth, read and write limiters
        cannot share a bucket; two limits enforced against one counter would let
        ordinary reads exhaust the auth budget.
        """
        try:
            allowed, retry = self._script(
                keys=[f"{_KEY_PREFIX}{name}:{key}"], args=[limit, period]
            )
        except Exception:
            # Logged once per transition rather than per request: a Redis outage
            # would otherwise emit a line per call and bury everything else.
            if not self._degraded:
                self._degraded = True
                logger.error(
                    "rate limiting degraded to per-replica buckets — Redis unreachable",
                    exc_info=True,
                    extra={"limiter": name},
                )
            return None
        if self._degraded:
            self._degraded = False
            logger.info("rate limiting restored to shared buckets", extra={"limiter": name})
        return bool(allowed), int(retry)


def create_backend() -> Optional[RedisBuckets]:
    """A shared backend when `IACTRANSLATE_REDIS_URL` is set, else None.

    Absent configuration this returns None and the in-process limiter is used
    unchanged — a single-node deployment needs no Redis, and requiring one would
    undermine the "runs anywhere with no services" property.
    """
    url = os.getenv(ENV_URL, "").strip()
    if not url:
        return None
    try:
        import redis
    except ImportError:
        logger.error(
            "%s is set but the 'redis' package is not installed — install "
            "iactranslate[redis]. Falling back to per-replica rate limiting, "
            "which does not hold across replicas.",
            ENV_URL,
        )
        return None
    try:
        client = redis.Redis.from_url(url, socket_timeout=1.0, socket_connect_timeout=1.0)
        client.ping()
    except Exception:
        logger.error(
            "cannot reach Redis at the configured URL; rate limiting will use "
            "per-replica buckets until it recovers",
            exc_info=True,
        )
        # Still return the backend: Redis may come back, and `check` degrades
        # per call rather than permanently disabling shared limiting because the
        # process happened to start during an outage.
        try:
            return RedisBuckets(redis.Redis.from_url(url, socket_timeout=1.0))
        except Exception:
            return None
    return RedisBuckets(client)
