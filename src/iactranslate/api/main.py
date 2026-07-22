"""FastAPI surface for IaCTranslate.

Flow:
    POST   /projects                   -> create a project
    POST   /projects/{id}/upload       -> upload an RVTools/VMware export
    POST   /projects/{id}/run          -> run the pipeline synchronously (422 on policy denial)
    POST   /projects/{id}/jobs         -> run asynchronously; returns a job id (202)
    GET    /jobs/{job_id}              -> job status (+ project summary when done)
    GET    /audit                      -> recent audit events (event-sourced)
    GET    /policies                   -> available policy rules
    POST   /projects/{id}/assess       -> pre-migration readiness assessment
    POST   /projects/{id}/recommend    -> compare clouds and recommend one
    POST   /projects/{id}/report       -> client-facing executive report (HTML)
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, field_validator
from starlette.requests import Request

from ..agents import build_migration_plan
from ..assessment import assess
from ..confidence import score_plan
from ..config import MAX_UPLOAD_BYTES, cors_origins
from ..exec_report import build_executive_report
from ..normalize import normalize
from ..pipeline import run_pipeline
from ..policy import PolicyViolationError, list_policies
from ..recommend import recommend
from ..sources import list_sources, resolve_source
from ..targets import get_target, list_targets
from ..validation import PlanValidationError
from .audit import AuditLog
from .events import Event, EventBus, EventType
from .jobs import JobQueue
from .store import Project, ProjectStore

logger = logging.getLogger("iactranslate.api")

app = FastAPI(title="IaCTranslate", version="0.1.0")
store = ProjectStore()

# Runtime orchestration layer (single-node realization; swap for Redis/Celery +
# Postgres in production — same interfaces). The pipeline stays a pure function.
bus = EventBus()
jobs = JobQueue(bus)
audit = AuditLog()
audit.attach(bus)

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
    policy: Optional[Dict[str, dict]] = None

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
    if body.policy:
        unknown = set(body.policy) - set(list_policies())
        if unknown:
            raise HTTPException(
                400,
                f"unknown policies {sorted(unknown)} (available: {', '.join(sorted(list_policies()))})",
            )
    project = store.create(
        name=body.name, target=body.target, source=body.source,
        column_map=body.column_map, region=body.region, policy=body.policy,
    )
    logger.info("created project %s (target=%s source=%s)", project.id, project.target, project.source)
    bus.publish(Event(EventType.PROJECT_CREATED, project_id=project.id,
                      detail={"target": project.target, "source": project.source}))
    return _summary(project)


@app.get("/policies")
def policies() -> dict:
    """Available policy rules (name -> description) for building a policy config."""
    return list_policies()


@app.get("/targets")
def targets() -> list:
    """Targets and their advertised capabilities — lets a UI enable features declaratively."""
    return [
        {"name": name, "capabilities": sorted(get_target(name).capabilities)}
        for name in list_targets()
    ]


@app.get("/projects/{pid}")
def get_project(pid: str) -> dict:
    return _summary(_require_project(pid))


@app.delete("/projects/{pid}", status_code=204)
def delete_project(pid: str) -> None:
    if not store.delete(pid):
        raise HTTPException(404, "project not found")
    logger.info("deleted project %s", pid)
    bus.publish(Event(EventType.PROJECT_DELETED, project_id=pid))


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
    bus.publish(Event(EventType.PROJECT_UPLOADED, project_id=project.id, detail={"bytes": total}))
    return _summary(project)


def _execute_run(project: Project) -> None:
    """Run the pipeline for a project and update its status/summary in place.

    Raises the original pipeline exceptions (PlanValidationError /
    PolicyViolationError / ValueError) after marking the project failed — the
    caller maps them to HTTP codes (sync) or to a failed job (async).
    """
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
            policy_config=project.policy,
        )
    except (PlanValidationError, PolicyViolationError, ValueError) as e:
        project.status = "failed"
        if isinstance(e, PlanValidationError):
            project.error = "; ".join(e.issues)
        elif isinstance(e, PolicyViolationError):
            project.error = "; ".join(f"[{v.policy}] {v.message}" for v in e.violations)
        else:
            project.error = str(e)
        raise

    confidence = score_plan(result.plan, result.vms)
    project.project_dir = result.project_dir
    project.zip_path = result.zip_path
    project.status = "completed"
    project.summary = {
        "vm_count": result.plan.vm_count,
        "estimated_monthly_cost_usd": result.plan.total_estimated_monthly_cost_usd,
        "pricing_source": result.plan.pricing_source,
        "right_sized_count": sum(1 for c in result.plan.compute if c.right_sized),
        "confidence": {
            "overall": confidence.overall,
            "level": confidence.level,
            "factor_averages": confidence.factor_averages,
            "low_confidence_count": len(confidence.low_confidence()),
        },
        "policy_warnings": [
            {"policy": v.policy, "message": v.message, "resource": v.resource}
            for v in (result.policy.warnings if result.policy else [])
        ],
        "trace": (
            {"total_ms": result.trace.total_ms,
             "stages": [{"stage": s.stage, "duration_ms": s.duration_ms} for s in result.trace.stages]}
            if result.trace else None
        ),
        "instances": [
            {"vm": c.vm_name, "instance_type": c.instance_type, "tier": c.tier.value}
            for c in result.plan.compute
        ],
    }
    logger.info("project %s generated %d instances", project.id, result.plan.vm_count)


@app.post("/projects/{pid}/run")
def run(pid: str) -> dict:
    """Synchronous run — generates in-request. See POST /jobs for the async path."""
    project = _require_project(pid)
    if project.upload_path is None:
        raise HTTPException(400, "no file uploaded for this project")
    try:
        _execute_run(project)
    except PlanValidationError as e:
        raise HTTPException(422, {"message": "plan failed validation", "issues": e.issues}) from e
    except PolicyViolationError as e:
        msgs = [f"[{v.policy}] {v.message}" for v in e.violations]
        raise HTTPException(422, {"message": "plan violates policy", "violations": msgs}) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _summary(project)


@app.post("/projects/{pid}/jobs", status_code=202)
def create_job(pid: str) -> dict:
    """Asynchronous run — enqueue the pipeline and return a job id to poll."""
    project = _require_project(pid)
    if project.upload_path is None:
        raise HTTPException(400, "no file uploaded for this project")
    project.status = "queued"
    job = jobs.submit(project.id, lambda: _execute_run(project))
    return job.to_dict()


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    data = job.to_dict()
    project = store.get(job.project_id)
    if project is not None:
        data["project"] = _summary(project)
    return data


@app.get("/audit")
def get_audit(project_id: Optional[str] = None, limit: int = 100) -> list:
    """Recent audit events (newest first), optionally scoped to one project."""
    return [e.to_dict() for e in audit.recent(project_id=project_id, limit=min(limit, 1000))]


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


@app.post("/projects/{pid}/assess")
def assess_estate(pid: str) -> dict:
    project = _require_project(pid)
    if project.upload_path is None:
        raise HTTPException(400, "no file uploaded for this project")

    vms = _parse_inventory(project)
    src = resolve_source(str(project.upload_path), project.source)
    a = assess(vms, project_name=project.name, source_platform=src.name)
    return a.model_dump(mode="json")


@app.post("/projects/{pid}/report", response_class=HTMLResponse)
def executive_report(pid: str, include_recommendation: bool = True) -> HTMLResponse:
    project = _require_project(pid)
    if project.upload_path is None:
        raise HTTPException(400, "no file uploaded for this project")

    vms = _parse_inventory(project)
    src = resolve_source(str(project.upload_path), project.source)
    target = get_target(project.target)
    plan = build_migration_plan(
        vms, project_name=project.name, target=target, region=project.region,
        source_platform=getattr(src, "source_platform", src.name),
    )
    rec = recommend(vms) if include_recommendation else None
    return HTMLResponse(build_executive_report(plan, vms, recommendation=rec))


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
