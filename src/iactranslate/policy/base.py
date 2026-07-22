"""Policy engine primitives — enforce org rules on a plan, without mutating it.

A `Policy` reads a validated `MigrationPlan` and returns violations. It never
changes the plan (that would break the immutable-plan contract and determinism);
enforcement is a *gate*, exactly like validation. `deny` violations fail the run;
`warn` violations are reported but don't block.

Policies are pluggable: each is a small callable registered by name and activated
(with parameters) through a policy config, so an organization expresses its rules
as configuration, never by editing the core pipeline.
"""
from __future__ import annotations

from enum import Enum
from typing import Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from ..models import MigrationPlan
from ..targets.base import Target


class Severity(str, Enum):
    DENY = "deny"   # blocks rendering
    WARN = "warn"   # reported, non-blocking


class PolicyViolation(BaseModel):
    policy: str
    severity: Severity
    message: str
    resource: Optional[str] = Field(default=None, description="Affected workload/resource, if scoped")


# A policy is: (plan, target, config, severity) -> list of violations.
PolicyFn = Callable[[MigrationPlan, Target, dict, Severity], List[PolicyViolation]]


class PolicyResult(BaseModel):
    violations: List[PolicyViolation] = Field(default_factory=list)

    @property
    def denials(self) -> List[PolicyViolation]:
        return [v for v in self.violations if v.severity == Severity.DENY]

    @property
    def warnings(self) -> List[PolicyViolation]:
        return [v for v in self.violations if v.severity == Severity.WARN]

    @property
    def ok(self) -> bool:
        return not self.denials


class PolicyViolationError(ValueError):
    """Raised when a plan violates one or more `deny` policies."""

    def __init__(self, violations: List[PolicyViolation]) -> None:
        self.violations = violations
        lines = [f"[{v.policy}] {v.message}" + (f" ({v.resource})" if v.resource else "") for v in violations]
        super().__init__("Migration plan violates policy:\n  - " + "\n  - ".join(lines))


# Registry of built-in policies: name -> (fn, default severity, description).
_REGISTRY: Dict[str, tuple] = {}


def register(name: str, default_severity: Severity, description: str) -> Callable[[PolicyFn], PolicyFn]:
    def deco(fn: PolicyFn) -> PolicyFn:
        _REGISTRY[name] = (fn, default_severity, description)
        return fn
    return deco


def registry() -> Dict[str, tuple]:
    return _REGISTRY
