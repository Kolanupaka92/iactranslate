"""Pre-migration infrastructure assessment.

Analyze a normalized inventory *before* building a cloud plan: surface migration
risks, cost-optimization opportunities, and data-quality gaps, and roll them into
an auditable readiness score. Fully deterministic — no AI, no network.
"""
from __future__ import annotations

from .analyzer import assess
from .models import (
    Finding,
    InfrastructureAssessment,
    ReadinessScore,
    Severity,
)
from .report import to_html, to_json

__all__ = [
    "assess",
    "Finding",
    "InfrastructureAssessment",
    "ReadinessScore",
    "Severity",
    "to_html",
    "to_json",
]
