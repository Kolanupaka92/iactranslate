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


# Marketplace image per logical key (publisher / offer / sku); version "latest"
# is applied in the template. Used in azurerm source_image_reference.
IMAGE_REFS: Dict[str, Dict[str, str]] = {
    "ubuntu-22.04": {"publisher": "Canonical", "offer": "0001-com-ubuntu-server-jammy", "sku": "22_04-lts-gen2"},
    "rhel-9": {"publisher": "RedHat", "offer": "RHEL", "sku": "9-lvm-gen2"},
    "sles-15": {"publisher": "SUSE", "offer": "sles-15-sp5", "sku": "gen2"},
    "centos-7": {"publisher": "OpenLogic", "offer": "CentOS", "sku": "7_9-gen2"},
    "windows-2022": {
        "publisher": "MicrosoftWindowsServer",
        "offer": "WindowsServer",
        "sku": "2022-datacenter-azure-edition",
    },
    "windows-2019": {
        "publisher": "MicrosoftWindowsServer",
        "offer": "WindowsServer",
        "sku": "2019-datacenter-gensecond",
    },
    "windows-2016": {
        "publisher": "MicrosoftWindowsServer",
        "offer": "WindowsServer",
        "sku": "2016-datacenter-gensecond",
    },
    # Amazon Linux has no Azure image — fall back to Ubuntu LTS.
    "amazon-linux-2": {"publisher": "Canonical", "offer": "0001-com-ubuntu-server-jammy", "sku": "22_04-lts-gen2"},
}

_DEFAULT_IMAGE = IMAGE_REFS["ubuntu-22.04"]


def image_reference(image_key: str) -> Dict[str, str]:
    return IMAGE_REFS.get(image_key, _DEFAULT_IMAGE)


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
