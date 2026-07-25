"""Agent pipeline: classify -> rightsize -> network -> MigrationPlan."""
from __future__ import annotations

from typing import List, Optional

from ..models import MigrationPlan, NormalizedVM
from ..targets.base import Target
from .base import LLMProvider
from .classifier import classify, tier_env_index
from .network import plan_network
from .providers import get_provider
from .rightsizing import build_compute_plans


def build_migration_plan(
    vms: List[NormalizedVM],
    project_name: str,
    target: Target,
    region: Optional[str] = None,
    provider: Optional[LLMProvider] = None,
    source_platform: str = "vmware",
    live_pricing: bool = False,
) -> MigrationPlan:
    """Run the agent stages and assemble an (un-validated) MigrationPlan."""
    provider = provider or get_provider(target)
    region = region or target.default_region

    app_groups = classify(vms, provider)
    tier_env = tier_env_index(app_groups)
    compute = build_compute_plans(vms, provider, tier_env, target, region, live_pricing)
    network = plan_network(compute, target)

    return MigrationPlan(
        project_name=project_name,
        source_platform=source_platform,
        target=target.name,
        region=region,
        network=network,
        compute=compute,
        app_groups=app_groups,
        provider_used=provider.name,
    )
