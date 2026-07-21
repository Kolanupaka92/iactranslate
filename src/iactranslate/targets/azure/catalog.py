"""Azure VM-size catalog (approx eastus pay-as-you-go Linux USD/hr).

D-series (Das_v5) general purpose; E-series (Eas_v5) memory-optimized; B-series
burstable for the smallest workloads. Prices are ballpark; production sizing
should use the Azure Retail Prices API.
"""
from __future__ import annotations

from typing import Dict, List

from ..base import InstanceSpec

INSTANCE_CATALOG: List[InstanceSpec] = [
    InstanceSpec("Standard_B2s", 2, 4.0, "B", 0.0416),
    InstanceSpec("Standard_D2as_v5", 2, 8.0, "Das_v5", 0.086),
    InstanceSpec("Standard_D4as_v5", 4, 16.0, "Das_v5", 0.172),
    InstanceSpec("Standard_D8as_v5", 8, 32.0, "Das_v5", 0.344),
    InstanceSpec("Standard_D16as_v5", 16, 64.0, "Das_v5", 0.688),
    InstanceSpec("Standard_D32as_v5", 32, 128.0, "Das_v5", 1.376),
    InstanceSpec("Standard_E2as_v5", 2, 16.0, "Eas_v5", 0.126),
    InstanceSpec("Standard_E4as_v5", 4, 32.0, "Eas_v5", 0.252),
    InstanceSpec("Standard_E8as_v5", 8, 64.0, "Eas_v5", 0.504),
    InstanceSpec("Standard_E16as_v5", 16, 128.0, "Eas_v5", 1.008),
]


def index() -> Dict[str, InstanceSpec]:
    return {i.name: i for i in INSTANCE_CATALOG}
