"""FastAPI surface for IaCTranslate.

Flow:
    POST /projects                     -> create a project
    POST /projects/{id}/upload         -> upload an RVTools/VMware export
    POST /projects/{id}/run            -> run the pipeline
    GET  /projects/{id}                -> status + summary
    GET  /projects/{id}/download       -> download the Terraform project ZIP
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..pipeline import run_pipeline
from ..validation import PlanValidationError
from .store import Project, ProjectStore

app = FastAPI(title="IaCTranslate", version="0.1.0")
store = ProjectStore()

_ALLOWED_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".csv"}


class CreateProject(BaseModel):
    name: str
    target: str = "aws"
    region: str = "us-east-1"


def _summary(project: Project) -> dict:
    data = {
        "id": project.id,
        "name": project.name,
        "target": project.target,
        "region": project.region,
        "status": project.status,
    }
    if project.error:
        data["error"] = project.error
    if project.summary:
        data["result"] = project.summary
    return data


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/projects", status_code=201)
def create_project(body: CreateProject) -> dict:
    if body.target != "aws":
        raise HTTPException(400, f"target '{body.target}' not supported (MVP: aws)")
    project = store.create(name=body.name, target=body.target, region=body.region)
    return _summary(project)


@app.get("/projects/{pid}")
def get_project(pid: str) -> dict:
    project = store.get(pid)
    if project is None:
        raise HTTPException(404, "project not found")
    return _summary(project)


@app.post("/projects/{pid}/upload")
async def upload(pid: str, file: UploadFile) -> dict:
    project = store.get(pid)
    if project is None:
        raise HTTPException(404, "project not found")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(400, f"unsupported file type '{suffix}' (expected .xlsx or .csv)")

    dest = project.workspace / f"upload{suffix}"
    dest.write_bytes(await file.read())
    project.upload_path = dest
    project.status = "uploaded"
    return _summary(project)


@app.post("/projects/{pid}/run")
def run(pid: str) -> dict:
    project = store.get(pid)
    if project is None:
        raise HTTPException(404, "project not found")
    if project.upload_path is None:
        raise HTTPException(400, "no file uploaded for this project")

    out_dir = project.workspace / "project"
    try:
        result = run_pipeline(
            input_path=str(project.upload_path),
            project_name=project.name,
            out_dir=str(out_dir),
            region=project.region,
            make_zip=True,
        )
    except PlanValidationError as e:
        project.status = "failed"
        project.error = "; ".join(e.issues)
        raise HTTPException(422, {"message": "plan failed validation", "issues": e.issues})
    except ValueError as e:
        project.status = "failed"
        project.error = str(e)
        raise HTTPException(400, str(e))

    project.project_dir = result.project_dir
    project.zip_path = result.zip_path
    project.status = "completed"
    project.summary = {
        "vm_count": result.plan.vm_count,
        "estimated_monthly_cost_usd": result.plan.total_estimated_monthly_cost_usd,
        "instances": [
            {"vm": c.vm_name, "instance_type": c.instance_type, "tier": c.tier.value}
            for c in result.plan.compute
        ],
    }
    return _summary(project)


@app.get("/projects/{pid}/download")
def download(pid: str) -> FileResponse:
    project = store.get(pid)
    if project is None:
        raise HTTPException(404, "project not found")
    if not project.zip_path or not Path(project.zip_path).exists():
        raise HTTPException(409, "project has not been generated yet; call /run first")
    return FileResponse(
        path=str(project.zip_path),
        media_type="application/zip",
        filename=f"{project.name}.zip",
    )
