"""AWS EC2 instance catalog (approx us-east-1 on-demand USD/hr).

Used for deterministic rightsizing and to validate any instance an LLM proposes.
Prices are ballpark; production sizing should use the AWS Pricing API. The sizes
above 48 vCPU / 192 GiB were read from AWS's published us-east-1 rates on
2026-11-24 (ADR 0086).
"""
from __future__ import annotations

from typing import Dict, List

from ..base import InstanceSpec

INSTANCE_CATALOG: List[InstanceSpec] = [
    InstanceSpec("t3.micro", 2, 1.0, "t3", 0.0104, shared_cpu=True),
    InstanceSpec("t3.small", 2, 2.0, "t3", 0.0208, shared_cpu=True),
    InstanceSpec("t3.medium", 2, 4.0, "t3", 0.0416, shared_cpu=True),
    InstanceSpec("t3.large", 2, 8.0, "t3", 0.0832, shared_cpu=True),
    InstanceSpec("t3.xlarge", 4, 16.0, "t3", 0.1664, shared_cpu=True),
    InstanceSpec("t3.2xlarge", 8, 32.0, "t3", 0.3328, shared_cpu=True),
    InstanceSpec("m5.large", 2, 8.0, "m5", 0.096),
    InstanceSpec("m5.xlarge", 4, 16.0, "m5", 0.192),
    InstanceSpec("m5.2xlarge", 8, 32.0, "m5", 0.384),
    InstanceSpec("m5.4xlarge", 16, 64.0, "m5", 0.768),
    InstanceSpec("m5.8xlarge", 32, 128.0, "m5", 1.536),
    InstanceSpec("m5.12xlarge", 48, 192.0, "m5", 2.304),
    InstanceSpec("r5.large", 2, 16.0, "r5", 0.126),
    InstanceSpec("r5.xlarge", 4, 32.0, "r5", 0.252),
    InstanceSpec("r5.2xlarge", 8, 64.0, "r5", 0.504),
    InstanceSpec("r5.4xlarge", 16, 128.0, "r5", 1.008),
    InstanceSpec("m5.16xlarge", 64, 256.0, "m5", 3.072),
    InstanceSpec("m5.24xlarge", 96, 384.0, "m5", 4.608),
    InstanceSpec("r5.8xlarge", 32, 256.0, "r5", 2.016),
    InstanceSpec("r5.12xlarge", 48, 384.0, "r5", 3.024),
    InstanceSpec("r5.16xlarge", 64, 512.0, "r5", 4.032),
    InstanceSpec("r5.24xlarge", 96, 768.0, "r5", 6.048),
    InstanceSpec("x2idn.16xlarge", 64, 1024.0, "x2idn", 6.669),
    InstanceSpec("x2idn.24xlarge", 96, 1536.0, "x2idn", 10.0035),
    InstanceSpec("x2idn.32xlarge", 128, 2048.0, "x2idn", 13.338),
    InstanceSpec("x2iedn.32xlarge", 128, 4096.0, "x2iedn", 26.676),
]


def index() -> Dict[str, InstanceSpec]:
    return {i.name: i for i in INSTANCE_CATALOG}
