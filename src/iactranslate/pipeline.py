"""End-to-end pipeline: discovery export -> validated Terraform project.

    parse -> normalize -> agents(classify/rightsize/network) -> validate -> render -> package
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .agents import build_migration_plan
from .agents.base import LLMProvider
from .config import MAX_VMS
from .models import MigrationPlan, NormalizedVM
from .normalize import normalize
from .packager import build_project, zip_project
from .pricing import live_enabled
from .sources import resolve_source
from .targets import get_target
from .validation import assert_valid


@dataclass
class PipelineResult:
    plan: MigrationPlan
    vms: List[NormalizedVM]
    project_dir: Path
    zip_path: Optional[Path]


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
) -> PipelineResult:
    tgt = get_target(target)  # raises UnknownTargetError for bad target
    src = resolve_source(input_path, source)  # auto-detect unless named

    vms = normalize(src.parse(input_path, column_map=column_map))
    if not vms:
        raise ValueError(f"No workloads found in '{input_path}' (source: {src.name})")
    if len(vms) > MAX_VMS:
        raise ValueError(f"Inventory has {len(vms)} workloads, exceeding the limit of {MAX_VMS}")

    plan = build_migration_plan(
        vms, project_name=project_name, target=tgt, region=region, provider=provider,
        source_platform=getattr(src, "source_platform", src.name),
        live_pricing=live_enabled(),
    )
    assert_valid(plan, tgt)  # raises PlanValidationError on any issue

    project_dir = build_project(plan, out_dir, tgt, vms=vms, renderer=renderer, gitops=gitops)
    zip_path = None
    if make_zip:
        zip_path = zip_project(project_dir, Path(out_dir).with_suffix(".zip"))

    return PipelineResult(plan=plan, vms=vms, project_dir=project_dir, zip_path=zip_path)
