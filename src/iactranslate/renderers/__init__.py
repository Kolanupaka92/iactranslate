"""Renderer registry — the same MigrationPlan, different IaC tools.

`terraform` (default) renders HCL via the target's Jinja2 templates (the
`generator` package). `pulumi` renders an equivalent Pulumi Python program.
Both consume the identical validated plan, proving the pipeline is renderer-
agnostic: parse/normalize/agents/validate produce a plan; renderers are the
last, swappable step.
"""
from __future__ import annotations

from typing import Callable, Dict, List

from ..generator import build_files as _build_terraform
from ..models import MigrationPlan
from ..targets.base import Target
from .bicep import build_bicep_files as _build_bicep
from .cloudformation import build_cloudformation_files as _build_cloudformation
from .pulumi import build_pulumi_files as _build_pulumi

# name -> (fn(plan, target) -> {filename: content}, human label)
_RENDERERS: Dict[str, tuple] = {
    "terraform": (_build_terraform, "Terraform (HCL)"),
    "pulumi": (_build_pulumi, "Pulumi (Python)"),
    "cloudformation": (_build_cloudformation, "CloudFormation (JSON, AWS-only)"),
    "bicep": (_build_bicep, "Bicep (Azure-only)"),
}


class UnknownRendererError(ValueError):
    pass


def list_renderers() -> List[str]:
    return list(_RENDERERS)


def render(name: str, plan: MigrationPlan, target: Target) -> Dict[str, str]:
    try:
        fn: Callable = _RENDERERS[name][0]
    except KeyError as e:
        raise UnknownRendererError(
            f"renderer '{name}' not supported (available: {', '.join(_RENDERERS)})"
        ) from e
    return fn(plan, target)


def renderer_label(name: str) -> str:
    return _RENDERERS[name][1] if name in _RENDERERS else name
