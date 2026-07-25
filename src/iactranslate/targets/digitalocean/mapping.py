"""DigitalOcean tier/image mappings and default firewall ingress.

**Honest platform gap:** DigitalOcean has no Windows Server image in its base
catalog — Droplets are Linux-only unless you bring a custom image. Windows
source VMs fall back to Ubuntu here (so the pipeline still produces a valid
plan) but this is flagged loudly in the generated README, not silently
papered over. If the estate has real Windows workloads, DigitalOcean is
likely the wrong target for them.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ...models import IngressRule, SubnetTier, Tier

VPC_CIDR = "10.0.0.0/16"

FAMILY_BY_TIER: Dict[Tier, Optional[str]] = {
    Tier.DATABASE: "m",
    Tier.CACHE: "m",
}

# Firewall name per tier — DigitalOcean firewalls attach by tag, not by
# subnet or NIC, so this doubles as the Droplet tag every instance in that
# tier carries.
SG_BY_TIER: Dict[Tier, str] = {
    Tier.WEB: "web-fw",
    Tier.APP: "app-fw",
    Tier.DATABASE: "db-fw",
    Tier.CACHE: "cache-fw",
    Tier.OTHER: "app-fw",
}

# DigitalOcean has no managed NAT gateway product and no public/private subnet
# distinction the way AWS/Azure/GCP/OCI do — every Droplet in a VPC gets a
# private VPC IP, and a public IP only if explicitly assigned. There is no
# "private subnet with NAT egress" to model, so every tier maps to PUBLIC
# here; whether a Droplet actually gets a public IP is still controlled by
# tier (see compute.tf.j2) — the distinction just isn't subnet-based on DO.
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


# DigitalOcean image slugs — real, stable, accepted directly by
# digitalocean_droplet.image. CentOS was retired from DO's base image catalog
# in 2023; Rocky Linux is DO's own documented successor. RHEL and SLES aren't
# offered at all; Rocky/Ubuntu are the nearest available alternatives. Windows
# isn't offered at all — see the module docstring.
IMAGE_SLUGS: Dict[str, str] = {
    "ubuntu-22.04": "ubuntu-22-04-x64",
    "rhel-9": "rockylinux-9-x64",
    "sles-15": "ubuntu-22-04-x64",
    "centos-7": "rockylinux-9-x64",
    "windows-2022": "ubuntu-22-04-x64",
    "windows-2019": "ubuntu-22-04-x64",
    "windows-2016": "ubuntu-22-04-x64",
    "amazon-linux-2": "ubuntu-22-04-x64",
}

_DEFAULT_IMAGE = IMAGE_SLUGS["ubuntu-22.04"]


def image_reference(image_key: str) -> Dict[str, str]:
    return {"slug": IMAGE_SLUGS.get(image_key, _DEFAULT_IMAGE)}


def image_key(os_string: Optional[str]) -> str:
    """Map a source OS string to a logical image key (rendered to a DO image slug)."""
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
