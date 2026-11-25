"""Cloud-target abstraction — the single seam between the shared pipeline and
cloud-specific catalogs, mappings, and templates.

The parser, normalizer, classification agent, generic models, validation logic,
renderer, and packager are all cloud-agnostic. A `Target` supplies everything
that differs per cloud: the instance/VM-size catalog, tier→family/subnet/security
mappings, OS→image-key detection, and the Jinja2 template set.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Protocol, runtime_checkable

from ..models import IngressRule, SubnetTier, Tier

HOURS_PER_MONTH = 730

# Capability flags a Target advertises, so callers (UI/API/renderers) can enable
# features declaratively instead of branching on `target.name == "aws"`.
CAP_TERRAFORM = "terraform"
CAP_PULUMI = "pulumi"
CAP_GITOPS = "gitops"
CAP_LIVE_PRICING = "live_pricing"
CAP_BROWNFIELD_IMPORT = "brownfield_import"  # emit IaC `import` blocks to adopt an existing fleet
#: The target has a published per-instance price, so its cost can be computed
#: from an inventory alone. Public clouds have this. An on-premises hypervisor
#: does not: its cost is hardware amortisation, licensing and facilities, none
#: of which an inventory contains. A target without this capability is left
#: out of cost ranking entirely rather than scored — a $0 cost would make it
#: "cheapest" by construction and collapse every real cloud's score against it.
CAP_PRICED = "priced"


@dataclass(frozen=True)
class InstanceSpec:
    """A single instance/VM size, cloud-neutral."""

    name: str
    vcpu: int
    memory_gib: float
    family: str
    on_demand_usd_hr: float
    #: CPU that is shared or burstable: cheap, and throttled under steady load.
    shared_cpu: bool = False

    @property
    def monthly_usd(self) -> float:
        return round(self.on_demand_usd_hr * HOURS_PER_MONTH, 2)


def smallest_fit(
    catalog: List[InstanceSpec],
    vcpu: float,
    memory_gib: float,
    steady_cpu: bool = False,
) -> InstanceSpec:
    """Cheapest instance that fits vCPU + memory.

    `steady_cpu` (databases, caches) leaves out shared and burstable CPUs, unless
    nothing else fits. The shape follows the source: a memory-optimized size is
    chosen when it is the cheapest that fits, never by tier alone. Forcing one
    put a 2 GiB database on a 16 GiB machine (ADR 0085).

    If nothing fits, returns the size that covers the most of it. The caller must
    check with `is_undersized` and say so (ADR 0086).
    """
    fit = sorted(
        (i for i in catalog if not is_undersized(i, vcpu, memory_gib)),
        key=lambda i: (i.on_demand_usd_hr, i.vcpu, i.memory_gib),
    )
    if steady_cpu:
        fit = [i for i in fit if not i.shared_cpu] or fit
    if fit:
        return fit[0]

    def covered(i: InstanceSpec) -> float:
        return min(i.vcpu / vcpu, 1.0) * min(i.memory_gib / memory_gib, 1.0)

    return min(catalog, key=lambda i: (-covered(i), i.on_demand_usd_hr, i.vcpu))


def is_undersized(spec: InstanceSpec, vcpu: float, memory_gib: float) -> bool:
    """Less vCPU or less memory than asked for."""
    return spec.vcpu < vcpu or spec.memory_gib < memory_gib


#: Tiers that run hot for long stretches, so a shared or burstable CPU would be
#: throttled just when it is needed.
STEADY_CPU_TIERS = frozenset({Tier.DATABASE, Tier.CACHE})


@runtime_checkable
class Target(Protocol):
    """Everything cloud-specific the pipeline needs, behind one interface."""

    name: str
    default_region: str
    vpc_cidr: str
    template_dir: Path
    template_map: Dict[str, str]
    default_ingress: Dict[str, List[IngressRule]]
    capabilities: "frozenset[str]"  # advertised features, e.g. CAP_BROWNFIELD_IMPORT

    def instance_exists(self, name: str) -> bool: ...

    def instance_names(self) -> List[str]: ...

    def smallest_fit(
        self,
        vcpu: float,
        memory_gib: float,
        steady_cpu: bool = False,
    ) -> InstanceSpec: ...

    def spec_of(self, name: str) -> Optional[InstanceSpec]: ...

    def cost_of(self, instance_name: str) -> float: ...

    def image_key(self, os: Optional[str]) -> str: ...

    def image_reference(self, image_key: str) -> Dict[str, object]:
        """Cloud-specific fields to resolve an OS image in Terraform.

        AWS   -> {"owners": [...], "name": "<filter>"}   (an aws_ami data source)
        Azure -> {"publisher", "offer", "sku"}           (source_image_reference)
        GCP   -> {"image": "<project>/<family>"}          (a public image family)
        """
        ...

    def subnet_tier_for_tier(self, tier: Tier) -> SubnetTier: ...

    def sg_for_tier(self, tier: Tier) -> str: ...


# Template filename set is identical across targets (only contents differ).
TEMPLATE_MAP: Dict[str, str] = {
    "versions.tf.j2": "versions.tf",
    "provider.tf.j2": "provider.tf",
    "variables.tf.j2": "variables.tf",
    "locals.tf.j2": "locals.tf",
    "terraform.tfvars.j2": "terraform.tfvars",
    "networking.tf.j2": "networking.tf",
    "security.tf.j2": "security.tf",
    "identity.tf.j2": "identity.tf",
    # Renders empty unless observed flows show the estate depends on networks
    # that are not migrating; the renderer drops empty files.
    "hybrid.tf.j2": "hybrid.tf",
    "loadbalancer.tf.j2": "loadbalancer.tf",
    "compute.tf.j2": "compute.tf",
    "storage.tf.j2": "storage.tf",
    "outputs.tf.j2": "outputs.tf",
    "main.tf.j2": "main.tf",
    "README.md.j2": "README.md",
}
