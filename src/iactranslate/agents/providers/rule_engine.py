"""Deterministic, key-free provider. Default and always-available fallback.

Target-aware: instance selection and image-key detection come from the target's
catalog and mappings, so the same provider works for any cloud.
"""
from __future__ import annotations

from typing import Dict, List

from ...models import AppGroup, Environment, NormalizedVM, Tier
from ...sizing import effective_demand
from ...targets.base import Target
from ..base import RightsizeSuggestion
from ..heuristics import detect_environment, detect_tier


class RuleEngineProvider:
    name = "rule"

    def __init__(self, target: Target) -> None:
        self._target = target

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
        order = {Environment.PRODUCTION: 0, Environment.STAGING: 1, Environment.TEST: 2,
                 Environment.DEVELOPMENT: 3, Environment.UNKNOWN: 4}
        return sorted(groups.values(), key=lambda g: order.get(g.environment, 9))

    def rightsize(
        self, vm: NormalizedVM, tier: Tier, environment: Environment
    ) -> RightsizeSuggestion:
        # Size to observed utilization when available, else to raw allocation.
        d = effective_demand(vm)
        instance = self._target.smallest_fit(
            vcpu=d.vcpu,
            memory_gib=d.memory_gib,
            headroom=d.headroom,
            prefer_family=self._target.family_for_tier(tier),
        )
        return RightsizeSuggestion(
            instance_type=instance.name,
            image_key=self._target.image_key(vm.os),
        )
