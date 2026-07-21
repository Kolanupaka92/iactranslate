"""Static AWS EC2 instance catalog used for deterministic rightsizing and for
validating any instance type an LLM proposes.

Prices are approximate us-east-1 on-demand USD/hour and are used only for a
ballpark monthly estimate (730 hrs/mo). They are intentionally conservative and
easy to update; production pricing should come from the AWS Pricing API.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

HOURS_PER_MONTH = 730


@dataclass(frozen=True)
class AWSInstance:
    instance_type: str
    vcpu: int
    memory_gib: float
    family: str
    on_demand_usd_hr: float

    @property
    def monthly_usd(self) -> float:
        return round(self.on_demand_usd_hr * HOURS_PER_MONTH, 2)


# General-purpose (t3 / m5) coverage sufficient for the MVP. Ordered small->large.
INSTANCE_CATALOG: List[AWSInstance] = [
    AWSInstance("t3.micro", 2, 1.0, "t3", 0.0104),
    AWSInstance("t3.small", 2, 2.0, "t3", 0.0208),
    AWSInstance("t3.medium", 2, 4.0, "t3", 0.0416),
    AWSInstance("t3.large", 2, 8.0, "t3", 0.0832),
    AWSInstance("t3.xlarge", 4, 16.0, "t3", 0.1664),
    AWSInstance("t3.2xlarge", 8, 32.0, "t3", 0.3328),
    AWSInstance("m5.large", 2, 8.0, "m5", 0.096),
    AWSInstance("m5.xlarge", 4, 16.0, "m5", 0.192),
    AWSInstance("m5.2xlarge", 8, 32.0, "m5", 0.384),
    AWSInstance("m5.4xlarge", 16, 64.0, "m5", 0.768),
    AWSInstance("m5.8xlarge", 32, 128.0, "m5", 1.536),
    AWSInstance("m5.12xlarge", 48, 192.0, "m5", 2.304),
    AWSInstance("r5.large", 2, 16.0, "r5", 0.126),
    AWSInstance("r5.xlarge", 4, 32.0, "r5", 0.252),
    AWSInstance("r5.2xlarge", 8, 64.0, "r5", 0.504),
    AWSInstance("r5.4xlarge", 16, 128.0, "r5", 1.008),
]


def catalog_index() -> Dict[str, AWSInstance]:
    return {i.instance_type: i for i in INSTANCE_CATALOG}


def instance_exists(instance_type: str) -> bool:
    return instance_type in catalog_index()


def smallest_fit(
    vcpu: int,
    memory_gib: float,
    headroom: float = 1.0,
    prefer_family: Optional[str] = None,
) -> AWSInstance:
    """Return the cheapest instance that fits the requested vCPU and memory.

    `headroom` (>= 1.0) inflates the requirement to leave capacity margin.
    `prefer_family` biases selection toward a family (e.g. "r5" for databases)
    but still falls back to any family if nothing in that family fits.
    """
    need_cpu = vcpu * headroom
    need_mem = memory_gib * headroom

    def candidates(family_filter: Optional[str]) -> List[AWSInstance]:
        fit = [
            i
            for i in INSTANCE_CATALOG
            if i.vcpu >= need_cpu and i.memory_gib >= need_mem
            and (family_filter is None or i.family == family_filter)
        ]
        return sorted(fit, key=lambda i: (i.on_demand_usd_hr, i.vcpu, i.memory_gib))

    if prefer_family:
        preferred = candidates(prefer_family)
        if preferred:
            return preferred[0]

    any_fit = candidates(None)
    if any_fit:
        return any_fit[0]

    # Nothing fits — return the largest available as a best effort.
    return max(INSTANCE_CATALOG, key=lambda i: (i.vcpu, i.memory_gib))
