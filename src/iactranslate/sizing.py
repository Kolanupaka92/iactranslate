"""Right-sizing demand model — allocation vs utilization.

When the inventory carries *observed utilization*, size the target instance to
what the workload uses, with headroom. Otherwise size to what it was allocated,
as it is: an allocation already contains whatever margin its owner chose, and
adding more pushed most machines up a size, which doubles the price (ADR 0087).

Each dimension stands alone. CPU usage says nothing about memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .config import TARGET_UTILIZATION
from .models import NormalizedVM
from .targets.base import InstanceSpec, Target, is_undersized

# Floors so a barely-used VM still lands on a real instance.
_MIN_VCPU = 1.0
_MIN_MEM_GIB = 1.0


@dataclass(frozen=True)
class Demand:
    """What a workload needs, and how much room to leave above that."""

    vcpu: float
    memory_gib: float
    #: Multipliers: 1 / TARGET_UTILIZATION where usage was measured, else 1.
    vcpu_headroom: float
    memory_headroom: float
    right_sized: bool

    @property
    def target_vcpu(self) -> float:
        return self.vcpu * self.vcpu_headroom

    @property
    def target_memory_gib(self) -> float:
        return self.memory_gib * self.memory_headroom


def _dimension(allocated: float, util_pct: Optional[float], floor: float) -> Tuple[float, float]:
    if util_pct is None:
        return float(allocated), 1.0
    return max(floor, allocated * util_pct / 100.0), 1.0 / TARGET_UTILIZATION


def effective_demand(vm: NormalizedVM) -> Demand:
    """Compute the sizing demand for one VM, one dimension at a time.

    Measured   -> allocation x utilization, plus headroom, so that the new
                  instance runs near TARGET_UTILIZATION.
    Unmeasured -> the allocation, unchanged.
    """
    vcpu, vcpu_headroom = _dimension(vm.cpu, vm.cpu_util_pct, _MIN_VCPU)
    memory, memory_headroom = _dimension(vm.memory_gib, vm.mem_util_pct, _MIN_MEM_GIB)
    return Demand(vcpu, memory, vcpu_headroom, memory_headroom, right_sized=vm.has_utilization)


def fit(target: Target, demand: Demand, steady_cpu: bool = False) -> InstanceSpec:
    """Cheapest size with room for the headroom; without it when none has."""
    spec = target.smallest_fit(demand.target_vcpu, demand.target_memory_gib, steady_cpu)
    if is_undersized(spec, demand.target_vcpu, demand.target_memory_gib):
        spec = target.smallest_fit(demand.vcpu, demand.memory_gib, steady_cpu)
    return spec
