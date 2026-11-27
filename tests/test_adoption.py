"""Adopting a running server never moves or replaces it (ADR 0091)."""
from __future__ import annotations

import re

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.models import NormalizedVM
from iactranslate.renderers import render
from iactranslate.targets import get_target, list_targets

ID = "i-0123456789abcdef0"
KEEPS = ("ignore_changes = [ami, subnet_id, vpc_security_group_ids, root_block_device, "
         "associate_public_ip_address, iam_instance_profile, tags]")


def _vm(name, external_id=None, disks=(40,)):
    return NormalizedVM(vm_name=name, cpu=2, memory_gib=8, disks_gib=list(disks), os="Ubuntu 22.04",
                        external_id=external_id)


def _plan(*vms, target="aws"):
    return build_migration_plan(list(vms), "acme", get_target(target))


@pytest.mark.parametrize("external_id,adopted", [
    (ID, True), ("i-0123abcd", True), ("i-0abc", False), ("I-0123456789ABCDEF0", False),
    ("/subscriptions/x/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1", False),
    ("vol-0123456789abcdef0", False), ("i-0123456789abcdef0 ", False), ("", False)])
def test_only_an_ec2_instance_id_is_adopted(external_id, adopted):
    c = _plan(_vm("prod-app-01", external_id)).compute[0]
    assert (c.external_id == external_id) is adopted
    assert ("Adopts the running server" in c.reason) is adopted
    assert ("Not adopted" in c.reason) is (bool(external_id) and not adopted)


@pytest.mark.parametrize("target", [t for t in list_targets() if t != "aws"])
def test_other_targets_plan_a_new_server_and_say_so(target):
    c = _plan(_vm("prod-app-01", ID), target=target).compute[0]
    assert c.external_id is None and "Not adopted" in c.reason


def test_an_adopted_server_is_behind_no_new_load_balancer():
    plan = _plan(_vm("prod-app-01", ID), _vm("prod-app-02"), _vm("prod-app-03"))
    (lb,) = plan.network.load_balancers
    assert lb.targets == ["prod-app-02", "prod-app-03"]
    plan = _plan(_vm("prod-app-01", ID), _vm("prod-app-02"))
    assert plan.network.load_balancers == []


def test_an_adopted_servers_disks_are_not_created_again():
    c = _plan(_vm("prod-app-01", ID, disks=(40, 100, 200))).compute[0]
    assert c.extra_volumes_gib == [] and "none are created" in c.reason


@pytest.mark.parametrize("layout", ["terraform", "terraform-foreach"])
def test_only_adopted_servers_keep_their_place(layout):
    files = render(layout, _plan(_vm("prod-app-01", ID), _vm("prod-app-02", disks=(40, 10))), get_target("aws"))
    compute = files["compute.tf"]
    assert compute.count(KEEPS) == 1 and compute.count("ignore_changes = [ami]") == 1
    address = 'aws_instance.adopted["prod_app_01"]' if layout == "terraform-foreach" else "aws_instance.prod_app_01"
    assert f"to = {address}\n  id = \"{ID}\"" in files["imports.tf"]
    assert "never \"replace\"" in files["imports.tf"]
    if layout == "terraform-foreach":
        keeps = compute.split('resource "aws_instance" "adopted"')[1]
        assert KEEPS in keeps and '"prod_app_01"' in compute.split("adopted = {")[1].split("\n  }")[0]
        assert "merge(aws_instance.vm, aws_instance.adopted)" in compute
        assert "local.instances[each.value.vm]" in files["storage.tf"]


def test_without_adoption_the_for_each_layout_is_as_before():
    compute = render("terraform-foreach", _plan(_vm("prod-app-01")), get_target("aws"))["compute.tf"]
    assert 'resource "aws_instance" "adopted"' not in compute
    assert "instances = aws_instance.vm\n" in compute


def test_pulumi_adopts_without_changing_anything():
    program = render("pulumi", _plan(_vm("prod-app-01", ID), _vm("prod-app-02")), get_target("aws"))["__main__.py"]
    compile(program, "__main__.py", "exec")
    adopted, new = (program.split(f"inst_{n} = aws.ec2.Instance(")[1].split("\n)")[0]
                    for n in ("prod_app_01", "prod_app_02"))
    assert f'import_="{ID}"' in adopted and '"subnetId"' in adopted and '"instanceType"' in adopted
    assert "import_" not in new and re.search(r'ignore_changes=\["ami"\]\)', new)


def test_switching_layouts_moves_an_adopted_server_to_its_own_map():
    files = render("terraform-foreach", _plan(_vm("prod-app-01", ID), _vm("prod-app-02")), get_target("aws"))
    moves = dict(re.findall(r"from = (\S+)\n  to   = (\S+)", files["migrate-from-classic.tf"]))
    assert moves["aws_instance.prod_app_01"] == 'aws_instance.adopted["prod_app_01"]'
    assert moves["aws_instance.prod_app_02"] == 'aws_instance.vm["prod_app_02"]'
    declared = set(re.findall(r'^resource "aws_instance" "(\w+)"', files["compute.tf"], re.M))
    assert {m.split("[")[0].split(".")[1] for m in moves.values() if m.startswith("aws_instance")} <= declared


def test_an_adopted_server_keeps_its_own_boot_disk_size_in_the_plan():
    c = _plan(_vm("prod-app-01", ID, disks=(8,))).compute[0]
    assert c.root_volume_gib == 8 and "Boot disk raised" not in c.reason
    new = _plan(_vm("prod-app-01", disks=(8,))).compute[0]
    assert new.root_volume_gib == 30 and "raised from 8 to 30 GiB" in new.reason


def test_the_reason_names_what_set_the_minimum():
    from iactranslate.models import NormalizedVM
    win = NormalizedVM(vm_name="w", cpu=2, memory_gib=8, disks_gib=[20], os="Windows Server 2022")
    c = build_migration_plan([win], "x", get_target("gcp")).compute[0]
    assert "this image needs at least 50 GiB" in c.reason
    lin = _plan(_vm("prod-app-01", disks=(20,))).compute[0]
    assert "the smallest this tool plans" in lin.reason
