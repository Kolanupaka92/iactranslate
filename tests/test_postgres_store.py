"""The durable stores, against a real PostgreSQL.

The main suite exercises these against SQLite, which is the default and covers
the logic. It cannot cover the part that actually breaks when an engine is
swapped: placeholder binding, type mapping, schema introspection, and the
job-queue claim — the four things `api/sql.py` exists to reconcile, and exactly
what a reimplementation gets subtly wrong.

Skipped unless `IACTRANSLATE_TEST_POSTGRES_URL` points at a server, so the suite
still runs offline. CI supplies one (see the `postgres-integration` job), which
is the only place these assertions are meaningful — a mock would be testing the
mock's idea of PostgreSQL rather than PostgreSQL.
"""
import os
import uuid

import pytest

from iactranslate.api.sql import Database
from iactranslate.api.store import SqlProjectStore

DSN = os.getenv("IACTRANSLATE_TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(
    not DSN, reason="set IACTRANSLATE_TEST_POSTGRES_URL to run against a real PostgreSQL"
)


@pytest.fixture
def db():
    database = Database("postgres", DSN)
    # Each test gets a clean table; these run against a throwaway CI service.
    database.execute("DROP TABLE IF EXISTS projects")
    yield database
    database.execute("DROP TABLE IF EXISTS projects")
    database.close()


@pytest.fixture
def store(db):
    return SqlProjectStore(db, max_projects=50)


# --- the dialect layer itself -------------------------------------------------

def test_placeholders_are_translated(db):
    """Statements are written with `?`; psycopg binds `%s`. Getting this wrong
    fails at execution, not at import."""
    db.execute("CREATE TABLE IF NOT EXISTS ph (k TEXT PRIMARY KEY, v TEXT)")
    db.execute("INSERT INTO ph (k, v) VALUES (?, ?)", ("a", "b"))
    assert db.query_one("SELECT v FROM ph WHERE k = ?", ("a",))[0] == "b"
    db.execute("DROP TABLE ph")


def test_type_placeholders_resolve_to_postgres_types(db):
    assert db.REAL == "DOUBLE PRECISION"
    assert db.SERIAL_PK == "BIGSERIAL PRIMARY KEY"
    assert "DOUBLE PRECISION" in db.schema("CREATE TABLE t (x {REAL})")


def test_column_introspection_works_without_pragma(db, store):
    """`PRAGMA table_info` does not exist here; the migrations depend on this."""
    assert "renderer" in db.columns("projects")
    assert "owner_id" in db.columns("projects")


def test_the_claim_statement_uses_skip_locked(db):
    """PostgreSQL has the primitive SQLite lacks, and it is strictly better —
    contended rows are skipped rather than serialising every claim."""
    assert "FOR UPDATE SKIP LOCKED" in db.claim_sql("jobs")


# --- the project store, end to end -------------------------------------------

def test_a_project_round_trips(store):
    created = store.create(name="acme", target="azure", renderer="bicep")
    fetched = store.get(created.id)
    assert (fetched.name, fetched.target, fetched.renderer) == ("acme", "azure", "bicep")


def test_json_columns_survive(store):
    created = store.create(name="j", column_map={"VM": "name"}, policy={"max_vcpu": {"max": 8}})
    fetched = store.get(created.id)
    assert fetched.column_map == {"VM": "name"}
    assert fetched.policy == {"max_vcpu": {"max": 8}}


def test_mutations_persist_across_instances(db, store):
    created = store.create(name="p")
    created.status = "completed"
    created.summary = {"vm_count": 7}
    store.save(created)

    # A second store over the same database is what a second replica looks like.
    reopened = SqlProjectStore(db, max_projects=50).get(created.id)
    assert reopened.status == "completed"
    assert reopened.summary == {"vm_count": 7}


def test_listing_is_scoped_to_the_owner(store):
    mine = store.create(name="mine", owner_id="u1")
    store.create(name="theirs", owner_id="u2")
    assert [p.id for p in store.list_for_owner("u1")] == [mine.id]


def test_single_tenant_rows_use_is_null(store):
    """`owner_id IS NULL` cannot be parameterised on either engine, so the
    clause is selected rather than bound — and that branch needs covering."""
    anon = store.create(name="anon", owner_id=None)
    assert anon.id in [p.id for p in store.list_for_owner(None)]


def test_deleting_removes_the_row(store):
    created = store.create(name="gone")
    assert store.delete(created.id) is True
    assert store.get(created.id) is None
    assert store.delete(created.id) is False


def test_capacity_eviction_orders_without_rowid(db):
    """SQLite ordered by `rowid`, which PostgreSQL has no equivalent for."""
    small = SqlProjectStore(db, max_projects=3)
    for i in range(6):
        small.create(name=f"p{i}")
    assert len(small.list_for_owner(None)) == 3


def test_a_missing_project_is_none(store):
    assert store.get(uuid.uuid4().hex[:12]) is None
