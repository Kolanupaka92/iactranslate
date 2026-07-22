"""Infrastructure diff — drift between two inventory snapshots."""
from iactranslate.diff import diff_inventories
from iactranslate.models import NormalizedVM


def _vm(name, cpu=4, mem=16.0, disks=(80.0,), os="Ubuntu 22.04", power="poweredOn"):
    return NormalizedVM(
        vm_name=name, cpu=cpu, memory_gib=mem, disks_gib=list(disks),
        os=os, power_state=power,
    )


def test_added_and_removed():
    before = [_vm("a"), _vm("b")]
    after = [_vm("b"), _vm("c")]
    d = diff_inventories(before, after)
    assert d.added == ["c"]
    assert d.removed == ["a"]
    assert d.unchanged == 1
    assert d.has_changes


def test_modified_reports_field_deltas():
    before = [_vm("a", cpu=4, mem=16.0)]
    after = [_vm("a", cpu=8, mem=32.0)]
    d = diff_inventories(before, after)
    assert not d.added and not d.removed
    assert len(d.modified) == 1
    fields = {c.field for c in d.modified[0].changes}
    assert fields == {"cpu", "memory_gib"}
    cpu_change = next(c for c in d.modified[0].changes if c.field == "cpu")
    assert cpu_change.before == "4" and cpu_change.after == "8"


def test_totals_and_deltas():
    before = [_vm("a", cpu=4, mem=16.0, disks=(80.0,))]
    after = [_vm("a", cpu=8, mem=16.0, disks=(80.0, 100.0)), _vm("b", cpu=2, mem=8.0, disks=(40.0,))]
    d = diff_inventories(before, after)
    assert d.before.vcpu == 4 and d.after.vcpu == 10
    assert d.vcpu_delta == 6
    assert d.storage_delta == 140.0  # +100 extra disk on a, +40 new b


def test_identical_inventories_have_no_changes():
    vms = [_vm("a"), _vm("b")]
    d = diff_inventories(vms, list(vms))
    assert not d.has_changes
    assert d.unchanged == 2
    assert d.vcpu_delta == 0


def test_name_match_is_case_insensitive():
    d = diff_inventories([_vm("Prod-DB-01")], [_vm("prod-db-01", cpu=8)])
    assert not d.added and not d.removed
    assert len(d.modified) == 1


def test_diff_real_fixtures(cmdb_path, cmdb_util_path):
    from iactranslate.normalize import normalize
    from iactranslate.sources import resolve_source

    before = normalize(resolve_source(cmdb_path).parse(cmdb_path))
    after = normalize(resolve_source(cmdb_util_path).parse(cmdb_util_path))
    d = diff_inventories(before, after)
    # Both derive from the same estate; totals are well-formed regardless.
    assert d.before.workloads > 0 and d.after.workloads > 0
    assert isinstance(d.has_changes, bool)
