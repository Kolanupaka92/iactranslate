"""FastAPI surface for IaCTranslate.

Flow:
    POST   /projects                   -> create a project
    POST   /projects/{id}/upload       -> upload an RVTools/VMware export
    POST   /projects/{id}/run          -> run the pipeline
    POST   /projects/{id}/recommend    -> compare clouds and recommend one
    GET    /projects/{id}              -> status + summary
    GET    /projects/{id}/download     -> download the Terraform project ZIP
    DELETE /projects/{id}              -> delete a project + its workspace
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, field_validator
from starlette.requests import Request

from ..config import MAX_UPLOAD_BYTES, cors_origins
from ..normalize import normalize
from ..pipeline import run_pipeline
from ..recommend import recommend
from ..sources import list_sources, resolve_source
from ..targets import list_targets
from ..validation import PlanValidationError
from .store import Project, ProjectStore

logger = logging.getLogger("iactranslate.api")

app = FastAPI(title="IaCTranslate", version="0.1.0")
store = ProjectStore()

_ALLOWED_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".csv"}
_NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,128}$")
_UPLOAD_CHUNK = 1024 * 1024  # 1 MiB

_origins = cors_origins()
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )


class CreateProject(BaseModel):
    name: str
    target: str = "aws"
    source: str = "auto"
    column_map: Optional[Dict[str, str]] = None
    region: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        v = v.strip()
        if not _NAME_RE.match(v):
            raise ValueError("name must be 1-128 chars of letters, digits, space, '.', '_', '-'")
        return v


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    # Never leak internals/tracebacks to clients; log server-side with context.
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


def _summary(project: Project) -> dict:
    data = {
        "id": project.id,
        "name": project.name,
        "target": project.target,
        "source": project.source,
        "region": project.region,
        "status": project.status,
    }
    if project.error:
        data["error"] = project.error
    if project.summary:
        data["result"] = project.summary
    return data


def _require_project(pid: str) -> Project:
    project = store.get(pid)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


def _parse_inventory(project: Project) -> List:
    """Parse + normalize the project's upload, mapping any parser failure to 400."""
    try:
        src = resolve_source(str(project.upload_path), project.source)
        vms = normalize(src.parse(str(project.upload_path), column_map=project.column_map))
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 — malformed upload must not 500/leak a traceback
        logger.warning("failed to parse uploaded inventory for %s", project.id, exc_info=True)
        raise HTTPException(400, "could not parse the uploaded file as an inventory export") from None
    if not vms:
        raise HTTPException(400, "no workloads found in the uploaded file")
    return vms


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/projects", status_code=201)
def create_project(body: CreateProject) -> dict:
    if body.target not in list_targets():
        raise HTTPException(400, f"target '{body.target}' not supported (available: {', '.join(list_targets())})")
    if body.source not in ("auto", *list_sources()):
        raise HTTPException(400, f"source '{body.source}' not supported (available: auto, {', '.join(list_sources())})")
    project = store.create(
        name=body.name, target=body.target, source=body.source,
        column_map=body.column_map, region=body.region,
    )
    logger.info("created project %s (target=%s source=%s)", project.id, project.target, project.source)
    return _summary(project)


@app.get("/projects/{pid}")
def get_project(pid: str) -> dict:
    return _summary(_require_project(pid))


@app.delete("/projects/{pid}", status_code=204)
def delete_project(pid: str) -> None:
    if not store.delete(pid):
        raise HTTPException(404, "project not found")
    logger.info("deleted project %s", pid)


@app.post("/projects/{pid}/upload")
async def upload(pid: str, file: UploadFile) -> dict:
    project = _require_project(pid)

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(400, f"unsupported file type '{suffix}' (expected .xlsx or .csv)")

    dest = project.workspace / f"upload{suffix}"
    total = 0
    try:
        with open(dest, "wb") as out:
            while chunk := await file.read(_UPLOAD_CHUNK):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise

    project.upload_path = dest
    project.status = "uploaded"
    logger.info("project %s uploaded %d bytes", project.id, total)
    return _summary(project)


@app.post("/projects/{pid}/run")
def run(pid: str) -> dict:
    project = _require_project(pid)
    if project.upload_path is None:
        raise HTTPException(400, "no file uploaded for this project")

    out_dir = project.workspace / "project"
    try:
        result = run_pipeline(
            input_path=str(project.upload_path),
            project_name=project.name,
            out_dir=str(out_dir),
            target=project.target,
            source=project.source,
            column_map=project.column_map,
            region=project.region,
            make_zip=True,
        )
    except PlanValidationError as e:
        project.status = "failed"
        project.error = "; ".join(e.issues)
        raise HTTPException(422, {"message": "plan failed validation", "issues": e.issues}) from e
    except ValueError as e:
        project.status = "failed"
        project.error = str(e)
        raise HTTPException(400, str(e)) from e

    project.project_dir = result.project_dir
    project.zip_path = result.zip_path
    project.status = "completed"
    project.summary = {
        "vm_count": result.plan.vm_count,
        "estimated_monthly_cost_usd": result.plan.total_estimated_monthly_cost_usd,
        "pricing_source": result.plan.pricing_source,
        "right_sized_count": sum(1 for c in result.plan.compute if c.right_sized),
        "instances": [
            {"vm": c.vm_name, "instance_type": c.instance_type, "tier": c.tier.value}
            for c in result.plan.compute
        ],
    }
    logger.info("project %s generated %d instances", project.id, result.plan.vm_count)
    return _summary(project)


@app.post("/projects/{pid}/recommend")
def recommend_cloud(pid: str) -> dict:
    project = _require_project(pid)
    if project.upload_path is None:
        raise HTTPException(400, "no file uploaded for this project")

    vms = _parse_inventory(project)
    try:
        return recommend(vms).model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/projects/{pid}/download")
def download(pid: str) -> FileResponse:
    project = _require_project(pid)
    if not project.zip_path or not Path(project.zip_path).exists():
        raise HTTPException(409, "project has not been generated yet; call /run first")
    return FileResponse(
        path=str(project.zip_path),
        media_type="application/zip",
        filename=f"{project.name}.zip",
    )
