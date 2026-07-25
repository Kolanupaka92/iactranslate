"""OCI tier/image mappings and default NSG ingress rules."""
from __future__ import annotations

from typing import Dict, List, Optional

from ...models import IngressRule, SubnetTier, Tier

VCN_CIDR = "10.0.0.0/16"

FAMILY_BY_TIER: Dict[Tier, Optional[str]] = {
    Tier.DATABASE: "E5.Flex",
    Tier.CACHE: "E5.Flex",
}

# Network Security Group name per tier (attached to instance VNICs, like an
# AWS security group or an Azure NSG-on-NIC — not OCI's subnet-level Security
# Lists, which would be the wrong analog for a per-tier, per-instance model).
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
        IngressRule(description="App tier", from_port=8080, to_port=8080, cidr_blocks=[VCN_CIDR]),
    ],
    "db-nsg": [
        IngressRule(description="PostgreSQL", from_port=5432, to_port=5432, cidr_blocks=[VCN_CIDR]),
        IngressRule(description="MySQL", from_port=3306, to_port=3306, cidr_blocks=[VCN_CIDR]),
        IngressRule(description="MSSQL", from_port=1433, to_port=1433, cidr_blocks=[VCN_CIDR]),
    ],
    "cache-nsg": [
        IngressRule(description="Redis", from_port=6379, to_port=6379, cidr_blocks=[VCN_CIDR]),
    ],
}


# OS + version to filter OCI's platform images by (via `data "oci_core_images"`,
# resolved at apply time — OCI image OCIDs are region-specific, so there is no
# static, portable id to bake in the way GCP's public image *families* allow).
# RHEL is not a default OCI platform image (needs Marketplace/BYOL); Oracle
# Linux is the binary-compatible platform alternative. Amazon Linux has no OCI
# equivalent — Oracle Linux is the nearest general-purpose fallback there too.
IMAGE_OS: Dict[str, Dict[str, str]] = {
    "ubuntu-22.04": {"operating_system": "Canonical Ubuntu", "operating_system_version": "22.04"},
    "rhel-9": {"operating_system": "Oracle Linux", "operating_system_version": "9"},
    "sles-15": {"operating_system": "SUSE Linux Enterprise Server", "operating_system_version": "15"},
    "centos-7": {"operating_system": "Oracle Linux", "operating_system_version": "9"},
    "windows-2022": {"operating_system": "Windows", "operating_system_version": "Server 2022 Standard"},
    "windows-2019": {"operating_system": "Windows", "operating_system_version": "Server 2019 Standard"},
    "windows-2016": {"operating_system": "Windows", "operating_system_version": "Server 2016 Standard"},
    "amazon-linux-2": {"operating_system": "Oracle Linux", "operating_system_version": "9"},
}

_DEFAULT_IMAGE = IMAGE_OS["ubuntu-22.04"]


def image_reference(image_key: str) -> Dict[str, str]:
    return IMAGE_OS.get(image_key, _DEFAULT_IMAGE)


def image_key(os_string: Optional[str]) -> str:
    """Map a source OS string to a logical image key (rendered to data.oci_core_images)."""
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
