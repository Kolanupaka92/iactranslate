"""Render a validated MigrationPlan into Terraform files via Jinja2.

No AI writes Terraform — templates do, deterministically. `build_files` returns
a mapping of {filename: content} which the packager writes to disk / zips.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..models import ComputePlan, MigrationPlan, SubnetTier

TEMPLATE_DIR = Path(__file__).parent / "templates"

# template file (.j2) -> output filename
_TEMPLATES = {
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


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )


def _assign_subnets(plan: MigrationPlan) -> Dict[str, str]:
    """Map each compute vm_name -> a subnet resource name, spread across AZs."""
    public = [s.resource_name for s in plan.network.subnets if s.tier == SubnetTier.PUBLIC]
    private = [s.resource_name for s in plan.network.subnets if s.tier == SubnetTier.PRIVATE]
    counters = {SubnetTier.PUBLIC: 0, SubnetTier.PRIVATE: 0}
    mapping: Dict[str, str] = {}
    for c in plan.compute:
        pool = public if c.subnet_tier == SubnetTier.PUBLIC else private
        if not pool:  # no subnet of that tier; fall back to any subnet
            pool = [s.resource_name for s in plan.network.subnets]
        idx = counters[c.subnet_tier] % len(pool)
        counters[c.subnet_tier] += 1
        mapping[c.vm_name] = pool[idx]
    return mapping


def _ami_keys(compute: List[ComputePlan]) -> List[str]:
    return sorted({c.ami_key for c in compute})


def _data_volumes(compute: List[ComputePlan]) -> List[dict]:
    """Flatten extra disks into aws_ebs_volume/attachment render records."""
    volumes: List[dict] = []
    for c in compute:
        for i, size in enumerate(c.extra_volumes_gib):
            device = f"/dev/sd{chr(ord('f') + i)}"  # sdf, sdg, ...
            volumes.append(
                {
                    "resource_name": f"{c.resource_name}_data_{i + 1}",
                    "instance_resource": c.resource_name,
                    "vm_name": c.vm_name,
                    "size": size,
                    "device": device,
                }
            )
    return volumes


def build_files(plan: MigrationPlan) -> Dict[str, str]:
    env = _env()
    subnet_of = _assign_subnets(plan)
    sg_resource = {sg.name: sg.resource_name for sg in plan.network.security_groups}
    context = {
        "plan": plan,
        "network": plan.network,
        "compute": plan.compute,
        "region": plan.region,
        "project": plan.project_name,
        "ami_keys": _ami_keys(plan.compute),
        "subnet_of": subnet_of,
        "sg_resource": sg_resource,
        "volumes": _data_volumes(plan.compute),
        "SubnetTier": SubnetTier,
    }
    out: Dict[str, str] = {}
    for template_name, filename in _TEMPLATES.items():
        out[filename] = env.get_template(template_name).render(**context)
    return out
