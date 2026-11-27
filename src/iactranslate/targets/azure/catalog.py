"""Azure VM-size catalog (approx eastus pay-as-you-go Linux USD/hr).

D-series (Das_v5) general purpose; E-series (Eas_v5) memory-optimized; B-series
burstable for the smallest workloads; M-series for the largest. The E-series and
everything above 32 vCPU / 128 GiB were read from the Azure Retail Prices API on
2026-11-24 (ADR 0086). Prices are ballpark; production sizing
should use the Azure Retail Prices API.
"""
from __future__ import annotations

from typing import Dict, List

from ..base import InstanceSpec

INSTANCE_CATALOG: List[InstanceSpec] = [
    # 1-vCPU sizes, read from the Azure Retail Prices API on 2026-11-27. Without
    # them a 1-vCPU machine was sized to 2 vCPUs (ADR 0100). Free Trial
    # subscriptions can't get them (KI-077).
    InstanceSpec("Standard_B1s", 1, 1.0, "B", 0.0104, shared_cpu=True),
    InstanceSpec("Standard_B1ms", 1, 2.0, "B", 0.0207, shared_cpu=True),
    InstanceSpec("Standard_B2s", 2, 4.0, "B", 0.0416, shared_cpu=True),
    InstanceSpec("Standard_D2as_v5", 2, 8.0, "Das_v5", 0.086),
    InstanceSpec("Standard_D4as_v5", 4, 16.0, "Das_v5", 0.172),
    InstanceSpec("Standard_D8as_v5", 8, 32.0, "Das_v5", 0.344),
    InstanceSpec("Standard_D16as_v5", 16, 64.0, "Das_v5", 0.688),
    InstanceSpec("Standard_D32as_v5", 32, 128.0, "Das_v5", 1.376),
    InstanceSpec("Standard_E2as_v5", 2, 16.0, "Eas_v5", 0.113),
    InstanceSpec("Standard_E4as_v5", 4, 32.0, "Eas_v5", 0.226),
    InstanceSpec("Standard_E8as_v5", 8, 64.0, "Eas_v5", 0.452),
    InstanceSpec("Standard_E16as_v5", 16, 128.0, "Eas_v5", 0.904),
    InstanceSpec("Standard_D48as_v5", 48, 192.0, "Das_v5", 2.064),
    InstanceSpec("Standard_D64as_v5", 64, 256.0, "Das_v5", 2.752),
    InstanceSpec("Standard_D96as_v5", 96, 384.0, "Das_v5", 4.128),
    InstanceSpec("Standard_E20as_v5", 20, 160.0, "Eas_v5", 1.13),
    InstanceSpec("Standard_E32as_v5", 32, 256.0, "Eas_v5", 1.808),
    InstanceSpec("Standard_E48as_v5", 48, 384.0, "Eas_v5", 2.712),
    InstanceSpec("Standard_E64as_v5", 64, 512.0, "Eas_v5", 3.616),
    InstanceSpec("Standard_E96as_v5", 96, 672.0, "Eas_v5", 5.424),
    InstanceSpec("Standard_M64s_v2", 64, 1024.0, "Msv2", 6.459),
    InstanceSpec("Standard_M64ms_v2", 64, 1792.0, "Msv2", 10.13),
    InstanceSpec("Standard_M128s_v2", 128, 2048.0, "Msv2", 12.919),
    InstanceSpec("Standard_M128ms_v2", 128, 3892.0, "Msv2", 26.269),
]


def index() -> Dict[str, InstanceSpec]:
    return {i.name: i for i in INSTANCE_CATALOG}
