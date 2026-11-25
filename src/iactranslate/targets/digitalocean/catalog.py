"""DigitalOcean Droplet size catalog (approx nyc3 on-demand USD/hr).

Unlike OCI's Flex shapes, DigitalOcean Droplet sizes are real, stable, named
slugs (`s-2vcpu-4gb`, `m-4vcpu-32gb`) that have existed unchanged for years —
no synthetic catalog key needed here, `name` is the actual Terraform `size`
value. "s-" is Basic (shared CPU, general purpose); "g-" is General Purpose and "m-"
Memory-Optimized, both with dedicated CPU. 40 vCPU and 256 GiB are the most any
of them offers.

Pricing: converted from DigitalOcean's published monthly rates (/730 hours) —
ballpark, like every other catalog in this codebase. Production sizing should
use DigitalOcean's own pricing page.
"""
from __future__ import annotations

from typing import Dict, List

from ..base import InstanceSpec

INSTANCE_CATALOG: List[InstanceSpec] = [
    # Basic (shared CPU, general purpose).
    InstanceSpec("s-1vcpu-1gb", 1, 1.0, "s", 0.0082, shared_cpu=True),
    InstanceSpec("s-1vcpu-2gb", 1, 2.0, "s", 0.0164, shared_cpu=True),
    InstanceSpec("s-2vcpu-2gb", 2, 2.0, "s", 0.0247, shared_cpu=True),
    InstanceSpec("s-2vcpu-4gb", 2, 4.0, "s", 0.0329, shared_cpu=True),
    InstanceSpec("s-4vcpu-8gb", 4, 8.0, "s", 0.0658, shared_cpu=True),
    InstanceSpec("s-8vcpu-16gb", 8, 16.0, "s", 0.1315, shared_cpu=True),
    # Memory-optimized (database/cache tiers).
    InstanceSpec("m-2vcpu-16gb", 2, 16.0, "m", 0.1151),
    InstanceSpec("m-4vcpu-32gb", 4, 32.0, "m", 0.2301),
    InstanceSpec("m-8vcpu-64gb", 8, 64.0, "m", 0.4603),
    InstanceSpec("m-16vcpu-128gb", 16, 128.0, "m", 0.9205),
    InstanceSpec("m-24vcpu-192gb", 24, 192.0, "m", 1.3808),
    InstanceSpec("m-32vcpu-256gb", 32, 256.0, "m", 1.8411),
    # General purpose (dedicated CPU), for what the Basic sizes can't hold.
    InstanceSpec("g-16vcpu-64gb", 16, 64.0, "g", 0.6904),
    InstanceSpec("g-32vcpu-128gb", 32, 128.0, "g", 1.3808),
    InstanceSpec("g-40vcpu-160gb", 40, 160.0, "g", 1.7260),
]


def index() -> Dict[str, InstanceSpec]:
    return {i.name: i for i in INSTANCE_CATALOG}
