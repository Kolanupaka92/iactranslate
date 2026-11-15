"""Which machines get their own public address is decided once, and every
renderer gives an address to exactly those machines.

The rule ("public subnet, and no load balancer in front") was written out in
six Terraform templates, twice in Pulumi and in Bicep, with Pulumi and Bicep
deriving "fronted" a different way from the templates. ADR 0016 is the record
of what happens to a decision that lives in several places.
"""
import re

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.graph import NodeKind, build_graph, public_instances
from iactranslate.models import SubnetTier
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.renderers import render
from iactranslate.targets import get_target


def _plan(cloud):
    vms = normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))
    return build_migration_plan(vms, "pub", get_target(cloud))


def _labels(plan, names):
    by_name = {c.vm_name: c.resource_name for c in plan.compute}
    return {by_name[n] for n in names}


def test_the_rule():
    plan = _plan("azure")
    fronted = {vm for lb in plan.network.load_balancers for vm in lb.targets}
    public = public_instances(plan)
    assert public  # the fixture has a public, unfronted machine
    for c in plan.compute:
        expected = c.subnet_tier == SubnetTier.PUBLIC and c.vm_name not in fronted
        assert (c.vm_name in public) == expected, c.vm_name
    # A fronted machine in a public subnet stays private: the LB is its entry.
    assert any(c.subnet_tier == SubnetTier.PUBLIC and c.vm_name in fronted for c in plan.compute)


def test_the_graph_records_it():
    plan = _plan("gcp")
    public = public_instances(plan)
    for node in build_graph(plan).nodes_of(NodeKind.INSTANCE):
        assert node.attributes["public_ip"] == (node.name in public), node.name


def test_azure_renderers_agree():
    plan = _plan("azure")
    expected = _labels(plan, public_instances(plan))
    machines = {c.resource_name for c in plan.compute}

    tf = "".join(render("terraform", plan, get_target("azure")).values())
    assert set(re.findall(r'resource "azurerm_public_ip" "(\w+)"', tf)) & machines == expected

    fe = render("terraform-foreach", plan, get_target("azure"))["compute.tf"]
    rows = dict(re.findall(r'^    "(\w+)" = \{.*?public_ip\s+= (true|false)', fe, re.MULTILINE | re.DOTALL))
    assert {k for k, v in rows.items() if v == "true"} == expected

    pulumi = "".join(render("pulumi", plan, get_target("azure")).values())
    # Load-balancer front ends are `pip_<lb>` too; only machine addresses count.
    pips = set(re.findall(r"^pip_(\w+) = azure\.network\.PublicIp\(", pulumi, re.MULTILINE))
    assert pips & machines == expected
    # Every NIC referencing a public IP references one that is declared.
    assert set(re.findall(r'"public_ip_address_id": pip_(\w+)\.id', pulumi)) & machines == expected

    bicep = "".join(render("bicep", plan, get_target("azure")).values())
    bicep_vm_pips = re.findall(r"name: '([\w-]+)-pip'", bicep)
    assert set(bicep_vm_pips) & machines == expected


@pytest.mark.parametrize("renderer", ["terraform", "terraform-foreach", "pulumi"])
def test_gcp_renderers_agree(renderer):
    plan = _plan("gcp")
    expected = _labels(plan, public_instances(plan))
    out = "".join(render(renderer, plan, get_target("gcp")).values())
    if renderer == "pulumi":
        pattern = r'inst_(\w+) = gcp\.compute\.Instance\((?:(?!\ninst_).)*?"access_configs": \[\{\}\]'
        got = set(re.findall(pattern, out, re.S))
    elif renderer == "terraform":
        pattern = r'resource "google_compute_instance" "(\w+)" \{(?:(?!\nresource ).)*?access_config \{'
        got = set(re.findall(pattern, out, re.S))
    else:
        rows = re.findall(r'^    "(\w+)" = \{.*?external_ip\s+= (true|false)', out, re.MULTILINE | re.DOTALL)
        got = {k for k, v in rows if v == "true"}
    assert got == expected
