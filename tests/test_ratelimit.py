"""Rate limiting and security headers (ADR 0028)."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from iactranslate.api.ratelimit import RateLimiter, reset_all


@pytest.fixture(autouse=True)
def _limits_on(monkeypatch):
    """Opt back in to the real limits (conftest disables them suite-wide)."""
    monkeypatch.setenv("IACTRANSLATE_RATE_AUTH", "10")
    monkeypatch.setenv("IACTRANSLATE_RATE_WRITE", "60")
    monkeypatch.setenv("IACTRANSLATE_RATE_READ", "240")
    reset_all()
    yield
    reset_all()


# -- the bucket itself -----------------------------------------------------


def test_allows_up_to_the_limit_then_refuses():
    limiter = RateLimiter("IACTRANSLATE_RATE_TEST", 3, 60.0)
    assert [limiter.check("ip")[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = limiter.check("ip")
    assert allowed is False
    assert retry_after >= 1  # always actionable, never "retry in 0s"


def test_keys_are_independent():
    limiter = RateLimiter("IACTRANSLATE_RATE_TEST", 1, 60.0)
    assert limiter.check("alice")[0] is True
    assert limiter.check("bob")[0] is True      # Bob is unaffected by Alice
    assert limiter.check("alice")[0] is False


def test_tokens_refill_over_time(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr("iactranslate.api.ratelimit.time.monotonic", lambda: clock["t"])

    limiter = RateLimiter("IACTRANSLATE_RATE_TEST", 60, 60.0)  # one token per second
    for _ in range(60):
        assert limiter.check("ip")[0] is True
    assert limiter.check("ip")[0] is False

    clock["t"] += 2.0  # two seconds -> two tokens
    assert limiter.check("ip")[0] is True
    assert limiter.check("ip")[0] is True
    assert limiter.check("ip")[0] is False


def test_a_zero_limit_disables_the_limiter():
    limiter = RateLimiter("IACTRANSLATE_RATE_TEST", 0, 60.0)
    assert all(limiter.check("ip")[0] for _ in range(1000))


def test_bucket_table_is_bounded(monkeypatch):
    """The limiter must not become the memory-exhaustion vector it prevents."""
    monkeypatch.setattr("iactranslate.api.ratelimit._MAX_BUCKETS", 100)
    limiter = RateLimiter("IACTRANSLATE_RATE_TEST", 5, 60.0)
    for i in range(1000):
        limiter.check(f"ip-{i}")
    assert len(limiter._buckets) <= 100


# -- applied to the API ----------------------------------------------------


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("IACTRANSLATE_AUTH", "session")
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "rl.db"))
    monkeypatch.setenv("IACTRANSLATE_COOKIE_SECURE", "0")
    monkeypatch.delenv("IACTRANSLATE_API_KEY", raising=False)
    import iactranslate.api.main as main
    importlib.reload(main)
    yield TestClient(main.app)
    # Undo the env *before* the teardown reload: that reload re-reads the
    # environment, so reloading first would leave the module stuck in
    # multi-tenant mode and 401 every later test in the session.
    monkeypatch.undo()
    importlib.reload(main)


def test_login_brute_force_is_throttled(api):
    api.post("/auth/register", json={"email": "a@example.com", "password": "correct-horse-battery"})
    api.post("/auth/logout")

    codes = [
        api.post("/auth/login", json={"email": "a@example.com", "password": f"guess-{i}-wrong"}).status_code
        for i in range(20)
    ]
    assert 429 in codes, "password guessing was never throttled"
    assert codes.index(429) <= 12, "throttle kicked in too late to matter"


def test_throttled_response_tells_the_client_when_to_retry(api):
    last = None
    for i in range(20):
        last = api.post("/auth/login", json={"email": "x@example.com", "password": f"guess-{i}-wrong"})
        if last.status_code == 429:
            break
    assert last.status_code == 429
    assert int(last.headers["retry-after"]) >= 1


def test_one_account_is_protected_from_many_source_addresses(api):
    """Per-IP throttling alone does nothing against credential stuffing."""
    api.post("/auth/register", json={"email": "victim@example.com", "password": "correct-horse-battery"})
    api.post("/auth/logout")

    codes = []
    for i in range(20):
        # A different source address each time — only the per-email bucket can stop this.
        codes.append(
            api.post(
                "/auth/login",
                json={"email": "victim@example.com", "password": f"guess-{i}-wrong"},
                headers={"x-forwarded-for": f"203.0.113.{i}"},
            ).status_code
        )
    assert 429 in codes


def test_security_headers_are_present(api):
    r = api.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    # Not asserted over plaintext http — it would pin localhost to https.
    assert "strict-transport-security" not in r.headers


def test_spoofed_forwarded_header_is_ignored_by_default(monkeypatch):
    """X-Forwarded-For must not be trusted unless a proxy is declared, or any
    client could bypass every limit by rotating a fake value."""
    from starlette.datastructures import Headers

    from iactranslate.api.ratelimit import client_key

    monkeypatch.delenv("IACTRANSLATE_TRUST_PROXY", raising=False)

    class _Req:
        headers = Headers({"x-forwarded-for": "1.2.3.4"})
        client = type("C", (), {"host": "10.0.0.1"})()

    assert client_key(_Req()) == "10.0.0.1"          # real peer wins
    monkeypatch.setenv("IACTRANSLATE_TRUST_PROXY", "1")
    assert client_key(_Req()) == "1.2.3.4"           # trusted only when declared
