"""Disks and probes that a plan accepts and a cloud refuses (ADR 0089)."""
from __future__ import annotations

import re

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.models import NormalizedVM
from iactranslate.normalize import normalize
from iactranslate.renderers import render
from iactranslate.sources.vmware import vmware_csv
from iactranslate.targets import get_target, list_targets

LAYOUTS = ["terraform", "terraform-foreach"]
WINDOWS, RHEL, UBUNTU = "Windows Server 2022", "Red Hat Enterprise Linux 9", "Ubuntu 22.04"


def _vm(name="prod-db-01", os=UBUNTU, disks=(20,)):
    return NormalizedVM(vm_name=name, resource_name=name.replace("-", "_"), cpu=2, memory_gib=8,
                        disks_gib=list(disks), os=os)


def _plan(target, *vms):
    return build_migration_plan(list(vms), "acme", get_target(target))


# --- boot disks ------------------------------------------------------------------------


@pytest.mark.parametrize("target,os,least", [
    ("gcp", WINDOWS, 50), ("gcp", RHEL, 30), ("azure", WINDOWS, 127), ("azure", RHEL, 64),
    ("azure", UBUNTU, 30), ("oci", UBUNTU, 50), ("oci", WINDOWS, 50), ("aws", WINDOWS, 30)])
@pytest.mark.parametrize("disk", [1, 20, 40, 49])
def test_the_boot_disk_is_never_below_the_images(target, os, least, disk):
    c = _plan(target, _vm(os=os, disks=(disk,))).compute[0]
    assert c.root_volume_gib == max(least, disk)


@pytest.mark.parametrize("target", list_targets())
@pytest.mark.parametrize("os", [WINDOWS, RHEL, UBUNTU])
def test_every_target_states_a_minimum_and_keeps_to_it(target, os):
    t = get_target(target)
    c = _plan(target, _vm(os=os, disks=(1, 5))).compute[0]
    assert c.root_volume_gib >= t.min_boot_gib(c.image_key) > 0
    assert c.extra_volumes_gib == [5]


@pytest.mark.parametrize("target", list_targets())
def test_a_big_enough_disk_is_left_alone(target):
    c = _plan(target, _vm(os=WINDOWS, disks=(300, 20))).compute[0]
    assert (c.root_volume_gib, c.extra_volumes_gib) == (300, [20])
    assert "Boot disk raised" not in c.reason and "No disk" not in c.reason


def test_raising_or_assuming_a_disk_is_said():
    assert "Boot disk raised from 40 to 50 GiB" in _plan("gcp", _vm(os=WINDOWS, disks=(40,))).compute[0].reason
    assert "No disk in the inventory: a 50 GiB" in _plan("oci", _vm(disks=())).compute[0].reason


@pytest.mark.parametrize("layout", LAYOUTS)
def test_oracle_takes_the_images_own_size_when_larger(layout):
    compute = render(layout, _plan("oci", _vm()), get_target("oci"))["compute.tf"]
    assert re.search(r"= max\(50, ceil\(data\.oci_core_images\.\w+\.images\[0\]\.size_in_mbs / 1024\)\)", compute)


# --- google ----------------------------------------------------------------------------


@pytest.mark.parametrize("layout", LAYOUTS)
def test_attached_disks_are_not_fought_over(layout):
    compute = render(layout, _plan("gcp", _vm(disks=(30, 10)), _vm("prod-web-01")), get_target("gcp"))["compute.tf"]
    assert compute.count("ignore_changes = [attached_disk]") == compute.count('resource "google_compute_instance"')


def test_an_internal_load_balancers_probes_are_let_in():
    plan = _plan("gcp", _vm("prod-app-01"), _vm("prod-app-02"), _vm("prod-web-01"), _vm("prod-web-02"))
    internal = [lb for lb in plan.network.load_balancers if not lb.internet_facing]
    assert internal
    for layout in LAYOUTS:
        text = render(layout, plan, get_target("gcp"))["loadbalancer.tf"]
        (name, rule), = re.findall(r'"google_compute_firewall" "(\w+)" \{(.*?)^}', text, re.S | re.M)
        assert name == "lb_probes" and re.search(r'source_ranges\s+= \["35\.191\.0\.0/16"\]', rule)
        for lb in internal:
            assert f'"{lb.security_group}"' in rule and f'"{lb.listeners[0].target_port}"' in rule
            assert all(c.security_group == lb.security_group for c in plan.compute if c.vm_name in lb.targets)


def test_no_probe_rule_without_an_internal_load_balancer():
    plan = _plan("gcp", _vm("prod-web-01"), _vm("prod-web-02"))
    assert plan.network.load_balancers
    assert "lb_probes" not in render("terraform", plan, get_target("gcp"))["loadbalancer.tf"]


def test_pulumi_lets_the_probes_in_too():
    plan = _plan("gcp", _vm("prod-app-01"), _vm("prod-app-02"))
    internal = plan.network.load_balancers
    program = render("pulumi", plan, get_target("gcp"))["__main__.py"]
    assert program.count('"35.191.0.0/16"') == len(internal)
    compile(program, "__main__.py", "exec")


# --- vmware csv ------------------------------------------------------------------------


@pytest.mark.parametrize("header,value,gib", [
    ("Provisioned MB", 40960, 40), ("Provisioned MiB", 40960, 40), ("Provisioned GB", 40, 40),
    ("Provisioned GiB", 40, 40), ("Disk MB", 40960, 40), ("Disk GB", 40, 40), ("provisioned mb", 40960, 40)])
def test_the_disk_column_is_read_in_its_own_unit(tmp_path, header, value, gib):
    source = tmp_path / "in.csv"
    source.write_text(f"Name,CPUs,Memory MB,{header},Guest OS,Power State,Cluster\n"
                      f"x,2,4096,{value},Ubuntu,poweredOn,c\n")
    assert normalize(vmware_csv.parse(str(source)))[0].disks_gib == [gib]
