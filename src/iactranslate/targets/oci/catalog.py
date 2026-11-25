"""OCI Compute shape catalog (approx us-ashburn-1 on-demand USD/hr).

OCI's current-generation shapes are "Flex" — a fixed shape family
(`VM.Standard.E4.Flex`, `VM.Standard.E5.Flex`) with the actual OCPU/memory
picked independently via `shape_config`, not a fixed-size SKU the way AWS/Azure
model instances. To fit the shared `InstanceSpec(name, vcpu, memory_gib, ...)`
contract — where `name` must be a stable, unique lookup key — each catalog
entry's `name` encodes both the shape family and its OCPU/memory as
`<shape>-<ocpu>x<mem>` (e.g. `VM.Standard.E4.Flex-2x16`); the OCI compute
template reads the shape and the OCPUs back out of it for `shape_config`. The
synthetic suffix only exists because our model needs a unique catalog key.

**An OCPU is a physical core: two vCPUs on these x86 shapes** (ADR 0088). OCI
sells and prices OCPUs; `InstanceSpec.vcpu` is vCPUs, like every other catalog,
so `-2x16` is 4 vCPU / 16 GiB.

Pricing: $0.025/OCPU-hr + $0.0015/GB-hr for E4.Flex, $0.03 + $0.002 for the newer
E5.Flex, read from Oracle's price API on 2026-11-25. Production sizing should
use OCI's Cost Estimator.
"""
from __future__ import annotations

from typing import Dict, List

from ..base import InstanceSpec

VCPU_PER_OCPU = 2

_E4_OCPU_HR = 0.025
_E4_GB_HR = 0.0015
_E5_OCPU_HR = 0.03
_E5_GB_HR = 0.002


def _e4(ocpu: int, mem_gb: float) -> InstanceSpec:
    price = round(ocpu * _E4_OCPU_HR + mem_gb * _E4_GB_HR, 4)
    return InstanceSpec(f"VM.Standard.E4.Flex-{ocpu}x{int(mem_gb)}", ocpu * VCPU_PER_OCPU, mem_gb, "E4.Flex", price)


def _e5(ocpu: int, mem_gb: float) -> InstanceSpec:
    price = round(ocpu * _E5_OCPU_HR + mem_gb * _E5_GB_HR, 4)
    return InstanceSpec(f"VM.Standard.E5.Flex-{ocpu}x{int(mem_gb)}", ocpu * VCPU_PER_OCPU, mem_gb, "E5.Flex", price)


INSTANCE_CATALOG: List[InstanceSpec] = [
    # E4.Flex, 2 GB per vCPU.
    _e4(1, 4),
    _e4(2, 8),
    _e4(4, 16),
    _e4(8, 32),
    _e4(16, 64),
    _e4(32, 128),
    _e4(48, 192),
    _e4(64, 256),
    # E4.Flex, 4 GB per vCPU: the general-purpose shape of every other catalog.
    _e4(1, 8),
    _e4(2, 16),
    _e4(4, 32),
    _e4(8, 64),
    _e4(16, 128),
    _e4(24, 192),
    _e4(32, 256),
    _e4(48, 384),
    _e4(64, 512),
    _e4(64, 1024),
    # Memory-optimized: E5.Flex, 8 GB per vCPU.
    _e5(1, 16),
    _e5(2, 32),
    _e5(4, 64),
    _e5(8, 128),
    _e5(16, 256),
    _e5(32, 512),
    _e5(64, 1024),
    # Up to each shape's limits: E4.Flex 64 OCPU, E5.Flex 126, 1,024 GB, at most
    # 64 GB per OCPU. Oracle's pages disagree on whether E5 past 94 OCPU is an
    # extended VM; 126 is allowed either way.
    _e5(94, 1024),
    _e5(126, 1024),
]


def index() -> Dict[str, InstanceSpec]:
    return {i.name: i for i in INSTANCE_CATALOG}
