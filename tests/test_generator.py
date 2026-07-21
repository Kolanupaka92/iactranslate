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


def test_ami_map_has_placeholder_per_os(rvtools_path):
    files, plan = _files(rvtools_path)
    variables = files["variables.tf"]
    for key in {c.image_key for c in plan.compute}:
        assert f'"{key}" = "ami-REPLACE_ME"' in variables
