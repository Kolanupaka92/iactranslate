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


def test_a_table_name_that_is_not_an_identifier_is_refused(db):
    """A table name cannot be bound as a parameter, so the one statement that
    interpolates one validates it rather than trusting the caller."""
    with pytest.raises(ValueError):
        db.claim_sql("jobs; DROP TABLE projects")


# --- accounts -----------------------------------------------------------------

@pytest.fixture
def accounts(db):
    from iactranslate.api.accounts import AccountStore

    for table in ("users", "sessions", "password_resets"):
        db.execute(f"DROP TABLE IF EXISTS {table}")
    yield AccountStore(db)
    for table in ("users", "sessions", "password_resets"):
        db.execute(f"DROP TABLE IF EXISTS {table}")


def test_a_duplicate_email_raises_email_taken(accounts):
    """The drivers raise unrelated exception classes for a constraint
    violation. Catching only SQLite's would surface this as a 500 rather than
    the "that address is taken" it is."""
    from iactranslate.api.accounts import EmailTaken

    accounts.create_user("a@acme.test", "correct horse battery")
    with pytest.raises(EmailTaken):
        accounts.create_user("a@acme.test", "another password entirely")


def test_credentials_round_trip(accounts):
    user = accounts.create_user("b@acme.test", "correct horse battery")
    assert accounts.authenticate("b@acme.test", "correct horse battery").id == user.id


def test_a_session_resolves_and_revokes(accounts):
    user = accounts.create_user("c@acme.test", "correct horse battery")
    token = accounts.create_session(user.id)
    assert accounts.user_for_session(token).id == user.id
    assert accounts.delete_sessions_for_user(user.id) == 1
    assert accounts.user_for_session(token) is None


def test_a_reset_token_is_single_use(accounts):
    """Single use is a security property. Read-then-delete has to be one
    transaction or two concurrent redemptions of the same link could both
    read the row before either deleted it."""
    user = accounts.create_user("d@acme.test", "correct horse battery")
    token = accounts.create_reset_token(user.id)
    assert accounts.consume_reset_token(token) == user.id
    assert accounts.consume_reset_token(token) is None


# --- roles --------------------------------------------------------------------

def test_grants_upsert_and_survive_a_reload(db):
    """`ON CONFLICT ... DO UPDATE` is spelled identically on both engines, which
    is the only reason this needed no dialect branch."""
    from iactranslate.api.roles import Role, SqlMembership

    db.execute("DROP TABLE IF EXISTS project_members")
    members = SqlMembership(db)
    members.grant("p1", "u1", Role.VIEWER)
    members.grant("p1", "u1", Role.ADMIN)  # upsert, not a duplicate row

    # `owner_id=None` so the assertion reads the stored grant rather than the
    # owner-is-always-admin shortcut, which would pass whatever the row said.
    reloaded = SqlMembership(db)
    assert reloaded.role_for("p1", "u1", None) is Role.ADMIN
    assert members.revoke("p1", "u1") is True
    assert SqlMembership(db).role_for("p1", "u1", None) is None
    db.execute("DROP TABLE IF EXISTS project_members")


# --- audit --------------------------------------------------------------------

def test_the_audit_trail_appends_and_reads_back(db):
    """`INTEGER PRIMARY KEY AUTOINCREMENT` has no PostgreSQL equivalent; the
    schema names it `{SERIAL_PK}` so each engine gets its own."""
    from iactranslate.api.audit import SqlAuditLog
    from iactranslate.api.events import Event, EventBus, EventType

    db.execute("DROP TABLE IF EXISTS audit")
    log = SqlAuditLog(db, capacity=100)
    bus = EventBus()
    log.attach(bus)
    bus.publish(Event(EventType.PROJECT_CREATED, project_id="p1"))
    bus.publish(Event(EventType.PROJECT_CREATED, project_id="p2"))

    assert len(log.recent()) == 2
    assert [e.project_id for e in log.recent(project_id="p1")] == ["p1"]
    db.execute("DROP TABLE IF EXISTS audit")


def test_the_audit_trail_trims_to_capacity(db):
    from iactranslate.api.audit import SqlAuditLog
    from iactranslate.api.events import Event, EventBus, EventType

    db.execute("DROP TABLE IF EXISTS audit")
    log = SqlAuditLog(db, capacity=5)
    bus = EventBus()
    log.attach(bus)
    for i in range(12):
        bus.publish(Event(EventType.PROJECT_CREATED, project_id=f"p{i}"))
    assert db.query_one("SELECT COUNT(*) FROM audit")[0] == 5
    db.execute("DROP TABLE IF EXISTS audit")


# --- the job queue ------------------------------------------------------------

def test_two_workers_never_claim_the_same_job(db):
    """The reason `FOR UPDATE SKIP LOCKED` is in the layer at all.

    Claiming is the one operation where the engines need different SQL rather
    than different spelling, and double-claiming is the failure that would show
    up as a workload migrated twice.
    """
    import threading

    from iactranslate.api.events import EventBus
    from iactranslate.api.jobs_sqlite import SqlJobQueue

    db.execute("DROP TABLE IF EXISTS jobs")
    queue = SqlJobQueue(EventBus(), db, max_workers=0)
    for i in range(20):
        queue.submit(f"proj-{i}")

    claimed, errors = [], []
    lock = threading.Lock()

    def drain():
        try:
            while True:
                row = queue._claim()
                if row is None:
                    return
                with lock:
                    claimed.append(row["id"])
        except Exception as exc:  # noqa: BLE001 — surfaced in the assertion
            errors.append(exc)

    threads = [threading.Thread(target=drain) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert len(claimed) == 20, "every job should be claimed exactly once"
    assert len(set(claimed)) == 20, "a job was claimed twice"
    db.execute("DROP TABLE IF EXISTS jobs")


def test_an_expired_lease_is_reclaimed_but_a_live_one_is_not(db):
    import time as _time

    from iactranslate.api.events import EventBus
    from iactranslate.api.jobs_sqlite import SqlJobQueue

    db.execute("DROP TABLE IF EXISTS jobs")
    queue = SqlJobQueue(EventBus(), db, max_workers=0)
    dead = queue.submit("proj-dead")
    live = queue.submit("proj-live")
    db.execute("UPDATE jobs SET status='running', lease_until=? WHERE id=?",
               (_time.time() - 1, dead.id))
    db.execute("UPDATE jobs SET status='running', lease_until=? WHERE id=?",
               (_time.time() + 600, live.id))

    assert queue.reclaim_expired() == 1
    assert queue.get(dead.id).status.value == "queued"
    assert queue.get(live.id).status.value == "running"
    db.execute("DROP TABLE IF EXISTS jobs")
