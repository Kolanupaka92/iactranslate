"""Brownfield support — adopt an existing cloud fleet via Terraform import blocks."""
from iactranslate.agents import build_migration_plan
from iactranslate.generator import build_files
from iactranslate.normalize import normalize
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


def _cloud_csv(tmp_path, with_ids=True):
    p = tmp_path / "fleet.csv"
    header = "InstanceId,Name,InstanceType,Platform,VolumeSize" if with_ids else "Name,InstanceType,Platform,VolumeSize"
    rows = (
        ["i-0abc,web-1,m5.large,Ubuntu 22.04,80", "i-0def,db-1,r5.xlarge,Windows Server 2022,200"]
        if with_ids else
        ["web-1,m5.large,Ubuntu 22.04,80", "db-1,r5.xlarge,Windows Server 2022,200"]
    )
    p.write_text("\n".join([header, *rows]) + "\n")
    return str(p)


def test_cloud_source_captures_external_id(tmp_path):
    vms = normalize(resolve_source(_cloud_csv(tmp_path)).parse(_cloud_csv(tmp_path)))
    by = {v.vm_name: v for v in vms}
    assert by["web-1"].external_id == "i-0abc"
    assert by["db-1"].external_id == "i-0def"


def test_imports_tf_generated_for_brownfield(tmp_path):
    vms = normalize(resolve_source(_cloud_csv(tmp_path)).parse(_cloud_csv(tmp_path)))
    plan = build_migration_plan(vms, "bf", get_target("aws"))
    files = build_files(plan, get_target("aws"))
    assert "imports.tf" in files
    imports = files["imports.tf"]
    assert "import {" in imports
    assert 'id = "i-0abc"' in imports
    assert "aws_instance." in imports
    # One import block per adopted workload.
    assert imports.count("import {") == 2


def test_no_imports_tf_without_ids(tmp_path):
    vms = normalize(resolve_source(_cloud_csv(tmp_path, with_ids=False)).parse(
        _cloud_csv(tmp_path, with_ids=False)))
    assert all(v.external_id is None for v in vms)
    plan = build_migration_plan(vms, "green", get_target("aws"))
    files = build_files(plan, get_target("aws"))
    # No brownfield ids → the empty imports.tf is dropped entirely.
    assert "imports.tf" not in files


def test_external_id_flows_to_compute_plan(tmp_path):
    vms = normalize(resolve_source(_cloud_csv(tmp_path)).parse(_cloud_csv(tmp_path)))
    plan = build_migration_plan(vms, "bf", get_target("aws"))
    ids = {c.vm_name: c.external_id for c in plan.compute}
    assert ids["web-1"] == "i-0abc"
