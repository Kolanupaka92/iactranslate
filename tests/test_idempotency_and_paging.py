"""Retried writes, and paged lists.

Every HTTP client retries — load balancers, SDKs, and users who click twice.
Without replay protection a retried `POST /projects` creates a second project
and a retried job runs the whole pipeline again.
"""
import pytest
from fastapi.testclient import TestClient

from iactranslate.api.idempotency import (
    HEADER,
    BodyMismatch,
    Conflict,
    IdempotencyStore,
    read_key,
)
from iactranslate.api.main import app

V1 = "/v1"


@pytest.fixture
def client():
    from iactranslate.api.main import idempotency
    idempotency.reset()
    return TestClient(app)


def _new(client, name="idem", key=None):
    headers = {HEADER: key} if key else {}
    return client.post(f"{V1}/projects", json={"name": name, "target": "aws"}, headers=headers)


# --- the store ---------------------------------------------------------------

def test_keys_are_scoped_so_tenants_cannot_collide():
    a = IdempotencyStore.cache_key("user-a", "POST", "/v1/projects", "k1")
    b = IdempotencyStore.cache_key("user-b", "POST", "/v1/projects", "k1")
    assert a != b


def test_the_same_key_on_a_different_route_is_a_different_record():
    a = IdempotencyStore.cache_key("u", "POST", "/v1/projects", "k1")
    b = IdempotencyStore.cache_key("u", "POST", "/v1/jobs", "k1")
    assert a != b


def test_an_in_flight_key_conflicts_rather_than_running_twice():
    store = IdempotencyStore()
    key = IdempotencyStore.cache_key("u", "POST", "/p", "k")
    assert store.begin(key, "fp") is None
    with pytest.raises(Conflict):
        store.begin(key, "fp")


def test_reusing_a_key_with_a_different_body_is_rejected():
    """Usually a key generated once and reused; replaying would hide the bug."""
    store = IdempotencyStore()
    key = IdempotencyStore.cache_key("u", "POST", "/p", "k")
    store.begin(key, "fingerprint-one")
    store.complete(key, 201, b"{}")
    with pytest.raises(BodyMismatch):
        store.begin(key, "fingerprint-two")


def test_a_failed_request_is_not_replayable():
    """A caller retrying after a 500 wants another attempt, not the 500 back."""
    store = IdempotencyStore()
    key = IdempotencyStore.cache_key("u", "POST", "/p", "k")
    store.begin(key, "fp")
    store.abandon(key)
    assert store.begin(key, "fp") is None  # claimable again


def test_records_expire():
    store = IdempotencyStore(ttl_seconds=0)
    key = IdempotencyStore.cache_key("u", "POST", "/p", "k")
    store.begin(key, "fp")
    store.complete(key, 201, b"{}")
    assert store.begin(key, "fp") is None, "an expired record should not replay"


def test_hostile_keys_are_rejected():
    """Caller-supplied and lands in memory."""
    assert read_key({HEADER: "abc-123"}) == "abc-123"
    assert read_key({}) is None
    with pytest.raises(ValueError):
        read_key({HEADER: "A" * 300})
    with pytest.raises(ValueError):
        read_key({HEADER: "bad key!\n"})


# --- through the API ---------------------------------------------------------

def test_a_retried_create_does_not_make_a_second_project(client):
    first = _new(client, key="retry-me")
    second = _new(client, key="retry-me")
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["Idempotency-Replayed"] == "true"


def test_the_replay_is_marked_so_a_client_can_tell(client):
    _new(client, key="marked")
    assert "Idempotency-Replayed" not in _new(client, key="other").headers
    assert _new(client, key="marked").headers["Idempotency-Replayed"] == "true"


def test_without_a_key_nothing_changes(client):
    """Idempotency is opt-in; requiring it would break every existing client."""
    a, b = _new(client), _new(client)
    assert a.json()["id"] != b.json()["id"], "two calls, two projects — as before"


def test_a_reused_key_with_a_different_body_is_422(client):
    _new(client, name="one", key="shared")
    r = _new(client, name="two", key="shared")
    assert r.status_code == 422
    assert "different request body" in r.json()["detail"]


def test_a_malformed_key_is_rejected_before_doing_work(client):
    r = client.post(f"{V1}/projects", json={"name": "x", "target": "aws"},
                    headers={HEADER: "bad key!"})
    assert r.status_code == 400


def test_a_rejected_request_does_not_burn_the_key(client):
    """A 400 must leave the key usable — the caller will fix the body and retry."""
    bad = client.post(f"{V1}/projects", json={"name": "", "target": "aws"},
                      headers={HEADER: "reusable"})
    assert bad.status_code == 422  # validation failure
    good = _new(client, key="reusable")
    assert good.status_code == 201, "the key should still be claimable"


# --- paging ------------------------------------------------------------------

def test_an_unparameterised_list_is_unchanged(client):
    """ADR 0049 argued against breaking clients; this must not break them either."""
    for i in range(4):
        _new(client, name=f"page{i}")
    r = client.get(f"{V1}/projects")
    assert isinstance(r.json(), list), "still an array, not an envelope"
    assert r.headers["X-Total-Count"].isdigit()
    assert "Link" not in r.headers, "one page means no next link"


def test_limit_and_offset_walk_the_list(client):
    for i in range(5):
        _new(client, name=f"walk{i}")
    first = client.get(f"{V1}/projects?limit=2&offset=0")
    assert len(first.json()) == 2
    assert 'rel="next"' in first.headers["Link"]

    second = client.get(f"{V1}/projects?limit=2&offset=2")
    assert 'rel="prev"' in second.headers["Link"]
    assert 'rel="first"' in second.headers["Link"]

    ids = {p["id"] for p in first.json()} | {p["id"] for p in second.json()}
    assert len(ids) == 4, "pages must not overlap"


def test_the_last_page_has_no_next_link(client):
    for i in range(3):
        _new(client, name=f"last{i}")
    total = int(client.get(f"{V1}/projects").headers["X-Total-Count"])
    r = client.get(f"{V1}/projects?limit=2&offset={max(0, total - 1)}")
    assert 'rel="next"' not in (r.headers.get("Link") or "")


@pytest.mark.parametrize("query", ["limit=0", "limit=99999", "offset=-1"])
def test_nonsense_paging_is_rejected(client, query):
    assert client.get(f"{V1}/projects?{query}").status_code == 400
