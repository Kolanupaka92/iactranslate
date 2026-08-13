"""SQLite-backed project store — the real, testable fix for "state dies on
restart" (ADR 0025). Covers what actually matters: a *second*, independent
store instance opened against the same db file sees what the first wrote —
the literal simulation of a process restart.
"""
import os

from iactranslate.api.store import ProjectStore, SqliteProjectStore, create_store


def test_sqlite_store_survives_a_simulated_restart(tmp_path):
    db_path = str(tmp_path / "projects.db")
    store_a = SqliteProjectStore(db_path)
    project = store_a.create(name="persist-me", target="aws", provider="anthropic")

    # A fresh instance against the same file == a new process after restart.
    store_b = SqliteProjectStore(db_path)
    fetched = store_b.get(project.id)
    assert fetched is not None
    assert fetched.name == "persist-me"
    assert fetched.target == "aws"
    assert fetched.provider == "anthropic"
    assert fetched.status == "created"


def test_sqlite_store_save_persists_mutations_across_instances(tmp_path):
    db_path = str(tmp_path / "projects.db")
    store_a = SqliteProjectStore(db_path)
    project = store_a.create(name="mutate-me")
    project.status = "completed"
    project.summary = {"vm_count": 7}
    project.error = None
    store_a.save(project)

    store_b = SqliteProjectStore(db_path)
    fetched = store_b.get(project.id)
    assert fetched.status == "completed"
    assert fetched.summary == {"vm_count": 7}


def test_sqlite_store_round_trips_json_fields(tmp_path):
    db_path = str(tmp_path / "projects.db")
    store = SqliteProjectStore(db_path)
    project = store.create(
        name="mapped", column_map={"name": "Hostname"}, policy={"no-public-ip": {}},
    )
    fetched = store.get(project.id)
    assert fetched.column_map == {"name": "Hostname"}
    assert fetched.policy == {"no-public-ip": {}}


def test_sqlite_store_delete_removes_row_and_workspace(tmp_path):
    db_path = str(tmp_path / "projects.db")
    store = SqliteProjectStore(db_path)
    project = store.create(name="delete-me")
    workspace = project.workspace
    assert workspace.exists()
    assert store.delete(project.id) is True
    assert store.get(project.id) is None
    assert not workspace.exists()
    assert store.delete(project.id) is False


def test_sqlite_store_evicts_oldest_beyond_capacity(tmp_path):
    db_path = str(tmp_path / "projects.db")
    store = SqliteProjectStore(db_path, max_projects=2)
    first = store.create(name="oldest")
    middle = store.create(name="middle")
    newest = store.create(name="newest")
    assert store.get(first.id) is None  # evicted
    assert store.get(middle.id) is not None
    assert store.get(newest.id) is not None


def test_memory_store_save_is_a_safe_no_op():
    store = ProjectStore()
    project = store.create(name="in-memory")
    project.status = "completed"
    store.save(project)  # must not raise
    assert store.get(project.id).status == "completed"


def test_create_store_defaults_to_memory(monkeypatch):
    monkeypatch.delenv("IACTRANSLATE_STORE", raising=False)
    assert isinstance(create_store(), ProjectStore)


def test_create_store_selects_sqlite_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("IACTRANSLATE_STORE", "sqlite")
    monkeypatch.setenv("IACTRANSLATE_DB_PATH", str(tmp_path / "env.db"))
    store = create_store()
    assert isinstance(store, SqliteProjectStore)
    monkeypatch.delenv("IACTRANSLATE_STORE", raising=False)
    monkeypatch.delenv("IACTRANSLATE_DB_PATH", raising=False)
    os.environ.pop("IACTRANSLATE_STORE", None)


# -- durable workspaces (ADR 0029) -----------------------------------------


def test_workspaces_default_to_a_temp_directory(monkeypatch):
    """Unchanged behaviour for the CLI and local use."""
    import tempfile

    from iactranslate.api.store import new_workspace

    monkeypatch.delenv("IACTRANSLATE_WORKSPACE_ROOT", raising=False)
    ws = new_workspace()
    assert ws.is_dir()
    assert str(ws).startswith(tempfile.gettempdir())


def test_workspace_root_places_workspaces_on_a_durable_volume(tmp_path, monkeypatch):
    from iactranslate.api.store import new_workspace

    root = tmp_path / "artifacts"
    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(root))

    a, b = new_workspace(), new_workspace()
    assert a.parent == root and b.parent == root
    assert a != b                      # unique per project
    assert oct(a.stat().st_mode)[-3:] == "700"  # mkdtemp keeps it private


def test_workspace_root_is_created_if_missing(tmp_path, monkeypatch):
    root = tmp_path / "does" / "not" / "exist" / "yet"
    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(root))
    from iactranslate.api.store import new_workspace

    assert new_workspace().parent == root


def test_generated_files_survive_a_restart_with_a_durable_root(tmp_path, monkeypatch):
    """Metadata *and* files must both survive, or a download 409s after restart."""
    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(tmp_path / "artifacts"))
    db = str(tmp_path / "p.db")

    store_a = SqliteProjectStore(db)
    project = store_a.create(name="durable")
    artifact = project.workspace / "project.zip"
    artifact.write_bytes(b"generated terraform")
    project.zip_path = artifact
    store_a.save(project)

    # A new process: the row is reloaded and the file it points at is still there.
    fetched = SqliteProjectStore(db).get(project.id)
    assert fetched.zip_path.exists()
    assert fetched.zip_path.read_bytes() == b"generated terraform"
