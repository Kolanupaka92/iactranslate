"""Policy engine — enforce organization rules on a plan before rendering.

Enterprise requirements diverge exactly here: naming, approved instance families,
no public IPs, budget caps, mandatory NAT. Rather than bury these in the core
pipeline, a company expresses them as a **policy config** that activates and
parameterizes pluggable policies. The engine reads the (already validated,
immutable) plan and returns violations — `deny` blocks rendering, `warn` is
reported. Nothing here mutates the plan.

    from iactranslate.policy import evaluate, load_policy_config
    result = evaluate(plan, target, {"no_public_subnets": {}, "max_vcpu": {"max": 16}})
    if not result.ok:
        ...  # result.denials
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from ..models import MigrationPlan
from ..targets.base import Target
from . import builtins  # noqa: F401 — registers the built-in policies
from .base import (
    PolicyResult,
    PolicyViolation,
    PolicyViolationError,
    Severity,
    registry,
)

__all__ = [
    "evaluate",
    "load_policy_config",
    "list_policies",
    "PolicyResult",
    "PolicyViolation",
    "PolicyViolationError",
    "Severity",
    "UnknownPolicyError",
]


class UnknownPolicyError(ValueError):
    pass


def list_policies() -> Dict[str, str]:
    """name -> description for every registered policy."""
    return {name: meta[2] for name, meta in registry().items()}


def load_policy_config(path: str) -> dict:
    """Load a JSON policy config: {policy_name: {params...}}."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("policy config must be a JSON object of {policy_name: {params}}")
    return data


def evaluate(
    plan: MigrationPlan,
    target: Target,
    policy_config: Optional[dict] = None,
) -> PolicyResult:
    """Run every configured policy against the plan and collect violations."""
    reg = registry()
    violations: List[PolicyViolation] = []
    for name, cfg in (policy_config or {}).items():
        if name not in reg:
            raise UnknownPolicyError(
                f"unknown policy '{name}' (available: {', '.join(sorted(reg))})"
            )
        cfg = cfg or {}
        fn, default_sev, _desc = reg[name]
        sev = Severity(cfg["severity"]) if cfg.get("severity") else default_sev
        violations.extend(fn(plan, target, cfg, sev))
    return PolicyResult(violations=violations)
