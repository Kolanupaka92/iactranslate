"""DigitalOcean Droplet size catalog (approx nyc3 on-demand USD/hr).

Unlike OCI's Flex shapes, DigitalOcean Droplet sizes are real, stable, named
slugs (`s-2vcpu-4gb`, `m-4vcpu-32gb`) that have existed unchanged for years —
no synthetic catalog key needed here, `name` is the actual Terraform `size`
value. "s-" is Basic (shared CPU, general purpose); "m-" is Memory-Optimized.

Pricing: converted from DigitalOcean's published monthly rates (/730 hours) —
ballpark, like every other catalog in this codebase. Production sizing should
use DigitalOcean's own pricing page.
"""
from __future__ import annotations

from typing import Dict, List

from ..base import InstanceSpec

INSTANCE_CATALOG: List[InstanceSpec] = [
    # Basic (shared CPU, general purpose).
    InstanceSpec("s-1vcpu-1gb", 1, 1.0, "s", 0.0082),
    InstanceSpec("s-1vcpu-2gb", 1, 2.0, "s", 0.0164),
    InstanceSpec("s-2vcpu-2gb", 2, 2.0, "s", 0.0247),
    InstanceSpec("s-2vcpu-4gb", 2, 4.0, "s", 0.0329),
    InstanceSpec("s-4vcpu-8gb", 4, 8.0, "s", 0.0658),
    InstanceSpec("s-8vcpu-16gb", 8, 16.0, "s", 0.1315),
    # Memory-optimized (database/cache tiers).
    InstanceSpec("m-2vcpu-16gb", 2, 16.0, "m", 0.1151),
    InstanceSpec("m-4vcpu-32gb", 4, 32.0, "m", 0.2301),
    InstanceSpec("m-8vcpu-64gb", 8, 64.0, "m", 0.4603),
]


def index() -> Dict[str, InstanceSpec]:
    return {i.name: i for i in INSTANCE_CATALOG}
