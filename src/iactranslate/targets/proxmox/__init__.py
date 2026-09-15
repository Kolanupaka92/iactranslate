"""Proxmox VE target — VMs, VLAN subnets and categories via the proxmox provider.

The first on-premises destination. Deliberately carries no `CAP_PRICED`: an
Proxmox VM's cost is hardware, licensing and facilities, which no inventory can
supply, so this target is generated as a destination but never cost-ranked
against the public clouds (see `targets/_onprem.py` and ADR 0065).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from ...models import IngressRule, SubnetTier, Tier
from .._onprem import INSTANCE_CATALOG, index
from ..base import (
    CAP_GITOPS,
    CAP_TERRAFORM,
    TEMPLATE_MAP,
    InstanceSpec,
    smallest_fit,
)
from . import mapping


class ProxmoxTarget:
    name = "proxmox"
    #: Not a region — there are none on-premises. Carried as the node name
    #: so the shared `region` plumbing still has something meaningful to show.
    default_region = "pve-01"
    vpc_cidr = mapping.VPC_CIDR
    template_dir = Path(__file__).parent / "templates"
    capabilities = frozenset({CAP_TERRAFORM, CAP_GITOPS})
    template_map: Dict[str, str] = TEMPLATE_MAP
    default_ingress: Dict[str, List[IngressRule]] = mapping.DEFAULT_INGRESS

    def __init__(self) -> None:
        self._index = index()

    def instance_exists(self, name: str) -> bool:
        return name in self._index

    def instance_names(self) -> List[str]:
        return [i.name for i in INSTANCE_CATALOG]

    def smallest_fit(
        self,
        vcpu: int,
        memory_gib: float,
        headroom: float = 1.0,
        prefer_family: Optional[str] = None,
    ) -> InstanceSpec:
        return smallest_fit(INSTANCE_CATALOG, vcpu, memory_gib, headroom, prefer_family)

    def spec_of(self, name: str) -> Optional[InstanceSpec]:
        return self._index.get(name)

    def cost_of(self, instance_name: str) -> float:
        # Zero by construction, and excluded from ranking by CAP_PRICED's
        # absence — never read this as "free".
        return 0.0

    def image_key(self, os: Optional[str]) -> str:
        return mapping.image_key(os)

    def image_reference(self, image_key: str) -> Dict[str, object]:
        return mapping.image_reference(image_key)

    def family_for_tier(self, tier: Tier) -> Optional[str]:
        return mapping.FAMILY_BY_TIER.get(tier)

    def subnet_tier_for_tier(self, tier: Tier) -> SubnetTier:
        return mapping.SUBNET_BY_TIER.get(tier, SubnetTier.PRIVATE)

    def sg_for_tier(self, tier: Tier) -> str:
        return mapping.SG_BY_TIER.get(tier, "app")
