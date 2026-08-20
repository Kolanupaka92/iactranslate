"""Typed schema contract passed between every stage of the pipeline.

Everything from `normalize` onward flows through these Pydantic models, so a
failure at any stage is localized, serializable, and testable.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class Tier(str, Enum):
    """Application tier a VM belongs to."""

    WEB = "web"
    APP = "app"
    DATABASE = "database"
    CACHE = "cache"
    OTHER = "other"


class Environment(str, Enum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"
    TEST = "test"
    UNKNOWN = "unknown"


class SubnetTier(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


# --------------------------------------------------------------------------- #
# Stage 1: normalized inventory
# --------------------------------------------------------------------------- #


def terraform_safe_name(value: str) -> str:
    """Coerce an arbitrary string into a valid Terraform resource label.

    Terraform identifiers must match [a-zA-Z_][a-zA-Z0-9_-]* — we lower-case,
    replace runs of non-alphanumerics with a single underscore, and ensure the
    first character is a letter or underscore.
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    if not slug:
        slug = "resource"
    if not re.match(r"[a-zA-Z_]", slug[0]):
        slug = f"r_{slug}"
    return slug


class NormalizedVM(BaseModel):
    """A single virtual machine, normalized across source formats.

    Units are canonical: `cpu` in vCPU count, all memory/disk in GiB.
    """

    schema_version: int = Field(default=1, description="Canonical-model version for forward compatibility")
    vm_name: str
    cpu: int = Field(ge=1, description="Allocated vCPU count")
    memory_gib: float = Field(gt=0, description="Allocated RAM in GiB")
    disks_gib: List[float] = Field(default_factory=list, description="Per-disk sizes in GiB")
    # Observed utilization (0-100%), when the source provides it. Enables
    # right-sizing to actual demand instead of translating over-provisioning 1:1.
    cpu_util_pct: Optional[float] = Field(default=None, ge=0, le=100)
    mem_util_pct: Optional[float] = Field(default=None, ge=0, le=100)
    network: Optional[str] = Field(default=None, description="VLAN / port group")
    os: Optional[str] = None
    power_state: Optional[str] = None
    ip_addresses: List[str] = Field(default_factory=list)
    hostname: Optional[str] = None
    cluster: Optional[str] = None
    datacenter: Optional[str] = None
    # Brownfield: the workload's existing cloud resource id (e.g. an EC2
    # instance id), when the source is an existing fleet. Enables generating
    # Terraform `import` blocks so the fleet is adopted, not recreated.
    external_id: Optional[str] = None
    tags: Dict[str, str] = Field(default_factory=dict)

    @field_validator("vm_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("vm_name must not be empty")
        return v

    @property
    def total_disk_gib(self) -> float:
        return round(sum(self.disks_gib), 2)

    @property
    def has_utilization(self) -> bool:
        return self.cpu_util_pct is not None or self.mem_util_pct is not None

    @property
    def resource_name(self) -> str:
        return terraform_safe_name(self.vm_name)


# --------------------------------------------------------------------------- #
# Stage 2: agent outputs
# --------------------------------------------------------------------------- #


class AppGroup(BaseModel):
    """A logical application: a set of VMs grouped by tier and environment."""

    name: str
    environment: Environment = Environment.UNKNOWN
    # vm_name -> tier
    members: Dict[str, Tier] = Field(default_factory=dict)


class ComputePlan(BaseModel):
    """Target compute definition for one source VM."""

    vm_name: str
    resource_name: str
    instance_type: str
    image_key: str = Field(description="Logical OS image selector, e.g. 'windows-2022' / 'ubuntu-22.04'")
    vcpu: int
    memory_gib: float
    root_volume_gib: int = Field(ge=8)
    extra_volumes_gib: List[int] = Field(default_factory=list)
    subnet_tier: SubnetTier = SubnetTier.PRIVATE
    security_group: str = "app-sg"
    tier: Tier = Tier.OTHER
    environment: Environment = Environment.UNKNOWN
    estimated_monthly_cost_usd: float = 0.0
    price_source: str = "static"  # 'static' (catalog) | 'live' (real market price)
    # True when the instance was sized to observed utilization rather than to the
    # source VM's raw allocation. Records the allocation we shrank from.
    right_sized: bool = False
    source_vcpu: Optional[int] = None
    source_memory_gib: Optional[float] = None
    # Explainability: a human-readable "why this instance / tier" for the decision.
    reason: Optional[str] = None
    # The OS the source inventory reported, kept so downstream consumers can ask
    # "what was this before?" without re-parsing the estate.
    source_os: Optional[str] = None
    # True when the plan provisions a different OS *family* than the source ran
    # — a workload that will not boot as-is, not merely a version bump. Carried
    # as a flag because renderers previously inferred it from an `image_key`
    # prefix, which stops being true the moment a target maps Windows to a Linux
    # image (DigitalOcean does; ADR 0023) — exactly the case that most needs the
    # warning.
    os_family_changed: bool = False
    # Brownfield: existing cloud resource id to adopt via a Terraform import block.
    external_id: Optional[str] = None

    @field_validator("resource_name")
    @classmethod
    def _valid_resource_name(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_-]*$", v):
            raise ValueError(f"invalid terraform resource name: {v!r}")
        return v


class IngressRule(BaseModel):
    description: str
    protocol: str = "tcp"
    from_port: int
    to_port: int
    cidr_blocks: List[str] = Field(default_factory=lambda: ["0.0.0.0/0"])


class SecurityGroup(BaseModel):
    name: str
    resource_name: str
    description: str = ""
    ingress: List[IngressRule] = Field(default_factory=list)


class Subnet(BaseModel):
    name: str
    resource_name: str
    cidr: str
    tier: SubnetTier
    availability_zone_index: int = 0


class LoadBalancerListener(BaseModel):
    """One listener/target-port pair, derived from the fronted tier's SG ingress."""

    protocol: str = "HTTP"
    listener_port: int
    target_port: int


class LoadBalancerPlan(BaseModel):
    """An internet-facing or internal load balancer fronting a multi-instance tier.

    Modeled whenever an (tier, environment) group has more than one instance —
    a single web-tier VM has nothing to front; two or more is exactly the case
    real production traffic is architected behind a load balancer, not pointed
    at an individual instance.
    """

    name: str
    resource_name: str
    tier: Tier
    environment: Environment
    subnet_tier: SubnetTier  # PUBLIC -> internet-facing, PRIVATE -> internal
    security_group: str
    listeners: List[LoadBalancerListener] = Field(default_factory=list)
    health_check_path: str = "/"
    targets: List[str] = Field(default_factory=list, description="vm_name of each fronted instance")

    @property
    def internet_facing(self) -> bool:
        return self.subnet_tier == SubnetTier.PUBLIC


class NetworkPlan(BaseModel):
    vpc_cidr: str = "10.0.0.0/16"
    subnets: List[Subnet] = Field(default_factory=list)
    security_groups: List[SecurityGroup] = Field(default_factory=list)
    load_balancers: List[LoadBalancerPlan] = Field(default_factory=list)
    internet_gateway: bool = True
    nat_gateway: bool = True


# --------------------------------------------------------------------------- #
# Stage 3: the validated plan the generator consumes
# --------------------------------------------------------------------------- #


class MigrationPlan(BaseModel):
    schema_version: int = Field(default=1, description="Canonical-plan version for forward compatibility")
    project_name: str
    source_platform: str = "vmware"
    target: str = "aws"
    region: str = "us-east-1"
    network: NetworkPlan
    compute: List[ComputePlan]
    app_groups: List[AppGroup] = Field(default_factory=list)
    provider_used: str = Field(
        default="rule",
        description=(
            "The decision engine that actually classified/sized this plan: "
            "'rule' (deterministic) or 'anthropic' (Claude). Requesting 'anthropic' "
            "without an API key silently falls back to 'rule' (see agents/providers) — "
            "this field is the honest record of what actually ran, not what was asked for."
        ),
    )

    @property
    def total_estimated_monthly_cost_usd(self) -> float:
        return round(sum(c.estimated_monthly_cost_usd for c in self.compute), 2)

    @property
    def vm_count(self) -> int:
        return len(self.compute)

    @property
    def pricing_source(self) -> str:
        """'live' if any instance was priced from a live source, else 'static'."""
        return "live" if any(c.price_source == "live" for c in self.compute) else "static"
