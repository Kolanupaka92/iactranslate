"""Stage: turn each normalized VM into a validated ComputePlan.

The provider proposes an instance type; this module re-checks that choice against
the target's catalog (falling back to a deterministic best-fit if the model
proposes something that doesn't exist), computes a cost estimate, and derives the
subnet tier and security group from the VM's tier — all via the target.
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

from ..models import ComputePlan, Environment, NormalizedVM, Tier
from ..pricing import monthly_cost
from ..sizing import effective_demand
from ..targets.base import Target
from .base import LLMProvider

_MIN_ROOT_GIB = 30


def _decision_reason(vm, demand, spec, tier, overridden: bool, suggested: str) -> str:
    """A human-readable explanation of why this instance/tier was chosen."""
    chosen = (
        f"{spec.name} ({spec.vcpu} vCPU / {spec.memory_gib:g} GiB)"
        if spec else "the selected instance"
    )
    if demand.right_sized:
        util = []
        if vm.cpu_util_pct is not None:
            util.append(f"{vm.cpu_util_pct:g}% CPU")
        if vm.mem_util_pct is not None:
            util.append(f"{vm.mem_util_pct:g}% mem")
        util_s = f" at {', '.join(util)}" if util else ""
        basis = (
            f"right-sized from observed utilization: {vm.cpu} vCPU / {vm.memory_gib:g} GiB"
            f"{util_s} → {chosen}"
        )
    else:
        basis = (
            f"sized from allocation (no utilization data): {vm.cpu} vCPU / "
            f"{vm.memory_gib:g} GiB → {chosen}"
        )
    reason = f"{basis}; {tier.value} tier."
    if overridden:
        reason += f" Catalog guardrail: model suggestion '{suggested}' is not a real type."
    return reason


_VERSION_RE = re.compile(r"(\d+(?:\.\d+)?)")
# Every RVTools OS string carries an architecture suffix — "Ubuntu Linux
# (64-bit)". Left in, the "64" reads as a version number and every Ubuntu
# machine gets reported as a substitution. Strip it before comparing.
_ARCH_RE = re.compile(r"\(?\b(?:32|64)[\s-]?bit\)?", re.IGNORECASE)


def os_substitution_note(source_os: Optional[str], image_key: str) -> Optional[str]:
    """Describe an OS version change, or None when the versions agree.

    The image catalog cannot stock every OS a real estate runs. Windows Server
    2012 R2 is a good example: it is past end of life and the clouds no longer
    publish a base image, so a plan *has* to fall forward to a supported
    release. That is defensible; doing it silently is not — a legacy
    application certified against 2012 R2 may simply not run on 2022, and the
    person reviewing the plan is the only one who can judge that.

    So the substitution stays, and it gets stated in the decision's `reason`,
    which flows into `decisions.json` and the executive report.
    """
    if not source_os:
        return None
    source_versions = _VERSION_RE.findall(_ARCH_RE.sub(" ", source_os))
    image_versions = _VERSION_RE.findall(image_key)
    # No version on either side means there is nothing to compare — an OS
    # string like "Ubuntu Linux" simply doesn't say which release it is, and
    # guessing a mismatch would cry wolf on every such machine.
    if not source_versions or not image_versions:
        return None
    # Compare the leading version token of each — "Windows Server 2012 R2"
    # against "windows-2022", "RHEL 8" against "rhel-8".
    if source_versions[0] == image_versions[0]:
        return None
    return (
        f"OS substituted: source reports '{source_os.strip()}' but the plan provisions "
        f"'{image_key}' — no matching image is available. Verify application "
        f"compatibility before migrating."
    )


def _root_and_extra(vm: NormalizedVM) -> Tuple[int, List[int]]:
    disks = [int(math.ceil(d)) for d in vm.disks_gib if d > 0]
    if not disks:
        return _MIN_ROOT_GIB, []
    root = max(_MIN_ROOT_GIB, disks[0])
    return root, disks[1:]


def build_compute_plans(
    vms: List[NormalizedVM],
    provider: LLMProvider,
    tier_env,
    target: Target,
    region: str = "",
    live_pricing: bool = False,
) -> List[ComputePlan]:
    """`tier_env` maps vm_name -> (Tier, Environment) from classification."""
    region = region or target.default_region
    plans: List[ComputePlan] = []

    for vm in vms:
        tier, environment = tier_env.get(vm.vm_name, (Tier.OTHER, Environment.UNKNOWN))

        demand = effective_demand(vm)
        suggestion = provider.rightsize(vm, tier, environment)
        instance_type = suggestion.instance_type
        # Guardrail: never emit an instance type that isn't in the target catalog.
        # Fall back to the same demand model the provider should have used.
        overridden = not target.instance_exists(instance_type)
        if overridden:
            instance_type = target.smallest_fit(
                demand.vcpu, demand.memory_gib, headroom=demand.headroom,
                prefer_family=target.family_for_tier(tier),
            ).name

        spec = target.spec_of(instance_type)
        reason = _decision_reason(vm, demand, spec, tier, overridden, suggestion.instance_type)
        image_key = suggestion.image_key or target.image_key(vm.os)
        substitution = os_substitution_note(vm.os, image_key)
        if substitution:
            reason += f" {substitution}"
        root_gib, extra_gib = _root_and_extra(vm)
        cost, price_source = monthly_cost(
            target.name, instance_type, region, target.cost_of(instance_type), live_pricing
        )

        plans.append(
            ComputePlan(
                vm_name=vm.vm_name,
                resource_name=vm.resource_name,
                instance_type=instance_type,
                image_key=image_key,
                vcpu=spec.vcpu if spec else vm.cpu,
                memory_gib=spec.memory_gib if spec else vm.memory_gib,
                root_volume_gib=root_gib,
                extra_volumes_gib=extra_gib,
                subnet_tier=target.subnet_tier_for_tier(tier),
                security_group=target.sg_for_tier(tier),
                tier=tier,
                environment=environment,
                estimated_monthly_cost_usd=cost,
                price_source=price_source,
                right_sized=demand.right_sized,
                source_vcpu=vm.cpu if demand.right_sized else None,
                source_memory_gib=vm.memory_gib if demand.right_sized else None,
                reason=reason,
                external_id=vm.external_id,
            )
        )
    _ensure_unique_resource_names(plans)
    return plans


def _ensure_unique_resource_names(plans: List[ComputePlan]) -> None:
    """Guarantee every Terraform resource label is unique.

    `terraform_safe_name` maps every non-alphanumeric run to `_`, so distinct
    machines can collapse onto the same label: `web-01`, `web.01`, `WEB_01`,
    and `web 01` all become `web_01`. Real estates contain exactly this — a
    CMDB and DNS rarely agree on separators or case — and the result was
    several `resource "aws_instance" "web_01"` blocks, which Terraform rejects
    as duplicates.

    Collisions are resolved by suffixing in the order the plans were built,
    which is derived from the sorted inventory, so the mapping is stable across
    runs. That stability matters: a label that moved between runs would look to
    Terraform like a resource to destroy and recreate.
    """
    seen: Dict[str, int] = {}
    for plan in plans:
        base = plan.resource_name
        count = seen.get(base, 0)
        seen[base] = count + 1
        if count:
            # `_2`, `_3`, … — and re-check, since the suffixed name could
            # itself collide with a real machine literally called "web_01_2".
            candidate = f"{base}_{count + 1}"
            while candidate in seen:
                count += 1
                candidate = f"{base}_{count + 1}"
            seen[candidate] = 1
            plan.resource_name = candidate
