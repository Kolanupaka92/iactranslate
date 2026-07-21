"""In-memory project/job store for the API (swap for Postgres + S3 later).

Each project gets an isolated temp workspace; uploaded files and generated
output live there and are cleaned up when the project is deleted.
"""
from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


@dataclass
class Project:
    id: str
    name: str
    target: str = "aws"
    region: str = "us-east-1"
    status: str = "created"  # created -> uploaded -> completed / failed
    workspace: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="iactranslate_")))
    upload_path: Optional[Path] = None
    project_dir: Optional[Path] = None
    zip_path: Optional[Path] = None
    error: Optional[str] = None
    summary: Optional[dict] = None


class ProjectStore:
    def __init__(self) -> None:
        self._projects: Dict[str, Project] = {}

    def create(self, name: str, target: str = "aws", region: str = "us-east-1") -> Project:
        pid = uuid.uuid4().hex[:12]
        project = Project(id=pid, name=name, target=target, region=region)
        self._projects[pid] = project
        return project

    def get(self, pid: str) -> Optional[Project]:
        return self._projects.get(pid)

    def delete(self, pid: str) -> bool:
        project = self._projects.pop(pid, None)
        if project is None:
            return False
        shutil.rmtree(project.workspace, ignore_errors=True)
        return True
