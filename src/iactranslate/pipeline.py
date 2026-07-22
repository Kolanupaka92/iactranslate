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
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel

from .agents import build_migration_plan
from .agents.base import LLMProvider
from .config import MAX_VMS
from .models import MigrationPlan, NormalizedVM
from .normalize import normalize
from .packager import build_project, zip_project
from .policy import PolicyResult, PolicyViolationError, evaluate
from .pricing import live_enabled
from .sources import resolve_source
from .targets import get_target
from .validation import assert_valid

logger = logging.getLogger("iactranslate.pipeline")


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
) -> PipelineResult:
    timings: List[StageTiming] = []

    @contextmanager
    def stage(name: str):
        t0 = time.perf_counter()
        yield
        timings.append(StageTiming(stage=name, duration_ms=round((time.perf_counter() - t0) * 1000, 3)))

    tgt = get_target(target)  # raises UnknownTargetError for bad target
    src = resolve_source(input_path, source)  # auto-detect unless named

    with stage("parse"):
        raw = src.parse(input_path, column_map=column_map)
    with stage("normalize"):
        vms = normalize(raw)
    if not vms:
        raise ValueError(f"No workloads found in '{input_path}' (source: {src.name})")
    if len(vms) > MAX_VMS:
        raise ValueError(f"Inventory has {len(vms)} workloads, exceeding the limit of {MAX_VMS}")

    with stage("plan"):
        plan = build_migration_plan(
            vms, project_name=project_name, target=tgt, region=region, provider=provider,
            source_platform=getattr(src, "source_platform", src.name),
            live_pricing=live_enabled(),
        )

    with stage("validate"):
        assert_valid(plan, tgt)  # raises PlanValidationError on any issue

    # Policy gate: enforce org rules on the (immutable) plan before rendering.
    # `deny` violations abort; `warn` violations are recorded and shipped.
    with stage("policy"):
        policy_result = evaluate(plan, tgt, policy_config)
    if not policy_result.ok:
        raise PolicyViolationError(policy_result.denials)

    with stage("package"):
        project_dir = build_project(
            plan, out_dir, tgt, vms=vms, renderer=renderer, gitops=gitops,
            policy_result=policy_result,
        )

    zip_path = None
    if make_zip:
        with stage("zip"):
            zip_path = zip_project(project_dir, Path(out_dir).with_suffix(".zip"))

    trace = PipelineTrace(stages=timings, total_ms=round(sum(t.duration_ms for t in timings), 3))
    # Structured, per-stage observability line + a machine-readable trace artifact.
    logger.info(
        "pipeline complete: %s workloads, %.1f ms total (%s)",
        len(vms), trace.total_ms,
        ", ".join(f"{t.stage}={t.duration_ms:.1f}ms" for t in trace.stages),
    )
    (project_dir / "pipeline-trace.json").write_text(
        json.dumps(trace.model_dump(mode="json"), indent=2)
    )

    return PipelineResult(
        plan=plan, vms=vms, project_dir=project_dir, zip_path=zip_path,
        policy=policy_result, trace=trace,
    )
