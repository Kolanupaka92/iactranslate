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


@dataclass(frozen=True)
class InstanceSpec:
    """A single instance/VM size, cloud-neutral."""

    name: str
    vcpu: int
    memory_gib: float
    family: str
    on_demand_usd_hr: float

    @property
    def monthly_usd(self) -> float:
        return round(self.on_demand_usd_hr * HOURS_PER_MONTH, 2)


def smallest_fit(
    catalog: List[InstanceSpec],
    vcpu: int,
    memory_gib: float,
    headroom: float = 1.0,
    prefer_family: Optional[str] = None,
) -> InstanceSpec:
    """Cheapest instance that fits vCPU + memory (× headroom).

    `prefer_family` biases toward a family (e.g. memory-optimized for databases)
    but falls back to any family if nothing in that family fits. If nothing fits
    at all, returns the largest available as a best effort.
    """
    need_cpu = vcpu * headroom
    need_mem = memory_gib * headroom

    def candidates(family: Optional[str]) -> List[InstanceSpec]:
        fit = [
            i
            for i in catalog
            if i.vcpu >= need_cpu
            and i.memory_gib >= need_mem
            and (family is None or i.family == family)
        ]
        return sorted(fit, key=lambda i: (i.on_demand_usd_hr, i.vcpu, i.memory_gib))

    if prefer_family:
        preferred = candidates(prefer_family)
        if preferred:
            return preferred[0]

    any_fit = candidates(None)
    if any_fit:
        return any_fit[0]

    return max(catalog, key=lambda i: (i.vcpu, i.memory_gib))


@runtime_checkable
class Target(Protocol):
    """Everything cloud-specific the pipeline needs, behind one interface."""

    name: str
    default_region: str
    vpc_cidr: str
    template_dir: Path
    template_map: Dict[str, str]
    default_ingress: Dict[str, List[IngressRule]]

    def instance_exists(self, name: str) -> bool: ...

    def instance_names(self) -> List[str]: ...

    def smallest_fit(
        self,
        vcpu: int,
        memory_gib: float,
        headroom: float = 1.0,
        prefer_family: Optional[str] = None,
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

    def family_for_tier(self, tier: Tier) -> Optional[str]: ...

    def subnet_tier_for_tier(self, tier: Tier) -> SubnetTier: ...

    def sg_for_tier(self, tier: Tier) -> str: ...


# Template filename set is identical across targets (only contents differ).
TEMPLATE_MAP: Dict[str, str] = {
    "versions.tf.j2": "versions.tf",
    "provider.tf.j2": "provider.tf",
    "variables.tf.j2": "variables.tf",
    "terraform.tfvars.j2": "terraform.tfvars",
    "networking.tf.j2": "networking.tf",
    "security.tf.j2": "security.tf",
    "compute.tf.j2": "compute.tf",
    "storage.tf.j2": "storage.tf",
    "outputs.tf.j2": "outputs.tf",
    "main.tf.j2": "main.tf",
    "README.md.j2": "README.md",
}
