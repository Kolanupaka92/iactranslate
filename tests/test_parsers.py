from iactranslate.parsers import detect_format, parse


def test_detect_format(rvtools_path, vmware_csv_path):
    assert detect_format(rvtools_path) == "rvtools"
    assert detect_format(vmware_csv_path) == "vmware_csv"


def test_rvtools_parse_row_count(rvtools_path):
    records = parse(rvtools_path)
    assert len(records) == 7
    by_name = {r["name"]: r for r in records}
    assert "prod-web-01" in by_name
    # RVTools memory is reported in MiB (16 GiB -> 16384).
    assert by_name["prod-web-01"]["memory_mib"] == 16 * 1024
    # Multi-disk VM split into two disks in vDisk.
    assert len(by_name["prod-app-01"]["disks_mib"]) == 2


def test_vmware_csv_parse(vmware_csv_path):
    records = parse(vmware_csv_path)
    assert len(records) == 7
    by_name = {r["name"]: r for r in records}
    rec = by_name["prod-web-01"]
    assert rec["memory_value"] == 16
    assert rec["memory_unit"] == "gib"
    assert rec["network"] == "VLAN20"
