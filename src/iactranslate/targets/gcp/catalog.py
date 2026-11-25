"""GCP Compute Engine machine-type catalog (approx us-central1 on-demand USD/hr).

e2-standard general purpose; n2-highmem memory-optimized; e2-medium for the
smallest workloads; n2-standard and m1 for the largest. The sizes above
32 vCPU / 128 GiB exist in us-central1 per gcloud; their prices came from a
third-party list (gcloud-compute.com, 2026-11-22), not from Google (ADR 0086).
Prices are ballpark; production sizing should use the GCP
Cloud Billing Catalog API.
"""
from __future__ import annotations

from typing import Dict, List

from ..base import InstanceSpec

INSTANCE_CATALOG: List[InstanceSpec] = [
    InstanceSpec("e2-medium", 2, 4.0, "e2", 0.0335, shared_cpu=True),
    InstanceSpec("e2-standard-2", 2, 8.0, "e2-standard", 0.067),
    InstanceSpec("e2-standard-4", 4, 16.0, "e2-standard", 0.134),
    InstanceSpec("e2-standard-8", 8, 32.0, "e2-standard", 0.268),
    InstanceSpec("e2-standard-16", 16, 64.0, "e2-standard", 0.536),
    InstanceSpec("e2-standard-32", 32, 128.0, "e2-standard", 1.072),
    InstanceSpec("n2-highmem-2", 2, 16.0, "n2-highmem", 0.131),
    InstanceSpec("n2-highmem-4", 4, 32.0, "n2-highmem", 0.262),
    InstanceSpec("n2-highmem-8", 8, 64.0, "n2-highmem", 0.524),
    InstanceSpec("n2-highmem-16", 16, 128.0, "n2-highmem", 1.048),
    InstanceSpec("n2-highmem-32", 32, 256.0, "n2-highmem", 2.0962),
    InstanceSpec("n2-highmem-48", 48, 384.0, "n2-highmem", 3.1443),
    InstanceSpec("n2-highmem-64", 64, 512.0, "n2-highmem", 4.1924),
    InstanceSpec("n2-highmem-96", 96, 768.0, "n2-highmem", 6.2887),
    InstanceSpec("n2-highmem-128", 128, 864.0, "n2-highmem", 7.707),
    InstanceSpec("n2-standard-48", 48, 192.0, "n2-standard", 2.3308),
    InstanceSpec("n2-standard-64", 64, 256.0, "n2-standard", 3.1078),
    InstanceSpec("n2-standard-96", 96, 384.0, "n2-standard", 4.6617),
    InstanceSpec("n2-standard-128", 128, 512.0, "n2-standard", 6.2156),
    InstanceSpec("m1-ultramem-40", 40, 961.0, "m1", 6.2931),
    InstanceSpec("m1-megamem-96", 96, 1433.6, "m1", 10.6522),
    InstanceSpec("m1-ultramem-80", 80, 1922.0, "m1", 12.5862),
    InstanceSpec("m1-ultramem-160", 160, 3844.0, "m1", 25.1724),
]


def index() -> Dict[str, InstanceSpec]:
    return {i.name: i for i in INSTANCE_CATALOG}
