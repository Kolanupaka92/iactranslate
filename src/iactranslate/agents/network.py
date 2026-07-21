"""Deterministic network planning (never delegated to an LLM).

Allocates a VPC/VNet, a public + private subnet per availability zone, and the
security groups (AWS SG / Azure NSG) implied by the tiers present in the compute
plan. Naming and default ingress come from the selected target; CIDR math is
deterministic so the same inventory always yields the same network.
"""
from __future__ import annotations

from typing import List

from ..models import (
    ComputePlan,
    NetworkPlan,
    SecurityGroup,
    Subnet,
    SubnetTier,
    terraform_safe_name,
)
from ..targets.base import Target

_AZ_COUNT = 2


def plan_network(compute: List[ComputePlan], target: Target) -> NetworkPlan:
    vpc_cidr = target.vpc_cidr
    subnets: List[Subnet] = []
    for az in range(_AZ_COUNT):
        subnets.append(
            Subnet(
                name=f"public-{az}",
                resource_name=f"public_{az}",
                cidr=f"10.0.{az}.0/24",
                tier=SubnetTier.PUBLIC,
                availability_zone_index=az,
            )
        )
        subnets.append(
            Subnet(
                name=f"private-{az}",
                resource_name=f"private_{az}",
                cidr=f"10.0.{az + 10}.0/24",
                tier=SubnetTier.PRIVATE,
                availability_zone_index=az,
            )
        )

    # Only emit security groups actually referenced by the compute plan.
    used = sorted({c.security_group for c in compute})
    fallback = target.default_ingress.get("app-sg", [])
    security_groups: List[SecurityGroup] = [
        SecurityGroup(
            name=sg_name,
            resource_name=terraform_safe_name(sg_name),
            description=f"Security group for {sg_name}",
            ingress=target.default_ingress.get(sg_name, fallback),
        )
        for sg_name in used
    ]

    needs_private = any(c.subnet_tier == SubnetTier.PRIVATE for c in compute)
    return NetworkPlan(
        vpc_cidr=vpc_cidr,
        subnets=subnets,
        security_groups=security_groups,
        internet_gateway=True,
        nat_gateway=needs_private,
    )
