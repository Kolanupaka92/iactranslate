"""AWS tier/image mappings and default security-group ingress."""
from __future__ import annotations

from typing import Dict, List, Optional

from ...models import IngressRule, SubnetTier, Tier

VPC_CIDR = "10.0.0.0/16"

SG_BY_TIER: Dict[Tier, str] = {
    Tier.WEB: "web-sg",
    Tier.APP: "app-sg",
    Tier.DATABASE: "db-sg",
    Tier.CACHE: "cache-sg",
    Tier.OTHER: "app-sg",
}

SUBNET_BY_TIER: Dict[Tier, SubnetTier] = {
    Tier.WEB: SubnetTier.PUBLIC,
    Tier.APP: SubnetTier.PRIVATE,
    Tier.DATABASE: SubnetTier.PRIVATE,
    Tier.CACHE: SubnetTier.PRIVATE,
    Tier.OTHER: SubnetTier.PRIVATE,
}

DEFAULT_INGRESS: Dict[str, List[IngressRule]] = {
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


# aws_ami lookup per logical image key: owner account(s) + name-pattern filter.
# most_recent=true is applied in the template so the freshest matching AMI wins.
AMI_FILTERS: Dict[str, Dict[str, object]] = {
    "amazon-linux-2": {"owners": ["amazon"], "name": "amzn2-ami-hvm-*-x86_64-gp2"},
    "windows-2022": {"owners": ["amazon"], "name": "Windows_Server-2022-English-Full-Base-*"},
    "windows-2019": {"owners": ["amazon"], "name": "Windows_Server-2019-English-Full-Base-*"},
    "windows-2016": {"owners": ["amazon"], "name": "Windows_Server-2016-English-Full-Base-*"},
    "ubuntu-22.04": {"owners": ["099720109477"],
                     "name": "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"},
    "rhel-9": {"owners": ["309956199498"], "name": "RHEL-9.*_HVM-*-x86_64-*"},
    "rhel-8": {"owners": ["309956199498"], "name": "RHEL-8.*_HVM-*-x86_64-*"},
    # A service pack digit and an 8-digit date: `sp*-v*` also matched SUSE's
    # bring-your-own-subscription container-host and ECS builds (ADR 0096).
    "sles-15": {"owners": ["013907871322"], "name": "suse-sles-15-sp?-v????????-hvm-ssd-x86_64"},
    # CentOS 7 is end of life and AWS hides its images, so a lookup found none
    # and the whole plan failed. Rocky Linux is its successor (ADR 0096).
    "rocky-9": {"owners": ["792107900819"], "name": "Rocky-9-EC2-Base-9.*x86_64"},
    "centos-7": {"owners": ["792107900819"], "name": "Rocky-9-EC2-Base-9.*x86_64"},
}

_DEFAULT_AMI = AMI_FILTERS["amazon-linux-2"]


def image_reference(image_key: str) -> Dict[str, object]:
    return AMI_FILTERS.get(image_key, _DEFAULT_AMI)


def image_key(os_string: Optional[str]) -> str:
    """Map a source OS string to a logical AMI key (rendered to var.ami_ids)."""
    if not os_string:
        return "amazon-linux-2"
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
        return "rocky-9"  # stated as a substitution in the plan
    return "amazon-linux-2"


def root_device(image_key: str) -> str:
    """The device an image boots from, read from AWS's images on 2026-11-26.

    A block device mapping only resizes and encrypts the boot disk under this
    name. Under any other it adds an empty disk and leaves the image's own boot
    disk as it was: small and unencrypted (ADR 0096).
    """
    return "/dev/xvda" if image_key.startswith("amazon-linux") else "/dev/sda1"
