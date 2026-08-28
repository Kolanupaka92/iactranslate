"""Rate limits that hold across replicas.

The bug: buckets lived in one process's memory, so N replicas behind a load
balancer allowed N times the configured rate, and a caller who reconnected until
they landed elsewhere got a fresh bucket. On `/auth/login` that is the difference
between a working credential-stuffing defence and a decorative one.

Two `RateLimiter` instances stand in for two replicas — separate objects with
separate in-process dicts, exactly the situation that was broken.
"""
import os

import pytest

from iactranslate.api import ratelimit
from iactranslate.api.ratelimit import RateLimiter
from iactranslate.api.ratelimit_redis import RedisBuckets

fakeredis = pytest.importorskip("fakeredis")


@pytest.fixture
def shared(monkeypatch):
    """Point every limiter at one Redis, as replicas share one real Redis.

    Uses a **real** Redis when `IACTRANSLATE_TEST_REDIS_URL` is set (CI does),
    and `fakeredis` otherwise so the suite still runs offline with no services.
    Running both matters: fakeredis is a faithful stand-in but the Lua script,
    `TIME` and TTL semantics are exactly the things a reimplementation can get
    subtly wrong, and this limiter's correctness rests on them.
    """
    url = os.getenv("IACTRANSLATE_TEST_REDIS_URL", "").strip()
    if url:
        import redis

        client = redis.Redis.from_url(url)
        client.flushdb()  # isolate from other tests in the same CI run
    else:
        client = fakeredis.FakeStrictRedis()
    backend = RedisBuckets(client)
    monkeypatch.setattr(ratelimit, "_backend", lambda: backend)
    return backend


@pytest.fixture
def two_replicas(monkeypatch):
    def _make():
        return RateLimiter("IACTRANSLATE_RATE_TEST", 5, 60.0, "requests")
    monkeypatch.setenv("IACTRANSLATE_RATE_TEST", "5")
    return _make


def test_without_sharing_two_replicas_allow_double(two_replicas, monkeypatch):
    """The bug, asserted — so the fix below is measured against something real."""
    monkeypatch.setattr(ratelimit, "_backend", lambda: None)
    a, b = two_replicas(), two_replicas()
    allowed = sum(1 for _ in range(5) if a.check("1.2.3.4")[0])
    allowed += sum(1 for _ in range(5) if b.check("1.2.3.4")[0])
    assert allowed == 10, "each replica hands out a full quota of its own"


def test_shared_buckets_hold_the_limit_across_replicas(shared, two_replicas):
    a, b = two_replicas(), two_replicas()
    allowed = sum(1 for _ in range(5) if a.check("1.2.3.4")[0])
    allowed += sum(1 for _ in range(5) if b.check("1.2.3.4")[0])
    assert allowed == 5, "the configured limit, regardless of which replica served it"


def test_reconnecting_to_another_replica_does_not_reset_the_bucket(shared, two_replicas):
    a, b = two_replicas(), two_replicas()
    for _ in range(5):
        a.check("attacker")
    allowed, retry = b.check("attacker")
    assert not allowed
    assert retry >= 1


def test_different_callers_have_independent_buckets(shared, two_replicas):
    a = two_replicas()
    for _ in range(5):
        a.check("caller-one")
    assert a.check("caller-two")[0]


def test_limiters_do_not_share_a_bucket(shared, monkeypatch):
    """Two limits enforced against one counter would let ordinary reads exhaust
    the auth budget."""
    monkeypatch.setenv("IACTRANSLATE_RATE_AUTH", "3")
    monkeypatch.setenv("IACTRANSLATE_RATE_READ", "3")
    auth = RateLimiter("IACTRANSLATE_RATE_AUTH", 3, 60.0)
    read = RateLimiter("IACTRANSLATE_RATE_READ", 3, 60.0)
    for _ in range(3):
        read.check("1.2.3.4")
    assert auth.check("1.2.3.4")[0], "reads must not consume the auth budget"


def test_a_disabled_limiter_stays_disabled(shared, monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_RATE_TEST", "0")
    limiter = RateLimiter("IACTRANSLATE_RATE_TEST", 5, 60.0)
    assert all(limiter.check("anyone")[0] for _ in range(50))


def test_buckets_expire_so_redis_does_not_grow_without_bound(shared, two_replicas):
    limiter = two_replicas()
    limiter.check("ephemeral")
    keys = shared._client.keys("iactranslate:rl:*")
    assert keys, "a bucket should exist"
    assert shared._client.ttl(keys[0]) > 0, "every bucket must carry a TTL"


def test_redis_failure_falls_back_to_local_buckets(monkeypatch, two_replicas):
    """Falling back preserves per-replica protection. Failing open would hand an
    attacker an unlimited window exactly when infrastructure is unhealthy."""
    class Broken:
        def __init__(self):
            self._client = None
            self._degraded = False

        def check(self, *a, **kw):
            return None  # what RedisBuckets returns when Redis errors

    monkeypatch.setattr(ratelimit, "_backend", lambda: Broken())
    limiter = two_replicas()
    allowed = sum(1 for _ in range(10) if limiter.check("1.2.3.4")[0])
    assert allowed == 5, "still limited, just per replica"


def test_backend_is_absent_without_configuration(monkeypatch):
    """A single-node deployment needs no Redis; requiring one would undermine
    the 'runs anywhere with no services' property."""
    from iactranslate.api.ratelimit_redis import create_backend

    monkeypatch.delenv("IACTRANSLATE_REDIS_URL", raising=False)
    assert create_backend() is None


def test_refill_happens_over_time(shared, monkeypatch):
    """A short period makes the refill observable without a sleep long enough to
    slow the suite."""
    monkeypatch.setenv("IACTRANSLATE_RATE_TEST", "2")
    limiter = RateLimiter("IACTRANSLATE_RATE_TEST", 2, 1.0)
    assert limiter.check("k")[0]
    assert limiter.check("k")[0]
    assert not limiter.check("k")[0]

    import time
    time.sleep(1.1)
    assert limiter.check("k")[0], "tokens should have refilled"
