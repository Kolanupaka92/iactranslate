"""GCP Compute Engine machine-type catalog (approx us-central1 on-demand USD/hr).

e2-standard general purpose; n2-highmem memory-optimized; e2-medium for the
smallest workloads. Prices are ballpark; production sizing should use the GCP
Cloud Billing Catalog API.
"""
from __future__ import annotations

from typing import Dict, List

from ..base import InstanceSpec

INSTANCE_CATALOG: List[InstanceSpec] = [
    InstanceSpec("e2-medium", 2, 4.0, "e2", 0.0335),
    InstanceSpec("e2-standard-2", 2, 8.0, "e2-standard", 0.067),
    InstanceSpec("e2-standard-4", 4, 16.0, "e2-standard", 0.134),
    InstanceSpec("e2-standard-8", 8, 32.0, "e2-standard", 0.268),
    InstanceSpec("e2-standard-16", 16, 64.0, "e2-standard", 0.536),
    InstanceSpec("e2-standard-32", 32, 128.0, "e2-standard", 1.072),
    InstanceSpec("n2-highmem-2", 2, 16.0, "n2-highmem", 0.131),
    InstanceSpec("n2-highmem-4", 4, 32.0, "n2-highmem", 0.262),
    InstanceSpec("n2-highmem-8", 8, 64.0, "n2-highmem", 0.524),
    InstanceSpec("n2-highmem-16", 16, 128.0, "n2-highmem", 1.048),
]


def index() -> Dict[str, InstanceSpec]:
    return {i.name: i for i in INSTANCE_CATALOG}
