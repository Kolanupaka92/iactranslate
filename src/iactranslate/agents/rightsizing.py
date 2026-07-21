"""Stage: turn each normalized VM into a validated ComputePlan.

The provider proposes an instance type; this module re-checks that choice against
the AWS catalog (falling back to a deterministic best-fit if the model proposes
something that doesn't exist), computes a cost estimate, and derives the subnet
tier and security group from the VM's tier.
"""
from __future__ import annotations

import math
from typing import List

from ..catalog import catalog_index, instance_exists, smallest_fit
from ..models import ComputePlan, Environment, NormalizedVM, Tier
from .base import LLMProvider
from .heuristics import detect_ami_key
from .network import SG_BY_TIER, SUBNET_BY_TIER

# Windows needs a larger root volume than a minimal Linux image.
_MIN_ROOT_GIB = 30
_HEADROOM = 1.2


def _root_and_extra(vm: NormalizedVM) -> tuple[int, List[int]]:
    disks = [int(math.ceil(d)) for d in vm.disks_gib if d > 0]
    if not disks:
        return _MIN_ROOT_GIB, []
    root = max(_MIN_ROOT_GIB, disks[0])
    extra = disks[1:]
    return root, extra


def build_compute_plans(
    vms: List[NormalizedVM],
    provider: LLMProvider,
    tier_env,
) -> List[ComputePlan]:
    """`tier_env` maps vm_name -> (Tier, Environment) from classification."""
    index = catalog_index()
    plans: List[ComputePlan] = []

    for vm in vms:
        tier, environment = tier_env.get(vm.vm_name, (Tier.OTHER, Environment.UNKNOWN))

        suggestion = provider.rightsize(vm, tier, environment)
        instance_type = suggestion.instance_type
        # Guardrail: never emit an instance type that isn't in the catalog.
        if not instance_exists(instance_type):
            prefer = "r5" if tier in (Tier.DATABASE, Tier.CACHE) else None
            instance_type = smallest_fit(
                vm.cpu, vm.memory_gib, headroom=_HEADROOM, prefer_family=prefer
            ).instance_type

        ami_key = suggestion.ami_key or detect_ami_key(vm.os)
        instance = index[instance_type]
        root_gib, extra_gib = _root_and_extra(vm)

        plans.append(
            ComputePlan(
                vm_name=vm.vm_name,
                resource_name=vm.resource_name,
                instance_type=instance_type,
                ami_key=ami_key,
                vcpu=instance.vcpu,
                memory_gib=instance.memory_gib,
                root_volume_gib=root_gib,
                extra_volumes_gib=extra_gib,
                subnet_tier=SUBNET_BY_TIER.get(tier, SUBNET_BY_TIER[Tier.OTHER]),
                security_group=SG_BY_TIER.get(tier, SG_BY_TIER[Tier.OTHER]),
                tier=tier,
                environment=environment,
                estimated_monthly_cost_usd=instance.monthly_usd,
            )
        )
    return plans
