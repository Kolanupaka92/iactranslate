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

from ..costing import estimate_costs
from ..graph import EdgeKind, NodeKind, build_graph
from ..landing_zone import LandingZone, gcp_labels
from ..models import ComputePlan, MigrationPlan, SubnetTier, terraform_safe_name
from ..state import StateBackend, resolve_backend
from ..targets.base import Target


def _rfc1035_slug(value: str) -> str:
    """Lower-case, hyphenated, RFC1035-safe name (for GCP resource names)."""
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not slug or not slug[0].isalpha():
        slug = f"n-{slug}" if slug else "resource"
    return slug[:60].rstrip("-")


def _env(template_dir: Path) -> Environment:
    # Autoescaping is deliberately off, and turning it on would be a bug: these
    # templates emit Terraform HCL, not HTML. HTML-escaping the output would
    # corrupt every `&`, `<` and `"` in generated configuration.
    #
    # Injection is defended at a different layer — `normalize.sanitize_identifier`
    # strips the characters that let a hostile inventory value escape into
    # generated code, once, for all six renderers (ADR 0031). Escaping here would
    # be both wrong and redundant. (nosec: B701 assumes a web template.)
    return Environment(  # nosec B701
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


def build_files(
    plan: MigrationPlan,
    target: Target,
    state_backend: Optional[StateBackend] = None,
    zone: Optional[LandingZone] = None,
) -> Dict[str, str]:
    # Default is an explicit *local* backend, not a silent one: `versions.tf`
    # renders a warning banner and the README explains the consequence. See
    # ADR 0041 — the original defect was that local state was never mentioned.
    state_backend = state_backend or resolve_backend(target.name)
    env = _env(target.template_dir)
    subnet_of = _assign_subnets(plan)
    sg_resource = _sg_resource_map(plan)
    # Resolve each used OS image via the target (data source / marketplace ref /
    # image family) so output deploys with no manual AMI/image editing.
    image_refs = {
        key: {**target.image_reference(key), "resource": terraform_safe_name(key)}
        for key in _image_keys(plan.compute)
    }
    _costs = estimate_costs(plan)
    _mandated = dict((zone or LandingZone()).tags)
    context = {
        "plan": plan,
        # `plan.total_estimated_monthly_cost_usd` is compute only. Templates
        # print an "estimated cost" header a customer reads, so they get the
        # itemized total instead (ADR 0039) — otherwise the generated README
        # contradicts the executive report shipped in the same bundle.
        "costs": _costs,
        "state_backend": state_backend,
        # Tags mandated by the customer's governance. Applied at the provider on
        # AWS/GCP, via a locals block on Azure/OCI, and as tag resources on
        # DigitalOcean — see ADR 0045.
        "mandated_tags": _mandated,
        "gcp_labels": gcp_labels(_mandated),
        "mandated_do_tags": (zone or LandingZone()).do_tags(),
        "state_backend_block": state_backend.terraform_block(),
        # Preformatted with thousands separators: Jinja's `format` filter is
        # printf-style, and "%.2f" rendered the estate total as "$21865.97".
        "cost_total_display": f"{_costs.total:,.2f}",
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
    # A named-but-incomplete backend ships the file the operator fills in.
    # `terraform init` fails without it, which is the intended behaviour: we
    # cannot invent a customer's bucket name, and falling back to local state
    # is precisely the defect this feature exists to remove.
    if not state_backend.is_local and not state_backend.is_complete:
        out["backend.hcl.example"] = state_backend.example_hcl()
    # Drop files that rendered empty — e.g. imports.tf when there are no
    # brownfield resource ids to adopt.
    return {name: content for name, content in out.items() if content.strip()}
