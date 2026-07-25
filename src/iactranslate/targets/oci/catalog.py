"""OCI Compute shape catalog (approx us-ashburn-1 on-demand USD/hr).

OCI's current-generation shapes are "Flex" — a fixed shape family
(`VM.Standard.E4.Flex`, `VM.Standard.E5.Flex`) with the actual OCPU/memory
picked independently via `shape_config`, not a fixed-size SKU the way AWS/Azure
model instances. To fit the shared `InstanceSpec(name, vcpu, memory_gib, ...)`
contract — where `name` must be a stable, unique lookup key — each catalog
entry's `name` encodes both the shape family and its OCPU/memory as
`<shape>-<ocpu>x<mem>` (e.g. `VM.Standard.E4.Flex-2x16`); the OCI compute
template splits on the first `-` to recover the real `shape` value and reads
`c.vcpu`/`c.memory_gib` (already the catalog spec's values post-rightsizing)
directly for `shape_config`. No information is lost — the synthetic suffix
only exists because our model needs a unique catalog key, OCI doesn't.

Pricing: OCPU-hour + GB-hour rates from OCI's published Flex pricing
(approximately $0.025/OCPU-hr + $0.0015/GB-hr for E4.Flex; E5.Flex is the
newer generation at a modest premium, ~$0.03/OCPU-hr + $0.0017/GB-hr).
Ballpark, like the AWS/Azure/GCP catalogs — production sizing should use
OCI's Cost Estimator.
"""
from __future__ import annotations

from typing import Dict, List

from ..base import InstanceSpec

_E4_OCPU_HR = 0.025
_E4_GB_HR = 0.0015
_E5_OCPU_HR = 0.03
_E5_GB_HR = 0.0017


def _e4(ocpu: int, mem_gb: float) -> InstanceSpec:
    price = round(ocpu * _E4_OCPU_HR + mem_gb * _E4_GB_HR, 4)
    return InstanceSpec(f"VM.Standard.E4.Flex-{ocpu}x{int(mem_gb)}", ocpu, mem_gb, "E4.Flex", price)


def _e5(ocpu: int, mem_gb: float) -> InstanceSpec:
    price = round(ocpu * _E5_OCPU_HR + mem_gb * _E5_GB_HR, 4)
    return InstanceSpec(f"VM.Standard.E5.Flex-{ocpu}x{int(mem_gb)}", ocpu, mem_gb, "E5.Flex", price)


INSTANCE_CATALOG: List[InstanceSpec] = [
    # General purpose — E4.Flex, ~1:4 OCPU:GB ratio (mirrors AWS/Azure general-purpose sizing).
    _e4(1, 4),
    _e4(2, 8),
    _e4(4, 16),
    _e4(8, 32),
    _e4(16, 64),
    _e4(32, 128),
    # Memory-optimized — E5.Flex, ~1:8 OCPU:GB ratio (databases, caches).
    _e5(2, 16),
    _e5(4, 32),
    _e5(8, 64),
    _e5(16, 128),
]


def index() -> Dict[str, InstanceSpec]:
    return {i.name: i for i in INSTANCE_CATALOG}
