"""Data model for pre-migration infrastructure assessment.

An assessment is a deterministic, auditable read of the *normalized* inventory
(before any cloud plan is built). It surfaces migration risks, cost-optimization
opportunities, and data-quality gaps, and rolls them into a readiness score the
client can defend line by line. No AI is in this loop.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Finding severity, ordered by migration impact."""

    CRITICAL = "critical"  # blocks or endangers the migration
    HIGH = "high"          # needs a decision before cutover
    MEDIUM = "medium"      # should be reviewed; cost or risk
    LOW = "low"            # minor / hygiene
    INFO = "info"          # informational, no action required


# Readiness penalty per finding, weighted by severity. Capped per-category in the
# analyzer so one noisy check cannot sink the whole score.
SEVERITY_WEIGHT: Dict[Severity, float] = {
    Severity.CRITICAL: 25.0,
    Severity.HIGH: 12.0,
    Severity.MEDIUM: 5.0,
    Severity.LOW: 2.0,
    Severity.INFO: 0.0,
}


class Finding(BaseModel):
    """A single observation about the estate."""

    id: str = Field(description="Stable slug, e.g. 'legacy-os'")
    category: str = Field(description="Grouping, e.g. 'risk' | 'cost' | 'data-quality' | 'capacity'")
    severity: Severity
    title: str
    detail: str = Field(description="Human-readable explanation with the numbers behind it")
    recommendation: str = Field(default="", description="What to do about it")
    affected: List[str] = Field(default_factory=list, description="Affected workload names")

    @property
    def affected_count(self) -> int:
        return len(self.affected)


class ReadinessScore(BaseModel):
    score: int = Field(ge=0, le=100, description="0-100 migration readiness")
    band: str = Field(description="'ready' | 'minor-gaps' | 'needs-work' | 'blocked'")
    rationale: str


class InfrastructureAssessment(BaseModel):
    """The full assessment artifact."""

    project_name: str
    source_platform: str
    total_workloads: int
    powered_on: int
    powered_off: int
    total_vcpu: int
    total_memory_gib: float
    total_storage_gib: float
    windows_workloads: int
    linux_workloads: int
    unknown_os_workloads: int
    utilization_coverage_pct: float = Field(
        description="Share of workloads that carry CPU/memory utilization data"
    )
    readiness: ReadinessScore
    findings: List[Finding] = Field(default_factory=list)

    def by_severity(self, severity: Severity) -> List[Finding]:
        return [f for f in self.findings if f.severity == severity]

    @property
    def counts_by_severity(self) -> Dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self.findings:
            out[f.severity.value] += 1
        return out
