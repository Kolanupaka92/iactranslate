"""Render a validated MigrationPlan into Terraform files via Jinja2.

No AI writes Terraform — templates do, deterministically. `build_files` returns
a mapping of {filename: content} which the packager writes to disk / zips.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..models import ComputePlan, MigrationPlan, SubnetTier, terraform_safe_name
from ..targets.base import Target


def _rfc1035_slug(value: str) -> str:
    """Lower-case, hyphenated, RFC1035-safe name (for GCP resource names)."""
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not slug or not slug[0].isalpha():
        slug = f"n-{slug}" if slug else "resource"
    return slug[:60].rstrip("-")


def _env(template_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
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


def _image_keys(compute: List[ComputePlan]) -> List[str]:
    return sorted({c.image_key for c in compute})


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
                    "lun": i,
                    "is_windows": c.image_key.startswith("windows"),
                }
            )
    return volumes


def build_files(plan: MigrationPlan, target: Target) -> Dict[str, str]:
    env = _env(target.template_dir)
    subnet_of = _assign_subnets(plan)
    sg_resource = {sg.name: sg.resource_name for sg in plan.network.security_groups}
    # Resolve each used OS image via the target (data source / marketplace ref /
    # image family) so output deploys with no manual AMI/image editing.
    image_refs = {
        key: {**target.image_reference(key), "resource": terraform_safe_name(key)}
        for key in _image_keys(plan.compute)
    }
    context = {
        "plan": plan,
        "network": plan.network,
        "compute": plan.compute,
        "region": plan.region,
        "project": plan.project_name,
        "project_slug": _rfc1035_slug(plan.project_name),
        "image_keys": _image_keys(plan.compute),
        "image_refs": image_refs,
        "subnet_of": subnet_of,
        "sg_resource": sg_resource,
        "vm_slug": {c.vm_name: _rfc1035_slug(c.vm_name) for c in plan.compute},
        "volumes": _data_volumes(plan.compute),
        "SubnetTier": SubnetTier,
    }
    out: Dict[str, str] = {}
    for template_name, filename in target.template_map.items():
        out[filename] = env.get_template(template_name).render(**context)
    # Drop files that rendered empty — e.g. imports.tf when there are no
    # brownfield resource ids to adopt.
    return {name: content for name, content in out.items() if content.strip()}
