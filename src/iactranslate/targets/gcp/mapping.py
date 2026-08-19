"""GCP tier/image mappings and default firewall ingress."""
from __future__ import annotations

from typing import Dict, List, Optional

from ...models import IngressRule, SubnetTier, Tier

VPC_CIDR = "10.0.0.0/16"

FAMILY_BY_TIER: Dict[Tier, Optional[str]] = {
    Tier.DATABASE: "n2-highmem",
    Tier.CACHE: "n2-highmem",
}

# Firewall/network-tag name per tier (used as GCE network tags — RFC1035).
SG_BY_TIER: Dict[Tier, str] = {
    Tier.WEB: "web-fw",
    Tier.APP: "app-fw",
    Tier.DATABASE: "db-fw",
    Tier.CACHE: "cache-fw",
    Tier.OTHER: "app-fw",
}

SUBNET_BY_TIER: Dict[Tier, SubnetTier] = {
    Tier.WEB: SubnetTier.PUBLIC,
    Tier.APP: SubnetTier.PRIVATE,
    Tier.DATABASE: SubnetTier.PRIVATE,
    Tier.CACHE: SubnetTier.PRIVATE,
    Tier.OTHER: SubnetTier.PRIVATE,
}

DEFAULT_INGRESS: Dict[str, List[IngressRule]] = {
    "web-fw": [
        IngressRule(description="HTTP", from_port=80, to_port=80, cidr_blocks=["0.0.0.0/0"]),
        IngressRule(description="HTTPS", from_port=443, to_port=443, cidr_blocks=["0.0.0.0/0"]),
    ],
    "app-fw": [
        IngressRule(description="App tier", from_port=8080, to_port=8080, cidr_blocks=[VPC_CIDR]),
    ],
    "db-fw": [
        IngressRule(description="PostgreSQL", from_port=5432, to_port=5432, cidr_blocks=[VPC_CIDR]),
        IngressRule(description="MySQL", from_port=3306, to_port=3306, cidr_blocks=[VPC_CIDR]),
        IngressRule(description="MSSQL", from_port=1433, to_port=1433, cidr_blocks=[VPC_CIDR]),
    ],
    "cache-fw": [
        IngressRule(description="Redis", from_port=6379, to_port=6379, cidr_blocks=[VPC_CIDR]),
    ],
}


# Public image family per logical key ("<project>/<family>") — accepted directly
# by google_compute_instance boot_disk.initialize_params.image.
IMAGE_FAMILIES: Dict[str, str] = {
    "ubuntu-22.04": "ubuntu-os-cloud/ubuntu-2204-lts",
    "rhel-9": "rhel-cloud/rhel-9",
    "rhel-8": "rhel-cloud/rhel-8",
    "sles-15": "suse-cloud/sles-15",
    "centos-7": "rocky-linux-cloud/rocky-linux-9",  # CentOS 7 is EOL; Rocky is the successor
    "windows-2022": "windows-cloud/windows-2022",
    "windows-2019": "windows-cloud/windows-2019",
    "windows-2016": "windows-cloud/windows-2016",
    "amazon-linux-2": "debian-cloud/debian-12",  # no Amazon Linux on GCP — Debian fallback
}

_DEFAULT_IMAGE = IMAGE_FAMILIES["ubuntu-22.04"]


def image_reference(image_key: str) -> Dict[str, str]:
    return {"image": IMAGE_FAMILIES.get(image_key, _DEFAULT_IMAGE)}


def image_key(os_string: Optional[str]) -> str:
    """Map a source OS string to a logical image key (rendered to var.image_ids)."""
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
        # Respect the reported major version. Silently returning the newest
        # RHEL we stock would upgrade a certified RHEL 8 estate to RHEL 9
        # without anyone deciding to — a real application-compatibility risk.
        return "rhel-8" if "8" in s else "rhel-9"
    if "ubuntu" in s:
        return "ubuntu-22.04"
    if "suse" in s or "sles" in s:
        return "sles-15"
    if "centos" in s:
        return "centos-7"
    return "ubuntu-22.04"
