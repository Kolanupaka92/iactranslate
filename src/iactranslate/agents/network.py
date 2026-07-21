"""Deterministic network planning (never delegated to an LLM).

Allocates a VPC, a public + private subnet per availability zone, and the
security groups implied by the tiers present in the compute plan. CIDR math is
deterministic so the same inventory always yields the same network.
"""
from __future__ import annotations

from typing import Dict, List

from ..models import (
    ComputePlan,
    IngressRule,
    NetworkPlan,
    SecurityGroup,
    Subnet,
    SubnetTier,
    Tier,
    terraform_safe_name,
)

_AZ_COUNT = 2
VPC_CIDR = "10.0.0.0/16"

# Security group name per tier.
SG_BY_TIER: Dict[Tier, str] = {
    Tier.WEB: "web-sg",
    Tier.APP: "app-sg",
    Tier.DATABASE: "db-sg",
    Tier.CACHE: "cache-sg",
    Tier.OTHER: "app-sg",
}

# Which subnet tier a VM tier lands in. Web is internet-facing; everything else private.
SUBNET_BY_TIER: Dict[Tier, SubnetTier] = {
    Tier.WEB: SubnetTier.PUBLIC,
    Tier.APP: SubnetTier.PRIVATE,
    Tier.DATABASE: SubnetTier.PRIVATE,
    Tier.CACHE: SubnetTier.PRIVATE,
    Tier.OTHER: SubnetTier.PRIVATE,
}

# Default ingress per security group. Web is open to the internet; the rest are
# restricted to inside the VPC.
_DEFAULT_INGRESS: Dict[str, List[IngressRule]] = {
    "web-sg": [
        IngressRule(description="HTTP", from_port=80, to_port=80, cidr_blocks=["0.0.0.0/0"]),
        IngressRule(description="HTTPS", from_port=443, to_port=443, cidr_blocks=["0.0.0.0/0"]),
    ],
    "app-sg": [
        IngressRule(description="App tier", from_port=8080, to_port=8080, cidr_blocks=[VPC_CIDR]),
    ],
    "db-sg": [
        IngressRule(description="PostgreSQL", from_port=5432, to_port=5432, cidr_blocks=[VPC_CIDR]),
        IngressRule(description="MySQL", from_port=3306, to_port=3306, cidr_blocks=[VPC_CIDR]),
        IngressRule(description="MSSQL", from_port=1433, to_port=1433, cidr_blocks=[VPC_CIDR]),
    ],
    "cache-sg": [
        IngressRule(description="Redis", from_port=6379, to_port=6379, cidr_blocks=[VPC_CIDR]),
    ],
}


def plan_network(compute: List[ComputePlan]) -> NetworkPlan:
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
    security_groups: List[SecurityGroup] = []
    for sg_name in used:
        security_groups.append(
            SecurityGroup(
                name=sg_name,
                resource_name=terraform_safe_name(sg_name),
                description=f"Security group for {sg_name}",
                ingress=_DEFAULT_INGRESS.get(sg_name, _DEFAULT_INGRESS["app-sg"]),
            )
        )

    needs_private = any(c.subnet_tier == SubnetTier.PRIVATE for c in compute)
    return NetworkPlan(
        vpc_cidr=VPC_CIDR,
        subnets=subnets,
        security_groups=security_groups,
        internet_gateway=True,
        nat_gateway=needs_private,
    )
