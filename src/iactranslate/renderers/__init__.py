"""Renderer registry — the same MigrationPlan, different IaC tools.

`terraform` (default) renders HCL via the target's Jinja2 templates (the
`generator` package). `pulumi` renders an equivalent Pulumi Python program.
Both consume the identical validated plan, proving the pipeline is renderer-
agnostic: parse/normalize/agents/validate produce a plan; renderers are the
last, swappable step.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from ..generator import build_files as _build_terraform
from ..identity import IdentityMode
from ..landing_zone import LandingZone
from ..models import MigrationPlan
from ..state import StateBackend
from ..targets.base import Target
from .bicep import build_bicep_files as _build_bicep
from .cdk import build_cdk_files as _build_cdk
from .cloudformation import build_cloudformation_files as _build_cloudformation
from .kubernetes import build_kubernetes_files as _build_kubernetes
from .pulumi import build_pulumi_files as _build_pulumi

#: Which clouds each renderer can actually emit. `None` means every target.
#:
#: The renderers already enforce this themselves — each raises
#: `RendererNotSupportedError` for a target it cannot express, because
#: CloudFormation is an AWS service and Bicep is an Azure one. What was missing
#: is a way to ask *before* running a pipeline, which any caller offering the
#: choice needs: an API that only discovers the answer by raising, and a UI that
#: offers Bicep for an AWS project, are the same defect at different layers.
#:
#: Declared rather than introspected, and therefore duplication — so
#: `test_renderers.py` calls every renderer against every target and asserts
#: this table matches what they really do. The matrix cannot drift without a
#: test failing, which is the property that makes declaring it acceptable.
_SUPPORTED: Dict[str, Optional[frozenset]] = {
    "terraform": None,
    "pulumi": frozenset({"aws", "azure", "gcp"}),
    "cloudformation": frozenset({"aws"}),
    "bicep": frozenset({"azure"}),
    "cdk": frozenset({"aws"}),
    "kubernetes": None,
}

# name -> (fn(plan, target) -> {filename: content}, human label)
_RENDERERS: Dict[str, tuple] = {
    "terraform": (_build_terraform, "Terraform (HCL)"),
    "pulumi": (_build_pulumi, "Pulumi (Python)"),
    "cloudformation": (_build_cloudformation, "CloudFormation (JSON, AWS-only)"),
    "bicep": (_build_bicep, "Bicep (Azure-only)"),
    "cdk": (_build_cdk, "AWS CDK (Python, AWS-only)"),
    "kubernetes": (_build_kubernetes, "Kubernetes/KubeVirt (JSON, any cloud)"),
}


def supports(name: str, target_name: str) -> bool:
    """Can `name` render for `target_name`?"""
    if name not in _RENDERERS:
        return False
    allowed = _SUPPORTED[name]
    return allowed is None or target_name in allowed


def renderers_for(target_name: str) -> List[str]:
    """The renderers valid for a cloud, in registry order.

    Registry order rather than alphabetical, so `terraform` — the default and
    the only one every target supports — is always offered first.
    """
    return [name for name in _RENDERERS if supports(name, target_name)]


class UnknownRendererError(ValueError):
    pass


def list_renderers() -> List[str]:
    return list(_RENDERERS)


def render(
    name: str,
    plan: MigrationPlan,
    target: Target,
    state_backend: Optional[StateBackend] = None,
    zone: Optional[LandingZone] = None,
    identity: Optional[IdentityMode] = None,
    hybrid=None,
) -> Dict[str, str]:
    """Render the plan with `name`.

    `state_backend` and `hybrid` are Terraform-only and deliberately not
    forwarded elsewhere:
    Pulumi keeps state in its own service or a self-managed backend, and
    CloudFormation/Bicep/CDK have no client-side state at all. Passing it to
    them would imply a control they do not have.
    """
    try:
        fn: Callable = _RENDERERS[name][0]
    except KeyError as e:
        raise UnknownRendererError(
            f"renderer '{name}' not supported (available: {', '.join(_RENDERERS)})"
        ) from e
    if name == "terraform":
        return fn(plan, target, state_backend, zone, identity, hybrid)
    return fn(plan, target)


def renderer_label(name: str) -> str:
    return _RENDERERS[name][1] if name in _RENDERERS else name
