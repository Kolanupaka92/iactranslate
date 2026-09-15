"""What Nutanix AHV and Proxmox VE share: VMs of any size, and no price list.

Every public-cloud target here picks from a catalog of fixed instance types.
A hypervisor has no such thing — a VM is whatever vCPU and memory you give it.
That is both freedom and a trap: "carry every allocation across exactly" would
reproduce the over-provisioning the customer is trying to leave behind, and it
would make right-sizing (ADR 0064) meaningless, because there would be nothing
to right-size *to*.

So both targets share one standard grid of sizes. It is a recommendation, not a
platform constraint — an operator can set any value after generation — and it
gives `smallest_fit` a real set to choose from, exactly as OCI's Flex shapes
needed a synthetic catalog for the same reason.

**Every price here is zero, and that is not a claim that it is free.** An
on-premises VM costs hardware amortisation, hypervisor licensing, power,
cooling, floor space and the people who run it — none of which an inventory
contains. Rather than invent a number, these targets carry no `CAP_PRICED`
capability, which keeps them out of cost ranking altogether (see
`recommend.py`). A $0 that was allowed into the comparison would make the
hypervisor "cheapest" by construction and flatten every real cloud's score
against a zero baseline — the most flattering lie the tool could tell.
"""
from __future__ import annotations

from typing import Dict, List

from .base import InstanceSpec

#: Balanced (1 vCPU : 4 GiB) and memory-heavy (1 : 8) shapes, named by what they
#: are so the generated code reads as a size and not as a cloud SKU.
INSTANCE_CATALOG: List[InstanceSpec] = [
    InstanceSpec("std-1x4", 1, 4.0, "std", 0.0),
    InstanceSpec("std-2x8", 2, 8.0, "std", 0.0),
    InstanceSpec("std-4x16", 4, 16.0, "std", 0.0),
    InstanceSpec("std-8x32", 8, 32.0, "std", 0.0),
    InstanceSpec("std-16x64", 16, 64.0, "std", 0.0),
    InstanceSpec("std-32x128", 32, 128.0, "std", 0.0),
    InstanceSpec("std-64x256", 64, 256.0, "std", 0.0),
    InstanceSpec("mem-2x16", 2, 16.0, "mem", 0.0),
    InstanceSpec("mem-4x32", 4, 32.0, "mem", 0.0),
    InstanceSpec("mem-8x64", 8, 64.0, "mem", 0.0),
    InstanceSpec("mem-16x128", 16, 128.0, "mem", 0.0),
    InstanceSpec("mem-32x256", 32, 256.0, "mem", 0.0),
]


def index() -> Dict[str, InstanceSpec]:
    return {i.name: i for i in INSTANCE_CATALOG}


#: The sentence every on-premises README carries, so nobody reads a $0 as free.
UNPRICED_NOTE = (
    "Cost is not estimated for this target. An on-premises VM costs hardware "
    "amortisation, hypervisor licensing, power, cooling and operations — none of "
    "which an inventory export contains, and none of which this tool will invent. "
    "To compare against the public-cloud figures, supply your current "
    "on-premises spend per workload."
)
