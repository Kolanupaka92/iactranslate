"""No renderer drops a data disk, and a newer image replaces no server (ADR 0090)."""
from __future__ import annotations

import json
import re

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.models import NormalizedVM
from iactranslate.renderers import list_renderers, render, supports
from iactranslate.targets import get_target, list_targets

#: Sizes nothing else in the output has.
SIZES = (737, 741)
COMBINATIONS = [(t, r) for t in list_targets() for r in list_renderers() if supports(r, t)]
PULUMI = ["aws", "azure", "gcp"]


def _vm(name, os="Ubuntu 22.04", disks=(60,)):
    return NormalizedVM(vm_name=name, cpu=2, memory_gib=8, disks_gib=list(disks), os=os)


def _files(target, renderer, *vms):
    t = get_target(target)
    return render(renderer, build_migration_plan(list(vms), "acme", t), t)


def _code(files):
    text = "\n".join(c for n, c in files.items() if not n.endswith(".md"))
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith(("#", "//")))


@pytest.mark.parametrize("os", ["Ubuntu 22.04", "Windows Server 2022"])
@pytest.mark.parametrize("target,renderer", COMBINATIONS)
def test_every_data_disk_is_in_the_code_once(target, renderer, os):
    code = _code(_files(target, renderer, _vm("db01", os, (60, *SIZES)), _vm("web01")))
    for size in SIZES:
        assert len(re.findall(rf"\b(?:{size}|{size * 1024})(?:Gi)?\b", code)) == 1, (target, renderer, size)


@pytest.mark.parametrize("target", PULUMI)
def test_pulumi_attaches_each_disk_to_its_own_machine(target):
    program = _files(target, "pulumi", _vm("db01", disks=(60, *SIZES)), _vm("web01", disks=(60, 50)))["__main__.py"]
    compile(program, "__main__.py", "exec")
    attach = {"aws": "aws.ec2.VolumeAttachment(", "azure": "azure.compute.DataDiskAttachment(",
              "gcp": "gcp.compute.AttachedDisk("}[target]
    blocks = [b.split("\n)")[0] for b in program.split(attach)[1:]]
    assert [re.search(r"inst_(\w+)", b).group(1) for b in blocks] == ["db01", "db01", "web01"]
    assert len({re.search(r'"(\w+)"', b).group(1) for b in blocks}) == 3


@pytest.mark.parametrize("target", PULUMI)
def test_pulumi_without_data_disks_has_no_disk_section(target):
    assert "# --- Data" not in _files(target, "pulumi", _vm("web01"))["__main__.py"]


def test_pulumi_load_balancers_use_arguments_the_libraries_take():
    vms = [_vm(n) for n in ("prod-web-01", "prod-web-02", "prod-app-01", "prod-app-02")]
    gcp = _files("gcp", "pulumi", *vms)["__main__.py"]
    for kind in ("TargetPool", "RegionBackendService"):  # one health check, not a list
        assert re.search(r"health_checks=hc_\w+\.self_link,", gcp.split(f"gcp.compute.{kind}(")[1].split("\n)")[0])
    azure = _files("azure", "pulumi", *vms)["__main__.py"]
    for kind in ("BackendAddressPool", "Probe", "Rule"):  # they belong to the load balancer
        assert "resource_group_name" not in azure.split(f"azure.lb.{kind}(")[1].split("\n)")[0]


def test_pulumi_ignores_newer_images():
    aws = _files("aws", "pulumi", _vm("a"), _vm("b"))["__main__.py"]
    assert aws.count('ignore_changes=["ami"]') == aws.count("aws.ec2.Instance(") == 2
    gcp = _files("gcp", "pulumi", _vm("a"), _vm("b"))["__main__.py"]
    assert gcp.count('"bootDisk.initializeParams.image"') == gcp.count("gcp.compute.Instance(") == 2


def test_pulumi_still_adopts_an_existing_instance():
    vm = _vm("a").model_copy(update={"external_id": "i-0123456789abcdef0"})
    program = _files("aws", "pulumi", vm)["__main__.py"]
    compile(program, "__main__.py", "exec")
    assert '"subnetId"' in program and 'import_="i-0123456789abcdef0"' in program.replace("'", '"')


def test_bicep_disks_have_their_own_luns_and_survive_the_machine():
    code = _files("azure", "bicep", _vm("db01", disks=(60, *SIZES)), _vm("web01"))["resources.bicep"]
    disks = re.findall(r"\{ name: '([\w-]+)', lun: (\d+), createOption: 'Empty', diskSizeGB: (\d+).*?"
                       r"deleteOption: 'Detach'", code)
    assert disks == [("db01-data-0", "0", "737"), ("db01-data-1", "1", "741")]
    assert code.count("dataDisks: [") == 1


def test_cloudformation_and_cdk_keep_a_replaced_server():
    files = _files("aws", "cloudformation", _vm("a"), _vm("b"))
    template = json.loads(next(c for n, c in files.items() if n.endswith(".json")))
    servers = [r for r in template["Resources"].values() if r["Type"] == "AWS::EC2::Instance"]
    assert len(servers) == 2 and all(r["UpdateReplacePolicy"] == "Retain" for r in servers)
    cdk = _code(_files("aws", "cdk", _vm("a"), _vm("b")))
    assert cdk.count("update_replace_policy = CfnDeletionPolicy.RETAIN") == cdk.count("ec2.CfnInstance(") == 2


@pytest.mark.parametrize("layout", ["terraform", "terraform-foreach"])
@pytest.mark.parametrize("target,ignored", [
    ("aws", "ami"), ("oci", "source_details[0].source_id"),
    ("gcp", "boot_disk[0].initialize_params[0].image")])
def test_terraform_ignores_newer_images(target, ignored, layout):
    compute = _files(target, layout, _vm("a"), _vm("b", disks=(60, 10)))["compute.tf"]
    servers = len(re.findall(r'^resource "(?:aws_instance|oci_core_instance|google_compute_instance)"', compute, re.M))
    assert len([r for r in re.findall(r"ignore_changes = \[(.*)\]$", compute, re.M) if ignored in r]) == servers


def test_oracle_asks_for_images_that_fit_the_shape():
    vms = [_vm("a"), _vm("b", "Windows Server 2022"),
           NormalizedVM(vm_name="c", cpu=8, memory_gib=64, disks_gib=[60], os="Ubuntu 22.04")]
    files = _files("oci", "terraform", *vms)
    used = set(re.findall(r'shape\s+= "(VM\.[\w.]+)"', files["compute.tf"]))
    lookups = re.findall(r'data "oci_core_images" "\w+" \{(.*?)^\}', files["images.tf"], re.S | re.M)
    assert len(lookups) == 2
    assert all(re.search(r'^  shape\s+= "([\w.]+)"$', b, re.M).group(1) in used for b in lookups)
    assert all(re.search(r'^  state\s+= "AVAILABLE"$', b, re.M) for b in lookups)
