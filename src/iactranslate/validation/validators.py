"""Validation layer — never trust LLM output directly.

Runs regardless of provider, on the assembled MigrationPlan, before any Terraform
is rendered. Checks structural and semantic invariants the type system can't:
catalog membership, duplicate resource names, CIDR validity/containment/overlap,
and referential integrity between compute and network.
"""
from __future__ import annotations

import ipaddress
import re
from typing import List

from ..catalog import instance_exists
from ..models import MigrationPlan

_TF_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")


class PlanValidationError(ValueError):
    """Raised when a MigrationPlan fails validation. Carries the issue list."""

    def __init__(self, issues: List[str]) -> None:
        self.issues = issues
        super().__init__("Migration plan failed validation:\n  - " + "\n  - ".join(issues))


def _network(value: str):
    return ipaddress.ip_network(value, strict=False)


def validate_plan(plan: MigrationPlan) -> List[str]:
    """Return a list of issue strings. Empty list means the plan is valid."""
    issues: List[str] = []

    if not plan.compute:
        issues.append("plan has no compute resources")

    # --- Compute: catalog membership, naming, duplicate resource names ---------
    seen_names: dict[str, str] = {}
    for c in plan.compute:
        if not instance_exists(c.instance_type):
            issues.append(f"{c.vm_name}: instance type '{c.instance_type}' is not in the AWS catalog")
        if not _TF_NAME_RE.match(c.resource_name):
            issues.append(f"{c.vm_name}: invalid terraform resource name '{c.resource_name}'")
        if c.resource_name in seen_names:
            issues.append(
                f"duplicate compute resource name '{c.resource_name}' "
                f"({seen_names[c.resource_name]} and {c.vm_name})"
            )
        else:
            seen_names[c.resource_name] = c.vm_name
        if c.root_volume_gib < 8:
            issues.append(f"{c.vm_name}: root volume {c.root_volume_gib} GiB is below the 8 GiB minimum")

    # --- Network: VPC + subnet CIDR validity, containment, overlap -------------
    try:
        vpc = _network(plan.network.vpc_cidr)
    except ValueError:
        issues.append(f"invalid VPC CIDR '{plan.network.vpc_cidr}'")
        vpc = None

    parsed = []
    for sn in plan.network.subnets:
        try:
            net = _network(sn.cidr)
        except ValueError:
            issues.append(f"subnet '{sn.name}': invalid CIDR '{sn.cidr}'")
            continue
        if vpc is not None and not net.subnet_of(vpc):
            issues.append(f"subnet '{sn.name}' ({sn.cidr}) is not within the VPC CIDR {plan.network.vpc_cidr}")
        parsed.append((sn.name, net))

    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            (a_name, a_net), (b_name, b_net) = parsed[i], parsed[j]
            if a_net.overlaps(b_net):
                issues.append(f"subnet CIDR overlap: '{a_name}' ({a_net}) overlaps '{b_name}' ({b_net})")

    # --- Referential integrity: compute -> security groups & subnet tiers ------
    sg_names = {sg.name for sg in plan.network.security_groups}
    subnet_tiers = {sn.tier for sn in plan.network.subnets}
    for c in plan.compute:
        if c.security_group not in sg_names:
            issues.append(f"{c.vm_name}: references undefined security group '{c.security_group}'")
        if c.subnet_tier not in subnet_tiers:
            issues.append(f"{c.vm_name}: no {c.subnet_tier.value} subnet exists for placement")

    # --- Duplicate security-group / subnet resource names ----------------------
    for label, items in (("security group", plan.network.security_groups), ("subnet", plan.network.subnets)):
        seen: set[str] = set()
        for it in items:
            if it.resource_name in seen:
                issues.append(f"duplicate {label} resource name '{it.resource_name}'")
            seen.add(it.resource_name)

    return issues


def assert_valid(plan: MigrationPlan) -> None:
    issues = validate_plan(plan)
    if issues:
        raise PlanValidationError(issues)
