"""Deterministic network planning (never delegated to an LLM).

Allocates a VPC/VNet, a public + private subnet per availability zone, the
security groups (AWS SG / Azure NSG) implied by the tiers present in the compute
plan, and a load balancer for any (tier, environment) group with more than one
instance. Naming and default ingress come from the selected target; all of it
is deterministic so the same inventory always yields the same network.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ..landing_zone import LandingZone, carve_subnets
from ..models import (
    ComputePlan,
    Environment,
    IngressRule,
    LoadBalancerListener,
    LoadBalancerPlan,
    NetworkPlan,
    SecurityGroup,
    Subnet,
    SubnetTier,
    Tier,
    terraform_safe_name,
)
from ..targets.base import Target

_AZ_COUNT = 2
_HTTPS_PORT = 443


def _listener_protocol(port: int) -> str:
    return "HTTPS" if port == _HTTPS_PORT else "HTTP"


def _plan_load_balancers(
    compute: List[ComputePlan], security_groups: List[SecurityGroup]
) -> List[LoadBalancerPlan]:
    """One load balancer per (tier, environment, subnet_tier) group of >1 instance."""
    sg_by_name: Dict[str, SecurityGroup] = {sg.name: sg for sg in security_groups}
    groups: Dict[Tuple[Tier, Environment, SubnetTier], List[ComputePlan]] = defaultdict(list)
    for c in compute:
        groups[(c.tier, c.environment, c.subnet_tier)].append(c)

    load_balancers: List[LoadBalancerPlan] = []
    for (tier, environment, subnet_tier), members in sorted(
        groups.items(), key=lambda kv: (kv[0][0].value, kv[0][1].value, kv[0][2].value)
    ):
        if len(members) < 2:
            continue
        sg = sg_by_name.get(members[0].security_group)
        ports = sorted({r.from_port for r in sg.ingress}) if sg else []
        if not ports:
            continue
        name = f"{environment.value}-{tier.value}-lb"
        load_balancers.append(
            LoadBalancerPlan(
                name=name,
                resource_name=terraform_safe_name(name),
                tier=tier,
                environment=environment,
                subnet_tier=subnet_tier,
                security_group=members[0].security_group,
                listeners=[
                    LoadBalancerListener(protocol=_listener_protocol(p), listener_port=p, target_port=p)
                    for p in ports
                ],
                targets=[c.vm_name for c in members],
            )
        )
    return load_balancers


def _retarget_ingress(
    rules: List[IngressRule], default_cidr: str, actual_cidr: str
) -> List[IngressRule]:
    """Point intra-VPC ingress rules at the VPC that actually exists.

    Each target's default ingress hardcodes its own `VPC_CIDR` as the source
    range for internal traffic (database ports reachable from the VPC, and so
    on). Once the VPC range can be supplied by the customer's IPAM team, those
    rules would allow a range the VPC does not use and — worse — *not* allow the
    range it does, silently producing a network that both over-permits an
    unrelated block and fails to work.

    Only rules whose source is exactly the target's default VPC range are
    rewritten; `0.0.0.0/0` on a public HTTP listener is deliberate and left alone.
    """
    if actual_cidr == default_cidr:
        return rules
    return [
        rule.model_copy(
            update={"cidr_blocks": [actual_cidr if c == default_cidr else c
                                    for c in rule.cidr_blocks]}
        )
        for rule in rules
    ]


def plan_network(
    compute: List[ComputePlan],
    target: Target,
    zone: Optional[LandingZone] = None,
) -> NetworkPlan:
    # The VPC range comes from the landing zone when the customer's network team
    # allocated one; otherwise the target's default.
    vpc_cidr = zone.cidr if zone else target.vpc_cidr
    # Carved from that range rather than hardcoded. Subnets used to be fixed at
    # `10.0.{az}.0/24` while the VPC took its CIDR from the target, so any other
    # range produced subnets outside their own VPC — latent only because every
    # target happened to use 10.0.0.0/16.
    public_cidrs, private_cidrs = carve_subnets(vpc_cidr, _AZ_COUNT)
    subnets: List[Subnet] = []
    for az in range(_AZ_COUNT):
        subnets.append(
            Subnet(
                name=f"public-{az}",
                resource_name=f"public_{az}",
                cidr=public_cidrs[az],
                tier=SubnetTier.PUBLIC,
                availability_zone_index=az,
            )
        )
        subnets.append(
            Subnet(
                name=f"private-{az}",
                resource_name=f"private_{az}",
                cidr=private_cidrs[az],
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
            ingress=_retarget_ingress(
                target.default_ingress.get(sg_name, fallback), target.vpc_cidr, vpc_cidr
            ),
        )
        for sg_name in used
    ]

    needs_private = any(c.subnet_tier == SubnetTier.PRIVATE for c in compute)
    return NetworkPlan(
        vpc_cidr=vpc_cidr,
        subnets=subnets,
        security_groups=security_groups,
        load_balancers=_plan_load_balancers(compute, security_groups),
        internet_gateway=True,
        nat_gateway=needs_private,
    )
