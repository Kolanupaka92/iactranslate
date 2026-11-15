"""Infrastructure diff — what changed between two inventory snapshots.

Re-run discovery a month later and compare: which workloads were added, removed,
renamed or resized, and how the aggregate footprint moved. Matches workloads by
VM UUID where both snapshots carry one, by name otherwise, and reports
field-level deltas. Deterministic; no AI.

Use it to keep generated Terraform honest against a drifting estate, or to size
a migration wave-by-wave.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

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


class Rename(BaseModel):
    before: str
    after: str


class Totals(BaseModel):
    workloads: int
    vcpu: int
    memory_gib: float
    storage_gib: float


class InventoryDiff(BaseModel):
    added: List[str] = Field(default_factory=list)
    removed: List[str] = Field(default_factory=list)
    #: Same machine (matched by VM UUID), new name. Reported instead of a
    #: removal plus an addition, which is what a rename looked like before and
    #: what it would look like to anyone planning a migration wave from it.
    renamed: List[Rename] = Field(default_factory=list)
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
        return bool(self.added or self.removed or self.renamed or self.modified)


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


def _uuid(vm: NormalizedVM) -> Optional[str]:
    return vm.source_uuid.strip().lower() if vm.source_uuid else None


def _pairs(before: List[NormalizedVM], after: List[NormalizedVM]):
    """Match workloads across snapshots: by VM UUID where both sides have one,
    then by name for whatever is left. Returns (pairs, unmatched_before,
    unmatched_after)."""
    a_by_uuid = {_uuid(v): v for v in after if _uuid(v)}
    pairs: List[Tuple[NormalizedVM, NormalizedVM]] = []
    b_rest: List[NormalizedVM] = []
    matched_after = set()
    for bvm in before:
        avm = a_by_uuid.get(_uuid(bvm)) if _uuid(bvm) else None
        if avm is not None:
            pairs.append((bvm, avm))
            matched_after.add(id(avm))
        else:
            b_rest.append(bvm)
    a_rest = [v for v in after if id(v) not in matched_after]

    b_by = {_key(v): v for v in b_rest}
    a_by = {_key(v): v for v in a_rest}
    for k in b_by.keys() & a_by.keys():
        pairs.append((b_by[k], a_by[k]))
    return (
        pairs,
        [b_by[k] for k in b_by.keys() - a_by.keys()],
        [a_by[k] for k in a_by.keys() - b_by.keys()],
    )


def diff_inventories(before: List[NormalizedVM], after: List[NormalizedVM]) -> InventoryDiff:
    """Compare two normalized inventories, matching by VM UUID, then by name."""
    pairs, gone, new = _pairs(before, after)

    added = sorted(v.vm_name for v in new)
    removed = sorted(v.vm_name for v in gone)

    renamed: List[Rename] = []
    modified: List[WorkloadChange] = []
    unchanged = 0
    for bvm, avm in sorted(pairs, key=lambda p: _key(p[1])):
        if _key(bvm) != _key(avm):
            renamed.append(Rename(before=bvm.vm_name, after=avm.vm_name))
        changes = [
            FieldChange(field=f, before=_field_value(bvm, f), after=_field_value(avm, f))
            for f in _COMPARED
            if _field_value(bvm, f) != _field_value(avm, f)
        ]
        if changes:
            modified.append(WorkloadChange(vm_name=avm.vm_name, changes=changes))
        elif _key(bvm) == _key(avm):
            unchanged += 1

    return InventoryDiff(
        added=added, removed=removed, renamed=renamed, modified=modified,
        unchanged=unchanged, before=_totals(before), after=_totals(after),
    )
