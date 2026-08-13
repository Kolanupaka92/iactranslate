"""Project/job store for the API.

Two implementations behind the same interface (`create`/`get`/`delete`),
selected via `IACTRANSLATE_STORE` (default `memory`):

- `ProjectStore` — in-memory `dict`. Fast, zero setup, the right default for
  local use and tests. Project *metadata* (not the generated files) is lost
  on process restart.
- `SqliteProjectStore` (`IACTRANSLATE_STORE=sqlite`) — the same metadata
  persisted to a local SQLite file (`IACTRANSLATE_DB_PATH`, default
  `./iactranslate.db`), so it survives a process restart. This closes the
  "project state lives only in process memory" gap with something real and
  fully testable in an environment with no Postgres/Redis available — see
  ADR 0025.

Generated *files* are a separate concern from metadata. Each project gets an
isolated workspace holding its upload and rendered output. By default that is
a system temp directory — fine locally, but `/tmp` is periodically cleaned and
never survives a container restart, so the database would keep pointing at
files that are gone. Setting `IACTRANSLATE_WORKSPACE_ROOT` to a real volume
makes the files durable too (see `new_workspace`), which together with
`IACTRANSLATE_STORE=sqlite` means a download still works after a restart.
Object storage (S3/GCS) remains the answer for multi-node, where a local
volume isn't shared between replicas.

Both stores are
thread-safe (FastAPI runs sync endpoints in a threadpool) and
capacity-bounded — the oldest projects are evicted (and their temp
workspaces deleted) beyond MAX_PROJECTS so disk usage can't grow without limit.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..config import MAX_PROJECTS


def new_workspace() -> Path:
    """Allocate a project workspace.

    By default this is a system temp directory, which is fine for the CLI and
    local use but is **not durable**: `/tmp` is periodically cleaned, is often
    a RAM disk, and never survives a container restart — so a customer's
    generated Terraform disappears while the database still points at it.

    Setting `IACTRANSLATE_WORKSPACE_ROOT` to a real volume makes generated
    files survive a restart alongside the metadata in `SqliteProjectStore`.
    Both paths go through `mkdtemp`, so the directory is unique and created
    0700 either way.
    """
    root = os.getenv("IACTRANSLATE_WORKSPACE_ROOT", "").strip()
    if root:
        base = Path(root)
        base.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="iactranslate_", dir=str(base)))
    return Path(tempfile.mkdtemp(prefix="iactranslate_"))


@dataclass
class Project:
    id: str
    name: str
    target: str = "aws"
    source: str = "auto"
    column_map: Optional[dict] = None
    region: Optional[str] = None
    policy: Optional[dict] = None
    provider: str = "rule"
    owner_id: Optional[str] = None  # None = single-tenant mode (IACTRANSLATE_AUTH unset)
    status: str = "created"  # created -> uploaded -> completed / failed
    workspace: Path = field(default_factory=new_workspace)
    upload_path: Optional[Path] = None
    project_dir: Optional[Path] = None
    zip_path: Optional[Path] = None
    error: Optional[str] = None
    summary: Optional[dict] = None


class ProjectStore:
    def __init__(self, max_projects: int = MAX_PROJECTS) -> None:
        self._projects: "OrderedDict[str, Project]" = OrderedDict()
        self._max = max_projects
        self._lock = threading.Lock()

    def create(
        self,
        name: str,
        target: str = "aws",
        source: str = "auto",
        column_map: Optional[dict] = None,
        region: Optional[str] = None,
        policy: Optional[dict] = None,
        provider: str = "rule",
        owner_id: Optional[str] = None,
    ) -> Project:
        pid = uuid.uuid4().hex[:12]
        project = Project(
            id=pid, name=name, target=target, source=source,
            column_map=column_map, region=region, policy=policy, provider=provider,
            owner_id=owner_id,
        )
        with self._lock:
            self._projects[pid] = project
            self._evict_locked()
        return project

    def get(self, pid: str) -> Optional[Project]:
        with self._lock:
            return self._projects.get(pid)

    def list_for_owner(self, owner_id: Optional[str]) -> List[Project]:
        """Projects belonging to one owner, newest first."""
        with self._lock:
            return [p for p in reversed(self._projects.values()) if p.owner_id == owner_id]

    def save(self, project: Project) -> None:
        """No-op: `get()` returns the same shared object, so in-place field
        mutations are already visible to every caller. Exists so callers can
        treat every store implementation identically — `SqliteProjectStore`
        genuinely needs this call to persist the mutation."""

    def delete(self, pid: str) -> bool:
        with self._lock:
            project = self._projects.pop(pid, None)
        if project is None:
            return False
        shutil.rmtree(project.workspace, ignore_errors=True)
        return True

    def _evict_locked(self) -> None:
        """Drop oldest projects beyond capacity. Caller must hold the lock."""
        while len(self._projects) > self._max:
            _, victim = self._projects.popitem(last=False)
            shutil.rmtree(victim.workspace, ignore_errors=True)


class SqliteProjectStore:
    """Same interface as `ProjectStore`, persisted to a local SQLite file.

    Project *metadata* survives a process restart. Whether the generated
    *files* also survive depends on `IACTRANSLATE_WORKSPACE_ROOT` — see the
    module docstring and `new_workspace`.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            target TEXT NOT NULL,
            source TEXT NOT NULL,
            column_map TEXT,
            region TEXT,
            policy TEXT,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            workspace TEXT NOT NULL,
            upload_path TEXT,
            project_dir TEXT,
            zip_path TEXT,
            error TEXT,
            summary TEXT,
            created_at REAL NOT NULL,
            owner_id TEXT
        )
    """
    _COLUMNS = (
        "id", "name", "target", "source", "column_map", "region", "policy",
        "provider", "status", "workspace", "upload_path", "project_dir",
        "zip_path", "error", "summary", "created_at", "owner_id",
    )

    def __init__(self, db_path: str, max_projects: int = MAX_PROJECTS) -> None:
        self._max = max_projects
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(self._SCHEMA)
        self._migrate()
        self._conn.execute("CREATE INDEX IF NOT EXISTS projects_owner ON projects (owner_id)")
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns missing from a database created by an older version.

        A file written before multi-tenancy has no `owner_id`; opening it would
        otherwise fail on every query. Additive and idempotent — it never drops
        or rewrites existing rows, so pre-existing projects simply come back
        with `owner_id = NULL` (single-tenant, as they were).
        """
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(projects)")}
        if "owner_id" not in existing:
            self._conn.execute("ALTER TABLE projects ADD COLUMN owner_id TEXT")
            self._conn.commit()

    def _row_to_project(self, row: tuple) -> Project:
        data = dict(zip(self._COLUMNS, row))
        return Project(
            id=data["id"], name=data["name"], target=data["target"], source=data["source"],
            column_map=json.loads(data["column_map"]) if data["column_map"] else None,
            region=data["region"],
            policy=json.loads(data["policy"]) if data["policy"] else None,
            provider=data["provider"], status=data["status"],
            workspace=Path(data["workspace"]),
            upload_path=Path(data["upload_path"]) if data["upload_path"] else None,
            project_dir=Path(data["project_dir"]) if data["project_dir"] else None,
            zip_path=Path(data["zip_path"]) if data["zip_path"] else None,
            error=data["error"],
            summary=json.loads(data["summary"]) if data["summary"] else None,
            owner_id=data["owner_id"],
        )

    def create(
        self,
        name: str,
        target: str = "aws",
        source: str = "auto",
        column_map: Optional[dict] = None,
        region: Optional[str] = None,
        policy: Optional[dict] = None,
        provider: str = "rule",
        owner_id: Optional[str] = None,
    ) -> Project:
        pid = uuid.uuid4().hex[:12]
        workspace = new_workspace()
        with self._lock:
            self._conn.execute(
                "INSERT INTO projects (id, name, target, source, column_map, region, policy, "
                "provider, status, workspace, upload_path, project_dir, zip_path, error, summary, "
                "created_at, owner_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, NULL, NULL, NULL, NULL, NULL, ?, ?)",
                (pid, name, target, source,
                 json.dumps(column_map) if column_map else None, region,
                 json.dumps(policy) if policy else None, provider, str(workspace), time.time(),
                 owner_id),
            )
            self._conn.commit()
            self._evict_locked()
        return Project(
            id=pid, name=name, target=target, source=source, column_map=column_map,
            region=region, policy=policy, provider=provider, workspace=workspace,
            owner_id=owner_id,
        )

    def get(self, pid: str) -> Optional[Project]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {', '.join(self._COLUMNS)} FROM projects WHERE id = ?", (pid,)
            ).fetchone()
        return self._row_to_project(row) if row else None

    def list_for_owner(self, owner_id: Optional[str]) -> List[Project]:
        """Projects belonging to one owner, newest first."""
        clause = "owner_id IS ?" if owner_id is None else "owner_id = ?"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {', '.join(self._COLUMNS)} FROM projects WHERE {clause} ORDER BY rowid DESC",
                (owner_id,),
            ).fetchall()
        return [self._row_to_project(row) for row in rows]

    def save(self, project: Project) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE projects SET status=?, upload_path=?, project_dir=?, zip_path=?, "
                "error=?, summary=? WHERE id=?",
                (project.status,
                 str(project.upload_path) if project.upload_path else None,
                 str(project.project_dir) if project.project_dir else None,
                 str(project.zip_path) if project.zip_path else None,
                 project.error,
                 json.dumps(project.summary) if project.summary else None,
                 project.id),
            )
            self._conn.commit()

    def delete(self, pid: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT workspace FROM projects WHERE id = ?", (pid,)).fetchone()
            if row is None:
                return False
            self._conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
            self._conn.commit()
        shutil.rmtree(row[0], ignore_errors=True)
        return True

    def _evict_locked(self) -> None:
        """Drop oldest projects beyond capacity. Caller must hold the lock."""
        count = self._conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        overflow = count - self._max
        if overflow <= 0:
            return
        victims = self._conn.execute(
            # rowid, not created_at, orders by actual insertion — immune to
            # same-millisecond timestamp collisions under rapid creation.
            "SELECT id, workspace FROM projects ORDER BY rowid ASC LIMIT ?", (overflow,)
        ).fetchall()
        for vid, workspace in victims:
            self._conn.execute("DELETE FROM projects WHERE id = ?", (vid,))
            shutil.rmtree(workspace, ignore_errors=True)
        self._conn.commit()


def create_store(max_projects: int = MAX_PROJECTS) -> "ProjectStore | SqliteProjectStore":
    """Resolve the configured store: `IACTRANSLATE_STORE` (default `memory`).

    `sqlite` persists to `IACTRANSLATE_DB_PATH` (default `./iactranslate.db`).
    """
    backend = os.getenv("IACTRANSLATE_STORE", "memory").strip().lower()
    if backend == "sqlite":
        db_path = os.getenv("IACTRANSLATE_DB_PATH", "./iactranslate.db")
        return SqliteProjectStore(db_path, max_projects=max_projects)
    return ProjectStore(max_projects=max_projects)
