"""Confidence Engine — how sure is IaCTranslate about each decision?

Every structured decision the pipeline makes (tier classification, instance
sizing, OS image, cost basis) rests on signals of varying strength: real
utilization data vs raw allocation, a recognized OS vs a blind default, a live
market price vs a curated rate. This module scores that certainty per workload
and rolls it up for the whole plan — deterministically, from observable signals,
so the number is auditable and never hand-waved.

The engine is read-only: it inspects a finished MigrationPlan + the source VMs
and attaches confidence; it never changes a decision.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .display import plural
from .models import ComputePlan, Environment, MigrationPlan, NormalizedVM, Tier

# Factor weights (sum to 1.0). Sizing dominates because a wrong instance size is
# the costliest and most common migration error; classification and image are
# next; cost basis is a smaller modifier.
_WEIGHTS: Dict[str, float] = {
    "sizing": 0.35,
    "classification": 0.25,
    "image": 0.20,
    "cost": 0.20,
}

# Level thresholds on the 0-1 overall score.
_HIGH = 0.80
_MEDIUM = 0.60

# OS family tokens we can map to a *specific* image; anything else is a default.
_RECOGNIZED_OS = (
    "windows", "red hat", "rhel", "ubuntu", "suse", "sles",
    "centos", "debian", "rocky", "amazon linux", "oracle linux",
)


class FactorScore(BaseModel):
    factor: str = Field(description="'sizing' | 'classification' | 'image' | 'cost'")
    score: float = Field(ge=0.0, le=1.0)
    basis: str = Field(description="Why this score — the signal it rests on")


class WorkloadConfidence(BaseModel):
    vm_name: str
    overall: float = Field(ge=0.0, le=1.0)
    level: str = Field(description="'high' | 'medium' | 'low'")
    factors: List[FactorScore]


class PlanConfidence(BaseModel):
    overall: float = Field(ge=0.0, le=1.0)
    level: str
    summary: str
    factor_averages: Dict[str, float]
    workloads: List[WorkloadConfidence]

    def low_confidence(self) -> List[WorkloadConfidence]:
        return [w for w in self.workloads if w.level == "low"]


def _level(score: float) -> str:
    if score >= _HIGH:
        return "high"
    if score >= _MEDIUM:
        return "medium"
    return "low"


def _classification_factor(c: ComputePlan) -> FactorScore:
    if c.tier == Tier.OTHER:
        return FactorScore(
            factor="classification", score=0.45,
            basis="No clear tier signal — defaulted to 'other'.",
        )
    if c.environment == Environment.UNKNOWN:
        return FactorScore(
            factor="classification", score=0.75,
            basis=f"Tier '{c.tier.value}' inferred; environment unknown.",
        )
    return FactorScore(
        factor="classification", score=0.9,
        basis=f"Tier '{c.tier.value}' and environment '{c.environment.value}' both inferred.",
    )


def _sizing_factor(c: ComputePlan) -> FactorScore:
    if c.right_sized:
        return FactorScore(
            factor="sizing", score=0.9,
            basis="Sized to observed utilization (real demand).",
        )
    return FactorScore(
        factor="sizing", score=0.6,
        basis="Sized from allocation — no utilization data, so headroom is assumed.",
    )


def _image_factor(vm: Optional[NormalizedVM]) -> FactorScore:
    os = (vm.os or "").strip().lower() if vm else ""
    if not os:
        return FactorScore(
            factor="image", score=0.35,
            basis="No OS recorded — image is a blind default.",
        )
    if any(tok in os for tok in _RECOGNIZED_OS):
        return FactorScore(
            factor="image", score=0.9,
            basis="OS recognized and mapped to a specific image.",
        )
    return FactorScore(
        factor="image", score=0.55,
        basis="OS present but unrecognized — image defaulted.",
    )


def _cost_factor(c: ComputePlan) -> FactorScore:
    if c.price_source == "live":
        return FactorScore(
            factor="cost", score=0.9,
            basis="Live market price.",
        )
    return FactorScore(
        factor="cost", score=0.7,
        basis="Curated static rate (no live price for this SKU/region).",
    )


def _workload_confidence(c: ComputePlan, vm: Optional[NormalizedVM]) -> WorkloadConfidence:
    factors = [
        _sizing_factor(c),
        _classification_factor(c),
        _image_factor(vm),
        _cost_factor(c),
    ]
    overall = round(sum(_WEIGHTS[f.factor] * f.score for f in factors), 4)
    return WorkloadConfidence(
        vm_name=c.vm_name, overall=overall, level=_level(overall), factors=factors,
    )


def score_plan(plan: MigrationPlan, vms: Optional[List[NormalizedVM]] = None) -> PlanConfidence:
    """Score confidence for every workload and roll it up to the plan."""
    by_name: Dict[str, NormalizedVM] = {v.vm_name: v for v in (vms or [])}
    workloads = [_workload_confidence(c, by_name.get(c.vm_name)) for c in plan.compute]

    if workloads:
        overall = round(sum(w.overall for w in workloads) / len(workloads), 4)
        factor_averages = {
            name: round(
                sum(f.score for w in workloads for f in w.factors if f.factor == name)
                / len(workloads),
                4,
            )
            for name in _WEIGHTS
        }
    else:
        overall = 0.0
        factor_averages = {name: 0.0 for name in _WEIGHTS}

    level = _level(overall)
    low = [w for w in workloads if w.level == "low"]
    weakest = min(factor_averages, key=factor_averages.get) if workloads else None
    summary = (
        f"Overall confidence {overall * 100:.0f}% ({level}). "
        + (f"{plural(len(low), 'workload')} are low-confidence. " if low else "")
        + (f"Weakest signal: {weakest} ({factor_averages[weakest] * 100:.0f}%)."
           if weakest else "No workloads to score.")
    )

    return PlanConfidence(
        overall=overall,
        level=level,
        summary=summary,
        factor_averages=factor_averages,
        workloads=workloads,
    )
