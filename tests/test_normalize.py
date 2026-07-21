from iactranslate.normalize import normalize
from iactranslate.parsers import parse


def test_unit_coercion_and_dedup(rvtools_path):
    vms = normalize(parse(rvtools_path))
    assert len(vms) == 7
    by_name = {v.vm_name: v for v in vms}
    web = by_name["prod-web-01"]
    assert web.cpu == 4
    assert web.memory_gib == 16.0            # 16384 MiB -> 16 GiB
    assert web.disks_gib == [200.0]
    assert web.ip_addresses == ["10.10.20.11"]
    # Multi-disk VM keeps both disks in GiB.
    assert by_name["prod-app-01"].disks_gib == [100.0, 200.0]


def test_csv_and_xlsx_agree_on_specs(rvtools_path, vmware_csv_path):
    x = {v.vm_name: v for v in normalize(parse(rvtools_path))}
    c = {v.vm_name: v for v in normalize(parse(vmware_csv_path))}
    for name in x:
        assert x[name].cpu == c[name].cpu
        assert x[name].memory_gib == c[name].memory_gib


def test_resource_name_is_terraform_safe(rvtools_path):
    vms = normalize(parse(rvtools_path))
    for vm in vms:
        assert vm.resource_name.replace("_", "").isalnum()
        assert vm.resource_name[0].isalpha() or vm.resource_name[0] == "_"
