"""FastAPI surface for IaCTranslate.

Flow:
    POST   /projects                   -> create a project
    POST   /projects/{id}/upload       -> upload an RVTools/VMware export
    POST   /projects/{id}/run          -> run the pipeline synchronously (422 on policy denial)
    POST   /projects/{id}/jobs         -> run asynchronously; returns a job id (202)
    GET    /jobs/{job_id}              -> job status (+ project summary when done)
    GET    /audit                      -> recent audit events (event-sourced)
    GET    /metrics                    -> Prometheus metrics (unauthenticated, aggregate only)
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
import os
import re
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, field_validator
from starlette.requests import Request

from ..agents import build_migration_plan
from ..agents.providers import get_provider
from ..assessment import assess
from ..confidence import score_plan
from ..config import MAX_UPLOAD_BYTES, cors_origins
from ..costing import estimate_costs
from ..crypto import (
    EncryptionUnavailable,
    decrypt_bytes,
    is_encrypted,
    write_file,
)
from ..exec_report import build_executive_report
from ..normalize import normalize
from ..observability import configure_logging, correlation_id, get_logger, new_correlation_id
from ..pipeline import run_pipeline
from ..policy import PolicyViolationError, list_policies
from ..recommend import recommend
from ..sources import list_sources, resolve_source
from ..targets import get_target, list_targets
from ..validation import PlanValidationError
from .accounts import (
    SESSION_COOKIE,
    EmailTaken,
    InvalidCredentials,
    User,
    create_account_store,
)
from .audit import create_audit_log
from .auth import require_api_key
from .delivery import deliver_reset_link
from .events import Event, EventBus, EventType
from .jobs import JobQueue
from .metrics import Metrics
from .ratelimit import limit_auth, limit_reads, limit_writes
from .roles import Membership, Role, at_least, parse_role
from .store import Project, create_store

logger = get_logger("iactranslate.api")

# Install the JSON handler once, at import of the app module. Doing it here
# rather than in `iactranslate/__init__` keeps library importers free of our
# logging config (see observability.configure_logging).
configure_logging()

app = FastAPI(title="IaCTranslate", version="0.1.0")

# Routes are declared on a router and mounted twice: under `/v1`, and at the
# legacy unprefixed paths. Adding the prefix now costs nothing; adding it after
# clients exist means breaking every one of them, and the first breaking change
# to an unversioned API is the expensive one. The unprefixed mount keeps the web
# app and any existing caller working, and marks itself deprecated so a client
# can discover the move without reading a changelog.
#
# `/health` and `/metrics` stay unversioned deliberately — probes and scrapers
# point at fixed paths and are infrastructure, not API surface.
router = APIRouter()
store = create_store()
accounts = create_account_store()  # None unless IACTRANSLATE_AUTH=session

# Runtime orchestration layer (single-node realization; swap for Redis/Celery +
# Postgres in production — same interfaces). The pipeline stays a pure function.
bus = EventBus()
jobs = JobQueue(bus)
audit = create_audit_log()
audit.attach(bus)
metrics = Metrics()
metrics.attach(bus)

# Who may do what on which project. Separate from the project store because
# a grant belongs to neither the project nor the user alone (see roles.py).
memberships = Membership()

_ALLOWED_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".csv"}
_NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,128}$")
_UPLOAD_CHUNK = 1024 * 1024  # 1 MiB

_origins = cors_origins()
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        # Required for the session cookie to cross origins (web app and API are
        # separate origins). Browsers reject credentialed requests against a
        # wildcard origin, so IACTRANSLATE_CORS_ORIGINS must name real origins
        # rather than "*" whenever IACTRANSLATE_AUTH=session.
        allow_credentials=True,
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
    provider: str = "rule"

    @field_validator("provider")
    @classmethod
    def _valid_provider(cls, v: str) -> str:
        if v not in {"rule", "anthropic"}:
            raise ValueError("provider must be 'rule' or 'anthropic'")
        return v

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        v = v.strip()
        if not _NAME_RE.match(v):
            raise ValueError("name must be 1-128 chars of letters, digits, space, '.', '_', '-'")
        return v


@app.middleware("http")
async def _correlate(request: Request, call_next):
    """Give every request an id, and log its outcome exactly once.

    An inbound `X-Request-Id` is honoured so a trace started at the load
    balancer or the web app stays one trace through this service; otherwise we
    mint one. It is echoed back on the response so a user reporting a problem
    can quote the id from their browser's network tab, and every log record
    emitted while handling the request carries it automatically.

    The id is accepted only as an opaque token and truncated: it is attacker
    -controlled input that lands in log files, and unbounded values are a log
    -injection and log-volume problem.
    """
    incoming = request.headers.get("X-Request-Id", "").strip()
    cid = "".join(c for c in incoming if c.isalnum() or c in "-_")[:64] or new_correlation_id()
    token = correlation_id.set(cid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request failed",
            extra={
                "http.method": request.method,
                "http.route": request.url.path,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        correlation_id.reset(token)
        raise
    duration = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-Id"] = cid
    # /health and /metrics are polled continuously by probes and scrapers;
    # logging them at INFO would bury real traffic in heartbeat noise.
    noisy = request.url.path in ("/health", "/metrics")
    logger.log(
        logging.DEBUG if noisy else logging.INFO,
        "request",
        extra={
            "http.method": request.method,
            "http.route": request.url.path,
            "http.status_code": response.status_code,
            "duration_ms": duration,
        },
    )
    correlation_id.reset(token)
    return response


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Baseline hardening headers on every response.

    These matter most on `/projects/{id}/report`, which returns HTML that is
    rendered in the user's browser: `nosniff` and a restrictive frame policy
    keep a generated report from being reinterpreted or embedded elsewhere.
    HSTS is only sent over https — asserting it on a plaintext dev server
    would pin localhost to https in the developer's browser.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


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
        "provider": project.provider,
        "status": project.status,
    }
    if project.error:
        data["error"] = project.error
    if project.summary:
        data["result"] = project.summary
    return data


def current_user(request: Request) -> Optional[User]:
    """Resolve the session cookie to a user.

    Returns None in single-tenant mode (`IACTRANSLATE_AUTH` unset), where every
    project has `owner_id = None` and the ownership check below degenerates to
    "everything belongs to the one operator" — the historical behavior.
    """
    if accounts is None:
        return None
    user = accounts.user_for_session(request.cookies.get(SESSION_COOKIE, ""))
    if user is None:
        raise HTTPException(401, "not signed in")
    return user


def _require_project(pid: str, user: Optional[User], role: Role = Role.VIEWER) -> Project:
    """Fetch a project the caller may access at `role` or above.

    **No access is 404, never 403.** A 403 confirms the id exists, letting an
    attacker enumerate other tenants' projects (ADR 0027). **Insufficient
    access is 403**, because a caller who already holds *some* role knows the
    project exists — 404 would tell them nothing they do not know and would
    make a permissions problem look like a missing resource.

    In single-tenant mode (`IACTRANSLATE_AUTH` unset) every project has
    `owner_id = None` and the one operator is implicitly an admin, which keeps
    the historical behaviour exactly.
    """
    project = store.get(pid)
    if project is None:
        raise HTTPException(404, "project not found")
    if user is None:
        # Single-tenant: the sole operator administers everything. A project
        # that *does* have an owner must not be reachable without a session.
        if project.owner_id is not None:
            raise HTTPException(404, "project not found")
        return project

    held = memberships.role_for(pid, user.id, project.owner_id)
    if held is None:
        raise HTTPException(404, "project not found")
    if not at_least(held, role):
        raise HTTPException(
            403,
            f"this action requires the '{role.value}' role; you have '{held.value}'",
        )
    return project


@contextmanager
def _plaintext_upload(project: Project):
    """Yield a filesystem path the parsers can read.

    The `Source` protocol is path-based — `detect(path)` sniffs headers and
    `parse(path)` hands the path to pandas — so an encrypted upload has to touch
    the filesystem in the clear to be read at all. This is the single place that
    happens, so the exposure is one auditable window rather than scattered.

    The plaintext copy is created 0600 inside the project's own workspace (same
    volume, so no spill onto a shared `/tmp` with different permissions or a
    different retention policy) and is removed in a `finally`. When encryption
    is off there is no copy — the original path is yielded unchanged.

    Eliminating this window entirely means teaching every source to parse from a
    buffer; that is tracked, not done here.
    """
    path = Path(str(project.upload_path))
    blob = path.read_bytes()
    if not is_encrypted(blob):
        yield str(path)
        return
    plaintext = decrypt_bytes(blob)
    tmp = path.with_name(f".plain-{secrets.token_hex(8)}{path.suffix}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, plaintext)
        os.close(fd)
        fd = None
        yield str(tmp)
    finally:
        if fd is not None:
            os.close(fd)
        tmp.unlink(missing_ok=True)


def _parse_inventory(project: Project) -> List:
    """Parse + normalize the project's upload, mapping any parser failure to 400."""
    try:
        with _plaintext_upload(project) as readable:
            src = resolve_source(readable, project.source)
            vms = normalize(src.parse(readable, column_map=project.column_map))
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


class Credentials(BaseModel):
    email: str
    password: str


def _require_accounts():
    if accounts is None:
        raise HTTPException(404, "accounts are not enabled on this deployment")
    return accounts


def _set_session_cookie(response: JSONResponse, token: str) -> None:
    """httponly blocks JS access (XSS can't steal it); samesite=lax blocks
    cross-site POSTs (CSRF) while still allowing top-level navigations, which
    is what makes the download/report links work."""
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("IACTRANSLATE_COOKIE_SECURE", "1") != "0",
        max_age=14 * 24 * 3600,
        path="/",
    )


@router.post("/auth/register", status_code=201)
def register(body: Credentials, request: Request) -> JSONResponse:
    # Throttled by IP and by target email — see ratelimit.limit_auth.
    limit_auth(request, body.email)
    accts = _require_accounts()
    try:
        user = accts.create_user(body.email, body.password)
    except EmailTaken:
        # Same shape as a validation failure — do not confirm which emails exist.
        raise HTTPException(400, "could not create that account") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    response = JSONResponse(status_code=201, content={"id": user.id, "email": user.email})
    _set_session_cookie(response, accts.create_session(user.id))
    logger.info("registered user %s", user.id)
    return response


@router.post("/auth/login")
def login(body: Credentials, request: Request) -> JSONResponse:
    # The most attackable endpoint in the product: it accepts a password and
    # reports whether it was right. Per-email throttling matters as much as
    # per-IP here, since credential stuffing hits one account from many hosts.
    limit_auth(request, body.email)
    accts = _require_accounts()
    try:
        user = accts.authenticate(body.email, body.password)
    except InvalidCredentials:
        raise HTTPException(401, "invalid email or password") from None
    response = JSONResponse(content={"id": user.id, "email": user.email})
    _set_session_cookie(response, accts.create_session(user.id))
    return response


@router.post("/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    if accounts is not None:
        accounts.delete_session(request.cookies.get(SESSION_COOKIE, ""))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


class ForgotPassword(BaseModel):
    email: str


class ResetPassword(BaseModel):
    token: str
    password: str


class ChangePassword(BaseModel):
    current_password: str
    new_password: str


@router.post("/auth/forgot-password", status_code=202)
def forgot_password(body: ForgotPassword, request: Request) -> dict:
    """Start a reset. Always 202, whether or not the account exists.

    Reporting "no such account" here would turn this endpoint into a free
    account-enumeration oracle, undoing the care taken in login and register.
    """
    limit_auth(request, body.email)
    accts = _require_accounts()
    user = accts.get_user_by_email(body.email)
    if user is not None:
        deliver_reset_link(user.email, accts.create_reset_token(user.id))
    return {"status": "if that account exists, a reset link has been sent"}


@router.post("/auth/reset-password", status_code=204)
def reset_password(body: ResetPassword, request: Request) -> Response:
    limit_auth(request)
    accts = _require_accounts()
    user_id = accts.consume_reset_token(body.token)  # single use, even if expired
    if user_id is None:
        raise HTTPException(400, "this reset link is invalid or has expired")
    try:
        accts.set_password(user_id, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    # A reset is how someone recovers a *compromised* account, so every existing
    # session must die — including the attacker's.
    accts.delete_sessions_for_user(user_id)
    logger.info("password reset completed for user %s", user_id)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post("/auth/change-password", status_code=204)
def change_password(
    body: ChangePassword, request: Request, user: Optional[User] = Depends(current_user)
) -> Response:
    """Change your own password. Requires the current one."""
    limit_auth(request)
    accts = _require_accounts()
    if user is None:
        raise HTTPException(401, "not signed in")
    try:
        accts.authenticate(user.email, body.current_password)
    except InvalidCredentials:
        raise HTTPException(403, "current password is incorrect") from None
    try:
        accts.set_password(user.id, body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    # Drop every session, then re-issue one for this caller: anyone else holding
    # a stolen cookie is signed out, while the person who just changed their
    # password isn't bounced to the login screen for doing the right thing.
    accts.delete_sessions_for_user(user.id)
    response = Response(status_code=204)
    _set_session_cookie(response, accts.create_session(user.id))
    logger.info("password changed for user %s", user.id)
    return response


@router.get("/auth/me")
def whoami(user: Optional[User] = Depends(current_user)) -> dict:
    if user is None:
        return {"authenticated": False, "multi_tenant": False}
    return {"authenticated": True, "multi_tenant": True, "id": user.id, "email": user.email}


@router.get("/projects", dependencies=[Depends(limit_reads)])
def list_projects(user: Optional[User] = Depends(current_user)) -> list:
    """Projects the caller owns, plus any shared with them.

    A grant the recipient cannot see is a grant that does not work: they would
    have to be told the project id out of band before they could use it.
    """
    owned = store.list_for_owner(user.id if user else None)
    if user is None:
        return [_summary(p) for p in owned]

    out = [{**_summary(p), "role": Role.ADMIN.value, "owner": True} for p in owned]
    seen = {p.id for p in owned}
    for pid in memberships.projects_for(user.id):
        if pid in seen:
            continue
        project = store.get(pid)
        if project is None:
            continue  # granted then deleted; nothing to show
        role = memberships.role_for(pid, user.id, project.owner_id)
        out.append({**_summary(project), "role": role.value if role else None, "owner": False})
    return out


@router.post("/projects", status_code=201, dependencies=[Depends(require_api_key), Depends(limit_writes)])
def create_project(body: CreateProject, user: Optional[User] = Depends(current_user)) -> dict:
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
        provider=body.provider, owner_id=user.id if user else None,
    )
    logger.info("created project %s (target=%s source=%s)", project.id, project.target, project.source)
    bus.publish(Event(EventType.PROJECT_CREATED, project_id=project.id,
                      detail={"target": project.target, "source": project.source}))
    return _summary(project)


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> PlainTextResponse:
    """Prometheus scrape endpoint (counters + an in-flight gauge).

    Unauthenticated like `/health`: scrapers do not send bearer tokens, and the
    payload is aggregate counts only — no project names, paths, or inventory.
    """
    return PlainTextResponse(
        metrics.render(jobs_in_flight=jobs.in_flight()),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/policies")
def policies() -> dict:
    """Available policy rules (name -> description) for building a policy config."""
    return list_policies()


@router.get("/targets")
def targets() -> list:
    """Targets and their advertised capabilities — lets a UI enable features declaratively."""
    return [
        {"name": name, "capabilities": sorted(get_target(name).capabilities)}
        for name in list_targets()
    ]


@router.get("/projects/{pid}", dependencies=[Depends(require_api_key), Depends(limit_reads)])
def get_project(pid: str, user: Optional[User] = Depends(current_user)) -> dict:
    return _summary(_require_project(pid, user, Role.VIEWER))


@router.delete("/projects/{pid}", status_code=204, dependencies=[Depends(require_api_key), Depends(limit_writes)])
def delete_project(pid: str, user: Optional[User] = Depends(current_user)) -> None:
    # Ownership is checked *before* deleting — otherwise any signed-in user
    # could destroy another tenant's project by guessing its id.
    _require_project(pid, user, Role.ADMIN)
    if not store.delete(pid):
        raise HTTPException(404, "project not found")
    memberships.drop_project(pid)
    logger.info("deleted project %s", pid)
    bus.publish(Event(EventType.PROJECT_DELETED, project_id=pid))


@router.post("/projects/{pid}/upload", dependencies=[Depends(require_api_key), Depends(limit_writes)])
async def upload(pid: str, file: UploadFile, user: Optional[User] = Depends(current_user)) -> dict:
    project = _require_project(pid, user, Role.EDITOR)

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(400, f"unsupported file type '{suffix}' (expected .xlsx or .csv)")

    dest = project.workspace / f"upload{suffix}"
    total = 0
    # Buffered rather than streamed to disk because the bytes are encrypted as a
    # unit (one authenticated blob per file). MAX_UPLOAD_BYTES already bounds
    # this — it is the same ceiling the streaming version enforced, and the
    # chunk loop still stops the moment it is crossed, so an oversized upload is
    # never fully buffered.
    chunks: List[bytes] = []
    try:
        while chunk := await file.read(_UPLOAD_CHUNK):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
            chunks.append(chunk)
        write_file(dest, b"".join(chunks))
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except EncryptionUnavailable as e:
        # Configured-but-broken encryption must not degrade to plaintext.
        dest.unlink(missing_ok=True)
        logger.error("upload rejected: encryption unavailable", extra={"project_id": project.id})
        raise HTTPException(500, str(e)) from e
    finally:
        chunks.clear()

    project.upload_path = dest
    project.status = "uploaded"
    store.save(project)
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
        with _plaintext_upload(project) as readable:
            result = run_pipeline(
                input_path=readable,
                project_name=project.name,
                out_dir=str(out_dir),
                target=project.target,
                source=project.source,
                column_map=project.column_map,
                region=project.region,
                provider=get_provider(get_target(project.target), name=project.provider),
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
        store.save(project)
        raise

    confidence = score_plan(result.plan, result.vms)
    project.project_dir = result.project_dir
    project.zip_path = result.zip_path
    project.status = "completed"
    _costs = estimate_costs(result.plan)
    project.summary = {
        "vm_count": result.plan.vm_count,
        # The itemized total, matching the executive report and the generated
        # README in the same bundle (ADR 0039). `compute_monthly_cost_usd` is
        # kept alongside it so a caller can still see the instance-only figure.
        "estimated_monthly_cost_usd": _costs.total,
        "compute_monthly_cost_usd": _costs.compute,
        "cost_breakdown": _costs.model_dump(),
        "pricing_source": result.plan.pricing_source,
        "right_sized_count": sum(1 for c in result.plan.compute if c.right_sized),
        "provider_requested": project.provider,
        "provider_used": result.plan.provider_used,
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
    store.save(project)
    logger.info("project %s generated %d instances", project.id, result.plan.vm_count)


@router.post("/projects/{pid}/run", dependencies=[Depends(require_api_key), Depends(limit_writes)])
def run(pid: str, user: Optional[User] = Depends(current_user)) -> dict:
    """Synchronous run — generates in-request. See POST /jobs for the async path."""
    project = _require_project(pid, user, Role.EDITOR)
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


@router.post("/projects/{pid}/jobs", status_code=202, dependencies=[Depends(require_api_key), Depends(limit_writes)])
def create_job(pid: str, user: Optional[User] = Depends(current_user)) -> dict:
    """Asynchronous run — enqueue the pipeline and return a job id to poll."""
    project = _require_project(pid, user, Role.EDITOR)
    if project.upload_path is None:
        raise HTTPException(400, "no file uploaded for this project")
    project.status = "queued"
    store.save(project)
    job = jobs.submit(project.id, lambda: _execute_run(project))
    return job.to_dict()


@router.get("/jobs/{job_id}", dependencies=[Depends(require_api_key), Depends(limit_reads)])
def get_job(job_id: str, user: Optional[User] = Depends(current_user)) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    # A job id is a handle to a project, so it inherits that project's access
    # check — otherwise a guessed job id would expose another tenant's summary.
    _require_project(job.project_id, user)
    data = job.to_dict()
    project = store.get(job.project_id)
    if project is not None:
        data["project"] = _summary(project)
    return data


@router.get("/audit", dependencies=[Depends(require_api_key), Depends(limit_reads)])
def get_audit(
    project_id: Optional[str] = None,
    limit: int = 100,
    user: Optional[User] = Depends(current_user),
) -> list:
    """Recent audit events (newest first), scoped to the caller's own projects.

    The unfiltered trail names every tenant's project ids and activity, so in
    multi-tenant mode it is filtered to projects the caller owns rather than
    returned whole.
    """
    if project_id is not None:
        _require_project(project_id, user)
        return [e.to_dict() for e in audit.recent(project_id=project_id, limit=min(limit, 1000))]

    events = audit.recent(limit=min(limit, 1000))
    if user is None:
        return [e.to_dict() for e in events]  # single-tenant: one operator, one trail
    owned = {p.id for p in store.list_for_owner(user.id)}
    return [e.to_dict() for e in events if e.project_id in owned]


@router.post("/projects/{pid}/recommend", dependencies=[Depends(require_api_key), Depends(limit_writes)])
def recommend_cloud(pid: str, user: Optional[User] = Depends(current_user)) -> dict:
    # A recommendation is read-only analysis, but it builds a plan per cloud —
    # five full pipelines of real CPU — so it is gated as work, not as a read.
    project = _require_project(pid, user, Role.EDITOR)
    if project.upload_path is None:
        raise HTTPException(400, "no file uploaded for this project")

    vms = _parse_inventory(project)
    try:
        return recommend(vms).model_dump()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/projects/{pid}/assess", dependencies=[Depends(require_api_key), Depends(limit_writes)])
def assess_estate(pid: str, user: Optional[User] = Depends(current_user)) -> dict:
    project = _require_project(pid, user, Role.EDITOR)
    if project.upload_path is None:
        raise HTTPException(400, "no file uploaded for this project")

    vms = _parse_inventory(project)
    # `resolve_source` sniffs the file to auto-detect, so it needs plaintext too.
    with _plaintext_upload(project) as readable:
        src = resolve_source(readable, project.source)
    a = assess(vms, project_name=project.name, source_platform=src.name)
    return a.model_dump(mode="json")


@router.post(
    "/projects/{pid}/report",
    response_class=HTMLResponse,
    dependencies=[Depends(require_api_key), Depends(limit_writes)],
)
def executive_report(pid: str, include_recommendation: bool = True,
                     user: Optional[User] = Depends(current_user)) -> HTMLResponse:
    project = _require_project(pid, user, Role.VIEWER)
    if project.upload_path is None:
        raise HTTPException(400, "no file uploaded for this project")

    vms = _parse_inventory(project)
    with _plaintext_upload(project) as readable:
        src = resolve_source(readable, project.source)
    target = get_target(project.target)
    plan = build_migration_plan(
        vms, project_name=project.name, target=target, region=project.region,
        source_platform=getattr(src, "source_platform", src.name),
    )
    rec = recommend(vms) if include_recommendation else None
    return HTMLResponse(build_executive_report(plan, vms, recommendation=rec))


@router.get("/projects/{pid}/download", dependencies=[Depends(require_api_key), Depends(limit_reads)])
def download(pid: str, user: Optional[User] = Depends(current_user)) -> FileResponse:
    project = _require_project(pid, user, Role.VIEWER)
    if not project.zip_path or not Path(project.zip_path).exists():
        raise HTTPException(409, "project has not been generated yet; call /run first")
    return FileResponse(
        path=str(project.zip_path),
        media_type="application/zip",
        filename=f"{project.name}.zip",
    )


class GrantAccess(BaseModel):
    email: str
    role: str = Role.VIEWER.value


@router.get("/projects/{pid}/members", dependencies=[Depends(require_api_key), Depends(limit_reads)])
def list_members(pid: str, user: Optional[User] = Depends(current_user)) -> list:
    """Who has access. Visible to anyone with access — knowing who else can see
    a project is part of understanding your own exposure."""
    project = _require_project(pid, user, Role.VIEWER)
    return memberships.members(pid, project.owner_id)


@router.post("/projects/{pid}/members", status_code=201,
             dependencies=[Depends(require_api_key), Depends(limit_writes)])
def grant_access(pid: str, body: GrantAccess,
                 user: Optional[User] = Depends(current_user)) -> dict:
    """Grant a user a role on this project. Admin only."""
    project = _require_project(pid, user, Role.ADMIN)
    if accounts is None:
        raise HTTPException(400, "sharing requires multi-tenant mode (IACTRANSLATE_AUTH=session)")
    try:
        role = parse_role(body.role)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    target_user = accounts.get_user_by_email(body.email)
    if target_user is None:
        # Deliberately explicit: the caller is an admin of this project and is
        # naming someone they intend to work with. Hiding the difference between
        # "no such account" and "granted" would leave them unable to tell a typo
        # from a silent success.
        raise HTTPException(404, f"no account for '{body.email}'")
    if target_user.id == project.owner_id:
        raise HTTPException(400, "the owner already administers this project")

    memberships.grant(pid, target_user.id, role)
    logger.info("granted %s on project %s", role.value, pid,
                extra={"project_id": pid, "role": role.value})
    return {"user_id": target_user.id, "role": role.value, "owner": False}


@router.delete("/projects/{pid}/members/{user_id}",
               dependencies=[Depends(require_api_key), Depends(limit_writes)])
def revoke_access(pid: str, user_id: str,
                  user: Optional[User] = Depends(current_user)) -> dict:
    """Revoke a grant. Admin only, and the owner cannot be removed —
    a project with no administrator is unrecoverable without a database edit."""
    project = _require_project(pid, user, Role.ADMIN)
    if user_id == project.owner_id:
        raise HTTPException(400, "the owner cannot be removed from their own project")
    if not memberships.revoke(pid, user_id):
        raise HTTPException(404, "no such grant on this project")
    logger.info("revoked access on project %s", pid, extra={"project_id": pid})
    return {"revoked": user_id}


# --------------------------------------------------------------------------- #
# Mounting
# --------------------------------------------------------------------------- #

API_VERSION = "v1"
_DEPRECATION_NOTE = (
    'unversioned paths are deprecated; use /v1'
)

app.include_router(router, prefix=f"/{API_VERSION}")

# Legacy, unprefixed mount. Kept so the web app and any existing caller keep
# working; removing it is a breaking change and belongs in a major release.
app.include_router(router, include_in_schema=False)


@app.middleware("http")
async def _deprecate_unversioned(request: Request, call_next):
    """Mark responses served from the legacy unprefixed paths.

    A client should be able to discover the move without reading a changelog,
    and a deployment should be able to measure how much traffic still needs
    migrating before the legacy mount is removed. `/health` and `/metrics` are
    versionless by design and are not flagged.
    """
    response = await call_next(request)
    path = request.url.path
    if not path.startswith(f"/{API_VERSION}/") and path not in ("/health", "/metrics"):
        response.headers.setdefault("Deprecation", "true")
        response.headers.setdefault("Warning", f'299 - "{_DEPRECATION_NOTE}"')
        response.headers.setdefault("Link", f'</{API_VERSION}{path}>; rel="successor-version"')
    return response
