"""Azure tier/image mappings and default NSG rules."""
from __future__ import annotations

from typing import Dict, List, Optional

from ...models import IngressRule, SubnetTier, Tier

VNET_CIDR = "10.0.0.0/16"

FAMILY_BY_TIER: Dict[Tier, Optional[str]] = {
    Tier.DATABASE: "Eas_v5",
    Tier.CACHE: "Eas_v5",
}

# Network security group name per tier.
SG_BY_TIER: Dict[Tier, str] = {
    Tier.WEB: "web-nsg",
    Tier.APP: "app-nsg",
    Tier.DATABASE: "db-nsg",
    Tier.CACHE: "cache-nsg",
    Tier.OTHER: "app-nsg",
}

SUBNET_BY_TIER: Dict[Tier, SubnetTier] = {
    Tier.WEB: SubnetTier.PUBLIC,
    Tier.APP: SubnetTier.PRIVATE,
    Tier.DATABASE: SubnetTier.PRIVATE,
    Tier.CACHE: SubnetTier.PRIVATE,
    Tier.OTHER: SubnetTier.PRIVATE,
}

DEFAULT_INGRESS: Dict[str, List[IngressRule]] = {
    "web-nsg": [
        IngressRule(description="HTTP", from_port=80, to_port=80, cidr_blocks=["0.0.0.0/0"]),
        IngressRule(description="HTTPS", from_port=443, to_port=443, cidr_blocks=["0.0.0.0/0"]),
    ],
    "app-nsg": [
        IngressRule(description="App tier", from_port=8080, to_port=8080, cidr_blocks=[VNET_CIDR]),
    ],
    "db-nsg": [
        IngressRule(description="PostgreSQL", from_port=5432, to_port=5432, cidr_blocks=[VNET_CIDR]),
        IngressRule(description="MySQL", from_port=3306, to_port=3306, cidr_blocks=[VNET_CIDR]),
        IngressRule(description="MSSQL", from_port=1433, to_port=1433, cidr_blocks=[VNET_CIDR]),
    ],
    "cache-nsg": [
        IngressRule(description="Redis", from_port=6379, to_port=6379, cidr_blocks=[VNET_CIDR]),
    ],
}


def image_key(os_string: Optional[str]) -> str:
    """Map a source OS string to a logical image key (rendered to var.source_image_ids)."""
    if not os_string:
        return "ubuntu-22.04"
    s = os_string.lower()
    if "windows" in s:
        if "2019" in s:
            return "windows-2019"
        if "2016" in s:
            return "windows-2016"
        return "windows-2022"
    if "red hat" in s or "rhel" in s:
        return "rhel-9"
    if "ubuntu" in s:
        return "ubuntu-22.04"
    if "suse" in s or "sles" in s:
        return "sles-15"
    if "centos" in s:
        return "centos-7"
    return "ubuntu-22.04"
