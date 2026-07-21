"""Stage: turn each normalized VM into a validated ComputePlan.

The provider proposes an instance type; this module re-checks that choice against
the target's catalog (falling back to a deterministic best-fit if the model
proposes something that doesn't exist), computes a cost estimate, and derives the
subnet tier and security group from the VM's tier — all via the target.
"""
from __future__ import annotations

import math
from typing import List, Tuple

from ..models import ComputePlan, Environment, NormalizedVM, Tier
from ..targets.base import Target
from .base import LLMProvider

_MIN_ROOT_GIB = 30
_HEADROOM = 1.2


def _root_and_extra(vm: NormalizedVM) -> Tuple[int, List[int]]:
    disks = [int(math.ceil(d)) for d in vm.disks_gib if d > 0]
    if not disks:
        return _MIN_ROOT_GIB, []
    root = max(_MIN_ROOT_GIB, disks[0])
    return root, disks[1:]


def build_compute_plans(
    vms: List[NormalizedVM],
    provider: LLMProvider,
    tier_env,
    target: Target,
) -> List[ComputePlan]:
    """`tier_env` maps vm_name -> (Tier, Environment) from classification."""
    plans: List[ComputePlan] = []

    for vm in vms:
        tier, environment = tier_env.get(vm.vm_name, (Tier.OTHER, Environment.UNKNOWN))

        suggestion = provider.rightsize(vm, tier, environment)
        instance_type = suggestion.instance_type
        # Guardrail: never emit an instance type that isn't in the target catalog.
        if not target.instance_exists(instance_type):
            instance_type = target.smallest_fit(
                vm.cpu, vm.memory_gib, headroom=_HEADROOM,
                prefer_family=target.family_for_tier(tier),
            ).name

        spec = target.spec_of(instance_type)
        image_key = suggestion.image_key or target.image_key(vm.os)
        root_gib, extra_gib = _root_and_extra(vm)

        plans.append(
            ComputePlan(
                vm_name=vm.vm_name,
                resource_name=vm.resource_name,
                instance_type=instance_type,
                image_key=image_key,
                vcpu=spec.vcpu if spec else vm.cpu,
                memory_gib=spec.memory_gib if spec else vm.memory_gib,
                root_volume_gib=root_gib,
                extra_volumes_gib=extra_gib,
                subnet_tier=target.subnet_tier_for_tier(tier),
                security_group=target.sg_for_tier(tier),
                tier=tier,
                environment=environment,
                estimated_monthly_cost_usd=target.cost_of(instance_type),
            )
        )
    return plans
