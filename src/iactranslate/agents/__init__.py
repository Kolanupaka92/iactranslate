"""Agent pipeline: classify -> rightsize -> network -> MigrationPlan."""
from __future__ import annotations

from typing import List, Optional

from ..models import MigrationPlan, NormalizedVM
from .base import LLMProvider
from .classifier import classify, tier_env_index
from .network import plan_network
from .providers import get_provider
from .rightsizing import build_compute_plans


def build_migration_plan(
    vms: List[NormalizedVM],
    project_name: str,
    region: str = "us-east-1",
    provider: Optional[LLMProvider] = None,
) -> MigrationPlan:
    """Run the agent stages and assemble an (un-validated) MigrationPlan."""
    provider = provider or get_provider()

    app_groups = classify(vms, provider)
    tier_env = tier_env_index(app_groups)
    compute = build_compute_plans(vms, provider, tier_env)
    network = plan_network(compute)

    return MigrationPlan(
        project_name=project_name,
        source_platform="vmware",
        target="aws",
        region=region,
        network=network,
        compute=compute,
        app_groups=app_groups,
    )
