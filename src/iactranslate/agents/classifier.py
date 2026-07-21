"""Stage: classify VMs into applications/tiers via the selected provider."""
from __future__ import annotations

from typing import Dict, List, Tuple

from ..models import AppGroup, Environment, NormalizedVM, Tier
from .base import LLMProvider


def classify(vms: List[NormalizedVM], provider: LLMProvider) -> List[AppGroup]:
    return provider.classify(vms)


def tier_env_index(app_groups: List[AppGroup]) -> Dict[str, Tuple[Tier, Environment]]:
    """Flatten app groups into a vm_name -> (tier, environment) lookup."""
    index: Dict[str, Tuple[Tier, Environment]] = {}
    for group in app_groups:
        for vm_name, tier in group.members.items():
            index[vm_name] = (tier, group.environment)
    return index
