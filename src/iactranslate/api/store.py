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
  ADR 0025. It does **not** close the "generated files survive a node being
  recycled" gap: each project's workspace (uploads, rendered output) is still
  a local temp directory. Durable *file* storage (S3/GCS) is the natural next
  step once a real object-storage backend is available to build against.

Each project gets an isolated temp workspace regardless of which store is
active; uploaded files and generated output live there. Both stores are
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
from typing import Optional

from ..config import MAX_PROJECTS


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
    status: str = "created"  # created -> uploaded -> completed / failed
    workspace: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="iactranslate_")))
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
    ) -> Project:
        pid = uuid.uuid4().hex[:12]
        project = Project(
            id=pid, name=name, target=target, source=source,
            column_map=column_map, region=region, policy=policy, provider=provider,
        )
        with self._lock:
            self._projects[pid] = project
            self._evict_locked()
        return project

    def get(self, pid: str) -> Optional[Project]:
        with self._lock:
            return self._projects.get(pid)

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

    Project *metadata* survives a process restart; each project's workspace
    (uploads, rendered output) is still a local temp directory — see the
    module docstring for that boundary.
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
            created_at REAL NOT NULL
        )
    """
    _COLUMNS = (
        "id", "name", "target", "source", "column_map", "region", "policy",
        "provider", "status", "workspace", "upload_path", "project_dir",
        "zip_path", "error", "summary", "created_at",
    )

    def __init__(self, db_path: str, max_projects: int = MAX_PROJECTS) -> None:
        self._max = max_projects
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(self._SCHEMA)
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
    ) -> Project:
        pid = uuid.uuid4().hex[:12]
        workspace = Path(tempfile.mkdtemp(prefix="iactranslate_"))
        with self._lock:
            self._conn.execute(
                "INSERT INTO projects (id, name, target, source, column_map, region, policy, "
                "provider, status, workspace, upload_path, project_dir, zip_path, error, summary, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, NULL, NULL, NULL, NULL, NULL, ?)",
                (pid, name, target, source,
                 json.dumps(column_map) if column_map else None, region,
                 json.dumps(policy) if policy else None, provider, str(workspace), time.time()),
            )
            self._conn.commit()
            self._evict_locked()
        return Project(
            id=pid, name=name, target=target, source=source, column_map=column_map,
            region=region, policy=policy, provider=provider, workspace=workspace,
        )

    def get(self, pid: str) -> Optional[Project]:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {', '.join(self._COLUMNS)} FROM projects WHERE id = ?", (pid,)
            ).fetchone()
        return self._row_to_project(row) if row else None

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
