"""One PostgreSQL pool per process, shared by every store.

Five stores each opening their own pool of 10 let one API instance hold 50
connections. On a Cloud SQL db-f1-micro (25, 3 reserved) a new revision could
not start while old instances held theirs. Runs against a stand-in pool: the
real-server behaviour is covered by the postgres integration job.
"""
import sys
import types

import pytest

from iactranslate.api import sql


@pytest.fixture
def fake_pool(monkeypatch):
    made = []

    class ConnectionPool:
        def __init__(self, dsn, min_size, max_size, open):
            self.dsn, self.min_size, self.max_size, self.closed = dsn, min_size, max_size, False
            made.append(self)

        def close(self):
            self.closed = True

    module = types.ModuleType("psycopg_pool")
    module.ConnectionPool = ConnectionPool
    monkeypatch.setitem(sys.modules, "psycopg_pool", module)
    monkeypatch.setattr(sql, "_POOLS", {})
    return made


def test_every_store_shares_one_pool(fake_pool):
    dbs = [sql.Database("postgres", "postgresql://db/iac") for _ in range(5)]
    assert len(fake_pool) == 1
    assert all(db._pool is dbs[0]._pool for db in dbs)
    assert fake_pool[0].max_size == 4 and fake_pool[0].min_size == 1


def test_the_pool_closes_with_its_last_user(fake_pool):
    a = sql.Database("postgres", "postgresql://db/iac")
    b = sql.Database("postgres", "postgresql://db/iac")
    a.close()
    assert not fake_pool[0].closed  # b still uses it
    b.close()
    assert fake_pool[0].closed
    c = sql.Database("postgres", "postgresql://db/iac")  # reopens cleanly
    assert len(fake_pool) == 2 and c._pool is fake_pool[1]


def test_different_databases_get_different_pools(fake_pool):
    sql.Database("postgres", "postgresql://db/one")
    sql.Database("postgres", "postgresql://db/two")
    assert len(fake_pool) == 2


@pytest.mark.parametrize("value,expected", [("2", 2), ("0", 1), ("lots", 4)])
def test_pool_size_is_configurable(fake_pool, monkeypatch, value, expected):
    monkeypatch.setenv("IACTRANSLATE_DB_POOL_SIZE", value)
    sql.Database("postgres", "postgresql://db/iac")
    assert fake_pool[0].max_size == expected
