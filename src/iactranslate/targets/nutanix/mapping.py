"""Nutanix AHV tier/image mappings and default firewall intent.

The first target that is not a public cloud, and the reason it exists is
strategic rather than technical: most of the VMware exodus is going to another
on-premises hypervisor, not to a hyperscaler, and no hyperscaler's migration
tool will ever say "stay on-prem, here is how". A neutral recommendation that
can only ever answer "which cloud" is not neutral about the biggest question.

**Images are yours, not a marketplace's.** There is no public image catalog on
a Nutanix cluster; images are uploaded by the operator to Prism Central. Every
image key resolves to a *variable* naming the uploaded image, and the operator
fills it in. This is the honest shape — inventing an image name that does not
exist on their cluster would fail at apply.

**Windows works.** Unlike DigitalOcean, a Nutanix cluster runs whatever the
operator uploads, so Windows source VMs stay Windows and the OS-substitution
check (ADR 0035) has nothing to report.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ...models import IngressRule, SubnetTier, Tier

VPC_CIDR = "10.0.0.0/16"

FAMILY_BY_TIER: Dict[Tier, Optional[str]] = {
    Tier.DATABASE: "mem",
    Tier.CACHE: "mem",
}

#: Nutanix's grouping primitive is the *category* (a key/value label a Flow
#: microsegmentation policy targets), not a security group attached to a NIC.
#: These names become category values, and the ingress rules below are the
#: policy intent recorded for the operator — see security.tf.j2 for why the
#: Flow rules themselves are not generated.
SG_BY_TIER: Dict[Tier, str] = {
    Tier.WEB: "web",
    Tier.APP: "app",
    Tier.DATABASE: "db",
    Tier.CACHE: "cache",
    Tier.OTHER: "app",
}

#: AHV subnets are VLAN-backed. "Public" here means the VLAN with a route to
#: the internet edge, which on-premises is a routing decision the network team
#: owns — the generated subnet declares the intent, the VLAN id is a variable.
SUBNET_BY_TIER: Dict[Tier, SubnetTier] = {
    Tier.WEB: SubnetTier.PUBLIC,
    Tier.APP: SubnetTier.PRIVATE,
    Tier.DATABASE: SubnetTier.PRIVATE,
    Tier.CACHE: SubnetTier.PRIVATE,
    Tier.OTHER: SubnetTier.PRIVATE,
}

DEFAULT_INGRESS: Dict[str, List[IngressRule]] = {
    "web": [
        IngressRule(description="HTTP", from_port=80, to_port=80, cidr_blocks=["0.0.0.0/0"]),
        IngressRule(description="HTTPS", from_port=443, to_port=443, cidr_blocks=["0.0.0.0/0"]),
    ],
    "app": [
        IngressRule(description="App tier", from_port=8080, to_port=8080, cidr_blocks=[VPC_CIDR]),
    ],
    "db": [
        IngressRule(description="PostgreSQL", from_port=5432, to_port=5432, cidr_blocks=[VPC_CIDR]),
        IngressRule(description="MySQL", from_port=3306, to_port=3306, cidr_blocks=[VPC_CIDR]),
        IngressRule(description="MSSQL", from_port=1433, to_port=1433, cidr_blocks=[VPC_CIDR]),
    ],
    "cache": [
        IngressRule(description="Redis", from_port=6379, to_port=6379, cidr_blocks=[VPC_CIDR]),
    ],
}

#: image key -> the Terraform variable that names the uploaded image. Windows
#: is a first-class citizen here; the operator uploads their own licensed ISO
#: or template, exactly as they do on VMware today.
IMAGE_VARIABLES: Dict[str, str] = {
    "ubuntu-22.04": "image_ubuntu_22_04",
    "rhel-9": "image_rhel_9",
    "rhel-8": "image_rhel_8",
    "sles-15": "image_sles_15",
    "centos-7": "image_centos_7",
    "windows-2022": "image_windows_2022",
    "windows-2019": "image_windows_2019",
    "windows-2016": "image_windows_2016",
    "amazon-linux-2": "image_ubuntu_22_04",
}


def image_reference(image_key: str) -> Dict[str, str]:
    return {"variable": IMAGE_VARIABLES.get(image_key, IMAGE_VARIABLES["ubuntu-22.04"])}


def image_key(os_string: Optional[str]) -> str:
    """Map a source OS string to a logical image key. Windows stays Windows."""
    if not os_string:
        return "ubuntu-22.04"
    s = os_string.lower()
    if "windows" in s:
        if "2022" in s:
            return "windows-2022"
        if "2016" in s:
            return "windows-2016"
        return "windows-2019"
    if "red hat" in s or "rhel" in s:
        return "rhel-8" if "8" in s else "rhel-9"
    if "ubuntu" in s:
        return "ubuntu-22.04"
    if "suse" in s or "sles" in s:
        return "sles-15"
    if "centos" in s:
        return "centos-7"
    return "ubuntu-22.04"
