"""Deterministic, key-free provider. Default and always-available fallback."""
from __future__ import annotations

from typing import Dict, List

from ...catalog import smallest_fit
from ...models import AppGroup, Environment, NormalizedVM, Tier
from ..base import RightsizeSuggestion
from ..heuristics import detect_ami_key, detect_environment, detect_tier

# Databases benefit from memory-optimized instances.
_FAMILY_BY_TIER = {Tier.DATABASE: "r5", Tier.CACHE: "r5"}
# Leave 20% headroom above observed allocation when picking an instance.
_HEADROOM = 1.2


class RuleEngineProvider:
    name = "rule"

    def classify(self, vms: List[NormalizedVM]) -> List[AppGroup]:
        """Group VMs into one application per detected environment.

        Deterministic and explainable: environment + tier come from name/host
        heuristics. A richer LLM provider can produce finer app boundaries.
        """
        groups: Dict[Environment, AppGroup] = {}
        for vm in vms:
            env = detect_environment(vm)
            tier = detect_tier(vm)
            group = groups.get(env)
            if group is None:
                group = AppGroup(name=f"{env.value}-application", environment=env, members={})
                groups[env] = group
            group.members[vm.vm_name] = tier
        # Stable order: production first, then the rest alphabetically.
        order = {Environment.PRODUCTION: 0, Environment.STAGING: 1, Environment.TEST: 2,
                 Environment.DEVELOPMENT: 3, Environment.UNKNOWN: 4}
        return sorted(groups.values(), key=lambda g: order.get(g.environment, 9))

    def rightsize(
        self, vm: NormalizedVM, tier: Tier, environment: Environment
    ) -> RightsizeSuggestion:
        instance = smallest_fit(
            vcpu=vm.cpu,
            memory_gib=vm.memory_gib,
            headroom=_HEADROOM,
            prefer_family=_FAMILY_BY_TIER.get(tier),
        )
        return RightsizeSuggestion(
            instance_type=instance.instance_type,
            ami_key=detect_ami_key(vm.os),
        )
