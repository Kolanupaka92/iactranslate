"""Infrastructure diff — what changed between two inventory snapshots.

Re-run discovery a month later and compare: which workloads were added, removed,
or resized, and how the aggregate footprint moved. Matches workloads by name and
reports field-level deltas. Deterministic; no AI.

Use it to keep generated Terraform honest against a drifting estate, or to size
a migration wave-by-wave.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .models import NormalizedVM

# Fields compared for "modified"; each maps to a human label.
_COMPARED = ("cpu", "memory_gib", "storage_gib", "os", "power_state")


class FieldChange(BaseModel):
    field: str
    before: Optional[str] = None
    after: Optional[str] = None


class WorkloadChange(BaseModel):
    vm_name: str
    changes: List[FieldChange]


class Totals(BaseModel):
    workloads: int
    vcpu: int
    memory_gib: float
    storage_gib: float


class InventoryDiff(BaseModel):
    added: List[str] = Field(default_factory=list)
    removed: List[str] = Field(default_factory=list)
    modified: List[WorkloadChange] = Field(default_factory=list)
    unchanged: int = 0
    before: Totals
    after: Totals

    @property
    def vcpu_delta(self) -> int:
        return self.after.vcpu - self.before.vcpu

    @property
    def memory_delta(self) -> float:
        return round(self.after.memory_gib - self.before.memory_gib, 2)

    @property
    def storage_delta(self) -> float:
        return round(self.after.storage_gib - self.before.storage_gib, 2)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.modified)


def _storage(vm: NormalizedVM) -> float:
    return round(float(sum(vm.disks_gib)), 2)


def _field_value(vm: NormalizedVM, field: str) -> Optional[str]:
    if field == "storage_gib":
        return f"{_storage(vm):g}"
    if field == "cpu":
        return str(vm.cpu)
    if field == "memory_gib":
        return f"{vm.memory_gib:g}"
    val = getattr(vm, field, None)
    return None if val in (None, "") else str(val)


def _totals(vms: List[NormalizedVM]) -> Totals:
    return Totals(
        workloads=len(vms),
        vcpu=sum(v.cpu for v in vms),
        memory_gib=round(sum(v.memory_gib for v in vms), 2),
        storage_gib=round(sum(_storage(v) for v in vms), 2),
    )


def _key(vm: NormalizedVM) -> str:
    return vm.vm_name.strip().lower()


def diff_inventories(before: List[NormalizedVM], after: List[NormalizedVM]) -> InventoryDiff:
    """Compare two normalized inventories, matching workloads by name."""
    b_by: Dict[str, NormalizedVM] = {_key(v): v for v in before}
    a_by: Dict[str, NormalizedVM] = {_key(v): v for v in after}

    added = sorted(a_by[k].vm_name for k in a_by.keys() - b_by.keys())
    removed = sorted(b_by[k].vm_name for k in b_by.keys() - a_by.keys())

    modified: List[WorkloadChange] = []
    unchanged = 0
    for k in sorted(b_by.keys() & a_by.keys()):
        bvm, avm = b_by[k], a_by[k]
        changes = [
            FieldChange(field=f, before=_field_value(bvm, f), after=_field_value(avm, f))
            for f in _COMPARED
            if _field_value(bvm, f) != _field_value(avm, f)
        ]
        if changes:
            modified.append(WorkloadChange(vm_name=avm.vm_name, changes=changes))
        else:
            unchanged += 1

    return InventoryDiff(
        added=added, removed=removed, modified=modified, unchanged=unchanged,
        before=_totals(before), after=_totals(after),
    )
