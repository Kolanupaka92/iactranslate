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

from ..evidence import sizing_decision
from ..models import ComputePlan, Environment, NormalizedVM, Tier
from ..pricing import monthly_cost
from ..sizing import effective_demand, fit
from ..targets.base import CAP_BROWNFIELD_IMPORT, STEADY_CPU_TIERS, Target, is_undersized
from .base import LLMProvider

_MIN_ROOT_GIB = 30


def _shortfall(demand, spec) -> Optional[str]:
    """Where a size can't hold the demand, or only without the usual headroom."""
    need = f"{demand.vcpu:g} vCPU / {demand.memory_gib:g} GiB"
    if is_undersized(spec, demand.vcpu, demand.memory_gib):
        return (
            f"DOES NOT FIT: the workload needs {need} and no size in the catalog is "
            f"that large. {spec.name} ({spec.vcpu} vCPU / {spec.memory_gib:g} GiB) is the "
            f"closest. Do not migrate this workload on this plan without resolving that first: "
            f"reduce or split it, choose another target, or pick a larger size by hand."
        )
    if is_undersized(spec, demand.target_vcpu, demand.target_memory_gib):
        return f"Fits the measured {need}, but without the usual headroom above it."
    return None


def _decision_reason(vm, demand, spec, tier, overridden: bool, suggested: str,
                     too_small: bool = False) -> str:
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
    if too_small:
        reason += f" Guardrail: model suggestion '{suggested}' is smaller than the workload needs."
    elif overridden:
        reason += f" Catalog guardrail: model suggestion '{suggested}' is not a real type."
    return reason


#: An EC2 instance id: the only kind the AWS target can adopt.
_EC2_ID = re.compile(r"^i-(?:[0-9a-f]{8}|[0-9a-f]{17})$")


def adoptable(target: Target, external_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(the id to adopt, or what to say when one was given and can't be)."""
    if not external_id:
        return None, None
    if CAP_BROWNFIELD_IMPORT in target.capabilities and _EC2_ID.match(external_id):
        return external_id, None
    return None, (
        f"Not adopted: '{external_id}' isn't a server {target.name} can take over, "
        "so a new one is planned."
    )


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
    # A change of OS *family* is categorically worse than a version bump. A
    # Windows workload landing on a Linux image will not run at all — no amount
    # of compatibility testing fixes it — and the only honest advice is to pick
    # a different target. DigitalOcean publishes no Windows image (ADR 0023),
    # so this is a real path, not a hypothetical one.
    if _os_family(source_os) != _os_family(image_key):
        return (
            f"OS FAMILY CHANGED: source reports '{source_os.strip()}' but the plan "
            f"provisions '{image_key}'. This target publishes no image for that "
            f"operating system, so the workload will not run as-is — it requires "
            f"either a different target cloud or a custom image. Do not migrate "
            f"this workload on this plan without resolving that first."
        )
    return (
        f"OS substituted: source reports '{source_os.strip()}' but the plan provisions "
        f"'{image_key}' — no matching image is available. Verify application "
        f"compatibility before migrating."
    )


def _os_family(text: str) -> str:
    """`windows` vs `linux` — the distinction that decides whether a workload
    boots at all, as opposed to merely needing a compatibility check."""
    return "windows" if "windows" in text.lower() else "linux"


def _root_and_extra(vm: NormalizedVM, image_min_gib: int) -> Tuple[int, List[int], Optional[str]]:
    """(boot disk, data disks, what to say if the boot disk isn't the source's)."""
    disks = [int(math.ceil(d)) for d in vm.disks_gib if d > 0]
    floor = max(_MIN_ROOT_GIB, image_min_gib)
    if not disks:
        return floor, [], f"No disk in the inventory: a {floor} GiB boot disk is assumed."
    if disks[0] < image_min_gib:
        return floor, disks[1:], (
            f"Boot disk raised from {disks[0]} to {floor} GiB: this image needs at least "
            f"{image_min_gib} GiB here."
        )
    if disks[0] < floor:
        return floor, disks[1:], (
            f"Boot disk raised from {disks[0]} to {floor} GiB, the smallest this tool plans."
        )
    return disks[0], disks[1:], None


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
        # Guardrail: never emit an instance type that isn't in the target catalog,
        # or one too small when the catalog has one that fits. Fall back to the
        # same demand model the provider should have used.
        suggested = target.spec_of(instance_type) if target.instance_exists(instance_type) else None
        too_small = suggested is not None and is_undersized(suggested, demand.vcpu, demand.memory_gib)
        overridden = suggested is None
        if suggested is None or too_small:
            instance_type = fit(target, demand, steady_cpu=tier in STEADY_CPU_TIERS).name
            too_small = too_small and instance_type != suggestion.instance_type
            overridden = overridden or too_small

        spec = target.spec_of(instance_type)
        reason = _decision_reason(vm, demand, spec, tier, overridden, suggestion.instance_type, too_small)
        undersized = bool(spec) and is_undersized(spec, demand.vcpu, demand.memory_gib)
        shortfall = _shortfall(demand, spec) if spec else None
        if shortfall:
            reason += f" {shortfall}"
        image_key = suggestion.image_key or target.image_key(vm.os)
        substitution = os_substitution_note(vm.os, image_key)
        if substitution:
            reason += f" {substitution}"
        family_changed = bool(vm.os) and _os_family(vm.os) != _os_family(image_key)
        external_id, adopt_note = adoptable(target, vm.external_id)
        root_gib, extra_gib, disk_note = _root_and_extra(vm, target.min_boot_gib(image_key))
        source_disks = [int(math.ceil(d)) for d in vm.disks_gib if d > 0]
        if external_id and source_disks:
            # An adopted server keeps its own boot disk (ADR 0091): plan and price
            # the one it has, and don't claim to raise it.
            root_gib, disk_note = max(8, source_disks[0]), None
        if disk_note:
            reason += f" {disk_note}"
        if adopt_note:
            reason += f" {adopt_note}"
        if external_id:
            reason += (
                f" Adopts the running server {external_id}: it stays in its own network with "
                "its own security groups, image and disks, so nothing replaces it. A "
                "different instance type is applied in place, which restarts it."
            )
            if extra_gib:
                reason += " Its data disks are already attached; none are created."
                extra_gib = []
        cost, price_source = monthly_cost(
            target.name, instance_type, region, target.cost_of(instance_type), live_pricing
        )

        plans.append(
            ComputePlan(
                vm_name=vm.vm_name,
                resource_name=vm.resource_name,
                source_os=vm.os,
                os_family_changed=family_changed,
                undersized=undersized,
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
                decision=sizing_decision(
                    vm.vm_name, instance_type,
                    source_vcpu=vm.cpu,
                    source_memory_gib=vm.memory_gib,
                    cpu_util_pct=vm.cpu_util_pct,
                    mem_util_pct=vm.mem_util_pct,
                    vcpu_headroom=demand.vcpu_headroom,
                    memory_headroom=demand.memory_headroom,
                    shortfall=shortfall,
                    target_vcpu=spec.vcpu if spec else None,
                    target_memory_gib=spec.memory_gib if spec else None,
                    # What the deterministic engine would have picked when the
                    # provider overrode it — the reviewer's "what else?".
                    alternatives=[suggestion.instance_type]
                    if overridden and suggestion.instance_type != instance_type
                    else [],
                ),
                external_id=external_id,
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
