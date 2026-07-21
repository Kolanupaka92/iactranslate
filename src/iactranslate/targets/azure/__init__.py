"""Azure target — VM/VNet/NSG via Terraform azurerm provider."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from ...models import IngressRule, SubnetTier, Tier
from ..base import TEMPLATE_MAP, InstanceSpec, smallest_fit
from . import mapping
from .catalog import INSTANCE_CATALOG, index


class AzureTarget:
    name = "azure"
    default_region = "eastus"
    vpc_cidr = mapping.VNET_CIDR
    template_dir = Path(__file__).parent / "templates"
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
        spec = self._index.get(instance_name)
        return spec.monthly_usd if spec else 0.0

    def image_key(self, os: Optional[str]) -> str:
        return mapping.image_key(os)

    def family_for_tier(self, tier: Tier) -> Optional[str]:
        return mapping.FAMILY_BY_TIER.get(tier)

    def subnet_tier_for_tier(self, tier: Tier) -> SubnetTier:
        return mapping.SUBNET_BY_TIER.get(tier, SubnetTier.PRIVATE)

    def sg_for_tier(self, tier: Tier) -> str:
        return mapping.SG_BY_TIER.get(tier, "app-nsg")
