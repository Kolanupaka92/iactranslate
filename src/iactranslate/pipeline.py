"""End-to-end pipeline: discovery export -> validated Terraform project.

The pipeline runs as an ordered list of **named stages**, each timed, so the run
is observable (a per-stage trace) and legible (the same stage names the docs use).
Full resumability/distribution would build on this stage model but needs
persistence — see the roadmap; today the pipeline is a synchronous, deterministic
function that records where its time goes.

    parse -> normalize -> plan -> validate -> policy -> package [-> zip]
"""
from __future__ import annotations

import json
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel

from .agents import build_migration_plan
from .agents.base import LLMProvider
from .config import MAX_VMS
from .dependencies import analyze_dependencies, parse_flows
from .hybrid import plan_hybrid
from .identity import IdentityMode
from .landing_zone import LandingZone
from .models import MigrationPlan, NormalizedVM
from .normalize import normalize
from .observability import get_logger
from .packager import build_project, zip_project
from .policy import PolicyResult, PolicyViolationError, evaluate
from .pricing import live_enabled
from .sources import resolve_source
from .state import StateBackend
from .targets import get_target
from .validation import assert_valid

logger = get_logger("iactranslate.pipeline")


class StageTiming(BaseModel):
    stage: str
    duration_ms: float


class PipelineTrace(BaseModel):
    """Per-stage timing for one pipeline run — the observability record."""

    stages: List[StageTiming]
    total_ms: float


@dataclass
class PipelineResult:
    plan: MigrationPlan
    vms: List[NormalizedVM]
    project_dir: Path
    zip_path: Optional[Path]
    policy: Optional[PolicyResult] = None
    trace: Optional[PipelineTrace] = None


def run_pipeline(
    input_path: str,
    project_name: str,
    out_dir: str,
    target: str = "aws",
    source: Optional[str] = None,
    column_map: Optional[Dict[str, str]] = None,
    region: Optional[str] = None,
    provider: Optional[LLMProvider] = None,
    make_zip: bool = False,
    renderer: str = "terraform",
    gitops: bool = False,
    policy_config: Optional[Dict] = None,
    state_backend: Optional[StateBackend] = None,
    zone: Optional[LandingZone] = None,
    identity: Optional[IdentityMode] = None,
    flows_path: Optional[str] = None,
) -> PipelineResult:
    timings: List[StageTiming] = []

    @contextmanager
    def stage(name: str):
        """Time a stage, logging it, and record the timing even when it fails.

        The `finally` matters: previously a stage that raised appended no
        timing at all, so the trace of a failed run stopped silently at the
        last stage that *succeeded* and said nothing about the one that broke —
        losing exactly the datum you want when a translation blows up on a
        customer's export.
        """
        t0 = time.perf_counter()
        try:
            yield
        except Exception as exc:
            elapsed = round((time.perf_counter() - t0) * 1000, 3)
            timings.append(StageTiming(stage=name, duration_ms=elapsed))
            logger.error(
                "stage %s failed", name,
                extra={"stage": name, "duration_ms": elapsed,
                       "outcome": "error", "error_type": type(exc).__name__},
            )
            raise
        elapsed = round((time.perf_counter() - t0) * 1000, 3)
        timings.append(StageTiming(stage=name, duration_ms=elapsed))
        logger.debug(
            "stage %s", name,
            extra={"stage": name, "duration_ms": elapsed, "outcome": "ok"},
        )

    tgt = get_target(target)  # raises UnknownTargetError for bad target

    # Everything that opens the file is inside this guard. A malformed upload —
    # an HTML page renamed .xlsx, a truncated zip — raised zipfile.BadZipFile
    # or openpyxl's InvalidFileException, neither of which is a ValueError, so
    # the API's handler let them through as a 500 "internal server error".
    # That is the wrong answer twice over: it is the caller's file that is
    # broken, not the server, and a generic 500 tells them nothing they can
    # act on. Everything a bad file can raise now becomes one ValueError with
    # a message that names the problem and not the server's path to it.
    try:
        src = resolve_source(input_path, source)  # auto-detect unless named
        with stage("parse"):
            raw = src.parse(input_path, column_map=column_map)
    except (zipfile.BadZipFile, InvalidFileException) as e:
        raise ValueError(
            "the uploaded file is not a valid .xlsx workbook (it may be renamed, "
            "truncated, or a different format). Export it again from the source tool."
        ) from e
    except UnicodeDecodeError as e:
        raise ValueError(
            "the uploaded file is not readable as text — a .csv must be UTF-8 (or "
            "UTF-8 with BOM). Re-export it, or upload the .xlsx instead."
        ) from e

    with stage("normalize"):
        vms = normalize(raw)
    if not vms:
        # The source name is useful; the server-side path is not, and leaking it
        # told the caller the workspace root, the project directory and the
        # naming scheme of the decrypted temporary file.
        raise ValueError(
            f"No workloads found in the uploaded inventory (read as: {src.name}). "
            "Check the file has a header row and at least one machine."
        )
    if len(vms) > MAX_VMS:
        raise ValueError(f"Inventory has {len(vms)} workloads, exceeding the limit of {MAX_VMS}")

    with stage("plan"):
        plan = build_migration_plan(
            vms, project_name=project_name, target=tgt, region=region, provider=provider,
            source_platform=getattr(src, "source_platform", src.name),
            live_pricing=live_enabled(), zone=zone,
        )

    with stage("validate"):
        assert_valid(plan, tgt)  # raises PlanValidationError on any issue

    # Policy gate: enforce org rules on the (immutable) plan before rendering.
    # `deny` violations abort; `warn` violations are recorded and shipped.
    with stage("policy"):
        policy_result = evaluate(plan, tgt, policy_config)
    if not policy_result.ok:
        raise PolicyViolationError(policy_result.denials)

    # On-prem networks the estate still depends on, from an optional flow
    # export. Runs after the plan because it needs the VPC range to detect an
    # on-prem network that collides with it. Analysis only — the plan is
    # immutable by here and this reads it (ADR 0007).
    hybrid = None
    if flows_path:
        with stage("hybrid"):
            hybrid = plan_hybrid(
                analyze_dependencies(parse_flows(flows_path), vms), plan.network.vpc_cidr
            )

    with stage("package"):
        project_dir = build_project(
            plan, out_dir, tgt, vms=vms, renderer=renderer, gitops=gitops,
            policy_result=policy_result, state_backend=state_backend, zone=zone,
            identity=identity, hybrid=hybrid,
        )

    zip_path = None
    if make_zip:
        with stage("zip"):
            zip_path = zip_project(project_dir, Path(out_dir).with_suffix(".zip"))

    trace = PipelineTrace(stages=timings, total_ms=round(sum(t.duration_ms for t in timings), 3))
    # Structured, per-stage observability line + a machine-readable trace artifact.
    logger.info(
        "pipeline complete",
        extra={
            "vm_count": len(vms),
            "duration_ms": trace.total_ms,
            "target": tgt.name,
            "source": src.name,
            "renderer": renderer,
            "region": plan.region,
            "provider_used": plan.provider_used,
            "stages": {t.stage: t.duration_ms for t in trace.stages},
        },
    )
    (project_dir / "pipeline-trace.json").write_text(
        json.dumps(trace.model_dump(mode="json"), indent=2)
    )

    return PipelineResult(
        plan=plan, vms=vms, project_dir=project_dir, zip_path=zip_path,
        policy=policy_result, trace=trace,
    )
