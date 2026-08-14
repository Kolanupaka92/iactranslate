"""Render a validated MigrationPlan into Terraform files via Jinja2.

No AI writes Terraform — templates do, deterministically. `build_files` returns
a mapping of {filename: content} which the packager writes to disk / zips.
"""
from __future__ import annotations

import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..graph import EdgeKind, NodeKind, build_graph
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
    """Map each compute vm_name -> a subnet resource name.

    Derived from the Infrastructure Graph's `placed_in` edges (see
    `graph.assign_subnets` / ADR 0016) rather than re-deriving placement here,
    so Terraform/Pulumi and the graph-based renderers (CloudFormation, Bicep,
    CDK) and the diagram can never disagree about where an instance lands.
    """
    graph = build_graph(plan)
    subnet_resource_by_id = {n.id: n.id.split(":", 1)[1] for n in graph.nodes_of(NodeKind.SUBNET)}
    mapping: Dict[str, str] = {}
    for inst in graph.nodes_of(NodeKind.INSTANCE):
        placed_in = graph.out_edges(inst.id, EdgeKind.PLACED_IN)
        if placed_in:
            mapping[inst.name] = subnet_resource_by_id[placed_in[0].target]
    return mapping


def _sg_resource_map(plan: MigrationPlan) -> Dict[str, str]:
    """Map each security-group name -> its resource name, via the graph."""
    graph = build_graph(plan)
    return {n.name: n.id.split(":", 1)[1] for n in graph.nodes_of(NodeKind.SECURITY_GROUP)}


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


def split_threshold() -> int:
    """Workload count above which `compute.tf` is split. 0 disables splitting."""
    try:
        return int(os.getenv("IACTRANSLATE_SPLIT_COMPUTE_ABOVE", "50"))
    except ValueError:
        return 50


def _compute_split(
    filename: str, compute: List[ComputePlan]
) -> Optional[List[Tuple[str, List[ComputePlan]]]]:
    """Group compute resources into per-file slices, or None to keep one file.

    A 5,000-VM estate otherwise renders a single ~95,000-line `compute.tf`.
    That is valid Terraform and completely unreviewable — and "reviewable IaC"
    is the product's whole claim, so a file nobody opens is a real defect.

    Splitting is **purely organizational**: Terraform loads every `.tf` in a
    directory as one configuration, so this changes no resource address, no
    dependency, and no state. Nothing needs migrating, and small projects are
    left as a single file because one short file is genuinely nicer to read
    than six tiny ones.

    Grouping is by environment then tier, which is the conventional split
    (by environment, then by component) and happens to match how migrations
    are actually executed and reviewed — the same two signals the wave planner
    sequences on (ADR 0024).
    """
    threshold = split_threshold()
    if not filename.startswith("compute") or threshold <= 0 or len(compute) <= threshold:
        return None

    grouped: "OrderedDict[str, List[ComputePlan]]" = OrderedDict()
    for c in sorted(compute, key=lambda x: (x.environment.value, x.tier.value, x.vm_name)):
        grouped.setdefault(f"{c.environment.value}-{c.tier.value}", []).append(c)
    return list(grouped.items())


def build_files(plan: MigrationPlan, target: Target) -> Dict[str, str]:
    env = _env(target.template_dir)
    subnet_of = _assign_subnets(plan)
    sg_resource = _sg_resource_map(plan)
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
        "fronted_vm_names": {vm for lb in plan.network.load_balancers for vm in lb.targets},
        "lb_resource_of": {vm: lb.resource_name for lb in plan.network.load_balancers for vm in lb.targets},
        "has_https_listener": any(
            listener.protocol == "HTTPS" for lb in plan.network.load_balancers for listener in lb.listeners
        ),
        "vm_slug": {c.vm_name: _rfc1035_slug(c.vm_name) for c in plan.compute},
        "volumes": _data_volumes(plan.compute),
        "SubnetTier": SubnetTier,
    }
    out: Dict[str, str] = {}
    for template_name, filename in target.template_map.items():
        template = env.get_template(template_name)
        groups = _compute_split(filename, plan.compute)
        if groups is None:
            out[filename] = template.render(**context)
            continue
        # Render the compute template once per group, each over its own slice.
        for suffix, subset in groups:
            name = filename[: -len(".tf")] if filename.endswith(".tf") else filename
            out[f"{name}-{suffix}.tf"] = template.render(**{**context, "compute": subset})
    # Drop files that rendered empty — e.g. imports.tf when there are no
    # brownfield resource ids to adopt.
    return {name: content for name, content in out.items() if content.strip()}
