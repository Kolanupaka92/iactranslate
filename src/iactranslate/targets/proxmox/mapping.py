"""Proxmox VE tier/image mappings and default firewall rules.

The second on-premises destination, and the one the smaller end of the VMware
exodus is actually choosing: Proxmox is the budget hypervisor, and SMBs priced
out of Broadcom's licensing land here more often than on a hyperscaler.

**Images are templates, not names.** Proxmox clones a VM from a *template VM*
identified by numeric id on a node. Every image key resolves to a variable
holding that id; the operator builds the template once (cloud-init enabled)
and points the variable at it. Windows stays Windows — the operator's own
licensed template is cloned like any other.

**Firewalling maps cleanly.** Unlike Nutanix Flow, the Proxmox firewall has a
*security group* — a named, reusable rule set attached to a VM — which is
exactly the shape the plan carries. So the rules are generated for real here,
not recorded as intent.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ...models import IngressRule, SubnetTier, Tier

VPC_CIDR = "10.0.0.0/16"

FAMILY_BY_TIER: Dict[Tier, Optional[str]] = {
    Tier.DATABASE: "mem",
    Tier.CACHE: "mem",
}

SG_BY_TIER: Dict[Tier, str] = {
    Tier.WEB: "web",
    Tier.APP: "app",
    Tier.DATABASE: "db",
    Tier.CACHE: "cache",
    Tier.OTHER: "app",
}

#: Proxmox VMs attach to a Linux bridge, optionally VLAN-tagged. "Public" is
#: the VLAN routed to the edge; a decision the network team owns.
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

#: image key -> the variable holding the template VM id to clone.
TEMPLATE_VARIABLES: Dict[str, str] = {
    "ubuntu-22.04": "template_ubuntu_22_04",
    "rhel-9": "template_rhel_9",
    "rhel-8": "template_rhel_8",
    "sles-15": "template_sles_15",
    "centos-7": "template_centos_7",
    "windows-2022": "template_windows_2022",
    "windows-2019": "template_windows_2019",
    "windows-2016": "template_windows_2016",
    "amazon-linux-2": "template_ubuntu_22_04",
}

#: Proxmox's `ostype` hint, which selects virtio defaults and guest tweaks.
#: `l26` is any modern Linux; `win11` covers Server 2022 and `win10` covers
#: 2016/2019 in Proxmox's own mapping.
OS_TYPE: Dict[str, str] = {
    "windows-2022": "win11",
    "windows-2019": "win10",
    "windows-2016": "win10",
}


def image_reference(image_key: str) -> Dict[str, str]:
    return {
        "variable": TEMPLATE_VARIABLES.get(image_key, TEMPLATE_VARIABLES["ubuntu-22.04"]),
        "ostype": OS_TYPE.get(image_key, "l26"),
    }


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
