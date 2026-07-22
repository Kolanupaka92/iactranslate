"""In-memory project/job store for the API (swap for Postgres + S3 later).

Each project gets an isolated temp workspace; uploaded files and generated output
live there. The store is thread-safe (FastAPI runs sync endpoints in a threadpool)
and capacity-bounded — the oldest projects are evicted (and their temp workspaces
deleted) beyond MAX_PROJECTS so disk usage can't grow without limit.
"""
from __future__ import annotations

import shutil
import tempfile
import threading
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
    ) -> Project:
        pid = uuid.uuid4().hex[:12]
        project = Project(
            id=pid, name=name, target=target, source=source,
            column_map=column_map, region=region, policy=policy,
        )
        with self._lock:
            self._projects[pid] = project
            self._evict_locked()
        return project

    def get(self, pid: str) -> Optional[Project]:
        with self._lock:
            return self._projects.get(pid)

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
