from iactranslate.agents import build_migration_plan
from iactranslate.generator import build_files
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import get_target

AWS = get_target("aws")

EXPECTED_FILES = {
    "versions.tf", "provider.tf", "variables.tf", "terraform.tfvars",
    "networking.tf", "security.tf", "compute.tf", "storage.tf",
    "outputs.tf", "main.tf", "README.md",
}


def _files(rvtools_path):
    vms = normalize(parse(rvtools_path))
    plan = build_migration_plan(vms, project_name="gen-test", target=AWS)
    return build_files(plan, AWS), plan


def test_all_expected_files_present(rvtools_path):
    files, _ = _files(rvtools_path)
    assert EXPECTED_FILES.issubset(files.keys())


def test_compute_has_one_instance_per_vm(rvtools_path):
    files, plan = _files(rvtools_path)
    compute = files["compute.tf"]
    for c in plan.compute:
        assert f'resource "aws_instance" "{c.resource_name}"' in compute
        assert f'instance_type          = "{c.instance_type}"' in compute
    assert compute.count('resource "aws_instance"') == plan.vm_count


def test_networking_defines_vpc_and_subnets(rvtools_path):
    files, plan = _files(rvtools_path)
    net = files["networking.tf"]
    assert 'resource "aws_vpc" "main"' in net
    assert f'cidr_block           = "{plan.network.vpc_cidr}"' in net
    for subnet in plan.network.subnets:
        assert f'resource "aws_subnet" "{subnet.resource_name}"' in net


def test_security_groups_rendered(rvtools_path):
    files, plan = _files(rvtools_path)
    sec = files["security.tf"]
    for sg in plan.network.security_groups:
        assert f'resource "aws_security_group" "{sg.resource_name}"' in sec


def test_amis_resolve_via_data_sources(rvtools_path):
    """AWS output resolves AMIs with data sources — no manual placeholders."""
    files, plan = _files(rvtools_path)
    images = files["images.tf"]
    compute = files["compute.tf"]
    # A data source per detected OS, referenced by each instance.
    from iactranslate.generator.renderer import terraform_safe_name

    for key in {c.image_key for c in plan.compute}:
        assert f'data "aws_ami" "{terraform_safe_name(key)}"' in images
    assert "data.aws_ami." in compute
    # Zero-edit: no leftover placeholder tokens anywhere in the project.
    for name, content in files.items():
        assert "REPLACE_ME" not in content, f"placeholder left in {name}"
