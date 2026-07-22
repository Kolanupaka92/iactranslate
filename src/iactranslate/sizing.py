"""Right-sizing demand model — allocation vs utilization.

The core improvement over naive migration: when the source inventory carries
*observed utilization*, size the target instance to what the workload actually
uses (with headroom), not to how much it was over-provisioned with. When no
utilization is present, fall back to the original allocation-based sizing so
behavior is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import TARGET_UTILIZATION
from .models import NormalizedVM

# Headroom applied to raw allocation when no utilization data exists (legacy
# behavior: leave ~20% margin above the configured allocation).
_ALLOCATION_HEADROOM = 1.2
# Floors so a barely-used VM still lands on a real instance.
_MIN_VCPU = 1.0
_MIN_MEM_GIB = 1.0


@dataclass(frozen=True)
class Demand:
    """What to feed `smallest_fit`: the requirement and the headroom multiplier."""

    vcpu: float
    memory_gib: float
    headroom: float
    right_sized: bool


def effective_demand(vm: NormalizedVM) -> Demand:
    """Compute the sizing demand for one VM.

    Utilization present -> demand = allocation x utilization, headroom = 1/target
    (size the new instance so it runs near TARGET_UTILIZATION).
    Utilization absent  -> demand = allocation, headroom = 1.2 (unchanged).
    """
    if vm.has_utilization:
        cpu_frac = (vm.cpu_util_pct if vm.cpu_util_pct is not None else 100.0) / 100.0
        mem_frac = (vm.mem_util_pct if vm.mem_util_pct is not None else 100.0) / 100.0
        return Demand(
            vcpu=max(_MIN_VCPU, vm.cpu * cpu_frac),
            memory_gib=max(_MIN_MEM_GIB, vm.memory_gib * mem_frac),
            headroom=1.0 / TARGET_UTILIZATION,
            right_sized=True,
        )
    return Demand(
        vcpu=float(vm.cpu),
        memory_gib=vm.memory_gib,
        headroom=_ALLOCATION_HEADROOM,
        right_sized=False,
    )
