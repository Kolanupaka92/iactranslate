"""A machine is its VM UUID, not its name.

Names are labels an administrator can change, and vCenter happily allows the
same name in two clusters. Keying identity on the name merged distinct machines
(one silently vanished from the migration) and made a rename look like a
decommission plus a new build.
"""
import pandas as pd

from iactranslate.agents import build_migration_plan
from iactranslate.agents.deidentify import Pseudonymizer
from iactranslate.diff import diff_inventories
from iactranslate.models import NormalizedVM
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import get_target

U1 = "421a0001-aaaa-bbbb-cccc-000000000017"
U2 = "421a0002-aaaa-bbbb-cccc-000000000042"


def _rec(name, uuid=None, disks=(20480,), **extra):
    rec = {"name": name, "cpus": 2, "memory_mib": 4096, "disks_mib": list(disks)}
    if uuid:
        rec["uuid"] = uuid
    rec.update(extra)
    return rec


# --- normalize ---------------------------------------------------------------


def test_two_machines_sharing_a_name_are_both_kept():
    vms = normalize([_rec("app01", U1, cluster="east"), _rec("app01", U2, cluster="west")])
    assert len(vms) == 2
    assert sorted(v.vm_name for v in vms) == ["app01-00000017", "app01-00000042"]
    assert {v.cluster for v in vms} == {"east", "west"}


def test_the_suffix_comes_from_the_uuid_so_file_order_does_not_matter():
    forward = normalize([_rec("app01", U1), _rec("app01", U2)])
    reverse = normalize([_rec("app01", U2), _rec("app01", U1)])
    assert {v.source_uuid: v.vm_name for v in forward} == {v.source_uuid: v.vm_name for v in reverse}


def test_repeated_rows_for_one_uuid_are_one_machine():
    vms = normalize([_rec("app01", U1, disks=(10240,)), _rec("app01", U1, disks=(51200,))])
    assert len(vms) == 1
    assert vms[0].vm_name == "app01"  # no collision left, so no suffix
    assert vms[0].total_disk_gib == 50  # the record with more disk detail wins


def test_without_uuids_names_still_de_duplicate():
    vms = normalize([_rec("app01"), _rec("app01", disks=(40960,))])
    assert len(vms) == 1 and vms[0].total_disk_gib == 40


def test_a_suffix_never_lands_on_a_real_machine_name():
    vms = normalize([_rec("app01", U1), _rec("app01", U2), _rec("app01-00000017")])
    names = [v.vm_name for v in vms]
    assert len(set(names)) == 3
    assert "app01-00000017" in names  # the real one keeps its name


def test_a_machine_without_a_uuid_keeps_the_bare_name():
    vms = normalize([_rec("app01"), _rec("app01", U2)])
    assert sorted(v.vm_name for v in vms) == ["app01", "app01-00000042"]


def test_a_short_or_odd_uuid_still_yields_unique_names():
    vms = normalize([_rec("db", "7"), _rec("db", "8")])
    assert sorted(v.vm_name for v in vms) == ["db-7", "db-8"]


def test_same_named_machines_get_distinct_terraform_resources():
    vms = normalize([_rec("app01", U1), _rec("app01", U2)])
    plan = build_migration_plan(vms, "uuid", get_target("aws"))
    assert len(plan.compute) == 2
    assert len({c.resource_name for c in plan.compute}) == 2


# --- RVTools parsing ---------------------------------------------------------


def _workbook(path, with_uuid=True):
    uuid = {"VM UUID": [U1, U2]} if with_uuid else {}
    vinfo = pd.DataFrame({"VM": ["app01", "app01"], "CPUs": [2, 4], "Memory": [4096, 8192],
                          "Cluster": ["east", "west"], **uuid})
    vdisk = pd.DataFrame({"VM": ["app01", "app01"], "Capacity MiB": [10240, 102400],
                          **({"VM UUID": [U1, U2]} if with_uuid else {})})
    vnet = pd.DataFrame({"VM": ["app01", "app01"], "Network": ["VLAN_10", "VLAN_20"],
                         **({"VM UUID": [U1, U2]} if with_uuid else {})})
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        vinfo.to_excel(xw, sheet_name="vInfo", index=False)
        vdisk.to_excel(xw, sheet_name="vDisk", index=False)
        vnet.to_excel(xw, sheet_name="vNetwork", index=False)
    return str(path)


def test_rvtools_joins_disks_and_nics_by_uuid(tmp_path):
    vms = {v.source_uuid: v for v in normalize(parse(_workbook(tmp_path / "rv.xlsx")))}
    assert vms[U1].total_disk_gib == 10 and vms[U1].network == "VLAN_10" and vms[U1].cpu == 2
    assert vms[U2].total_disk_gib == 100 and vms[U2].network == "VLAN_20" and vms[U2].cpu == 4


def test_rvtools_without_uuids_behaves_as_before(tmp_path):
    vms = normalize(parse(_workbook(tmp_path / "rv.xlsx", with_uuid=False)))
    assert len(vms) == 1 and vms[0].source_uuid is None


def test_the_realistic_fixture_carries_a_uuid_for_every_machine():
    vms = normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))
    assert vms and all(v.source_uuid for v in vms)


# --- de-identification ---------------------------------------------------------


def test_the_uuid_never_reaches_the_model():
    vm = NormalizedVM(vm_name="app01", cpu=2, memory_gib=4, source_uuid=U1)
    assert Pseudonymizer().deidentify(vm).source_uuid is None


# --- diff ----------------------------------------------------------------------


def _vm(name, uuid=None, cpu=2):
    return NormalizedVM(vm_name=name, cpu=cpu, memory_gib=4, disks_gib=[20], source_uuid=uuid)


def test_a_rename_is_reported_as_a_rename():
    d = diff_inventories([_vm("old-name", U1)], [_vm("new-name", U1)])
    assert [(r.before, r.after) for r in d.renamed] == [("old-name", "new-name")]
    assert not d.added and not d.removed and not d.modified
    assert d.unchanged == 0 and d.has_changes


def test_a_rename_with_a_resize_is_both():
    d = diff_inventories([_vm("old-name", U1, cpu=2)], [_vm("new-name", U1, cpu=8)])
    assert len(d.renamed) == 1
    assert d.modified[0].vm_name == "new-name"
    assert d.modified[0].changes[0].field == "cpu"


def test_uuid_matching_wins_over_a_reused_name():
    # The name moved to a different machine; the old machine was renamed.
    before = [_vm("app01", U1)]
    after = [_vm("app01-legacy", U1), _vm("app01", U2)]
    d = diff_inventories(before, after)
    assert [(r.before, r.after) for r in d.renamed] == [("app01", "app01-legacy")]
    assert d.added == ["app01"]


def test_without_uuids_a_rename_still_looks_like_add_and_remove():
    d = diff_inventories([_vm("old-name")], [_vm("new-name")])
    assert d.added == ["new-name"] and d.removed == ["old-name"] and not d.renamed
