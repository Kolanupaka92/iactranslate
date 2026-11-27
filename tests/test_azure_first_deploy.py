"""What the first real Azure deployment refused or drifted on (ADR 0099)."""
from __future__ import annotations

import re

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.generator.renderer import windows_computer_name
from iactranslate.models import NormalizedVM
from iactranslate.renderers import render
from iactranslate.targets import get_target

LAYOUTS = ["terraform", "terraform-foreach"]
WINDOWS, UBUNTU = "Windows Server 2022", "Ubuntu 22.04"
AZ = get_target("azure")


def _vm(name, os=UBUNTU, disks=(30,)):
    return NormalizedVM(vm_name=name, resource_name=name.replace("-", "_"), cpu=2, memory_gib=8,
                        disks_gib=list(disks), os=os)


def _plan(*vms, name="aztest"):
    return build_migration_plan(list(vms), name, AZ)


ESTATE = [_vm("prod-web-01"), _vm("prod-web-02", WINDOWS, (60, 10)),
          _vm("prod-sql-server-01", WINDOWS), _vm("prod-sql-server-02", WINDOWS)]


def _machines(text):
    return re.findall(r'^resource "azurerm_(linux|windows)_virtual_machine" "\w+" \{(.*?)^}', text, re.S | re.M)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_linux_machines_get_their_tiers_identity_as_windows_ones_do(layout):
    machines = _machines(render(layout, _plan(*ESTATE), AZ)["compute.tf"])
    assert {kind for kind, _ in machines} == {"linux", "windows"}
    assert all('type         = "UserAssigned"' in body for _, body in machines)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_azures_own_agent_updates_setting_is_left_alone(layout):  # else every plan drifts
    machines = _machines(render(layout, _plan(*ESTATE), AZ)["compute.tf"])
    assert all("ignore_changes = [vm_agent_platform_updates_enabled]" in body for _, body in machines)
    program = render("pulumi", _plan(*ESTATE), AZ)["__main__.py"]
    assert program.count('ignore_changes=["vmAgentPlatformUpdatesEnabled"]') == 4


@pytest.mark.parametrize("name", ["prod-web-02", "prod-sql-server-01", "WIN_DC_01", "a" * 80, "123"])
def test_windows_computer_names_fit_azures_15_characters(name):
    computer = windows_computer_name(name)
    assert re.fullmatch(r"[a-z][-a-z0-9]{0,14}", computer) and not computer.endswith("-")


def test_long_windows_names_that_start_alike_stay_apart():
    assert windows_computer_name("prod-sql-server-01") != windows_computer_name("prod-sql-server-02")


def test_every_azure_renderer_gives_windows_a_short_computer_name():
    plan = _plan(*ESTATE)
    want = {windows_computer_name(n) for n in ("prod-web-02", "prod-sql-server-01", "prod-sql-server-02")}
    for layout in LAYOUTS:
        text = render(layout, plan, AZ)["compute.tf"]
        assert want <= set(re.findall(r'computer(?:_name)?\s+= "([^"]+)"', text)), layout
    bicep = render("bicep", plan, AZ)["resources.bicep"]
    names = set(re.findall(r"computerName: '([^']+)'", bicep))
    assert want <= names and "prod-web-01" in names
    assert all("_" not in n for n in names)
    program = render("pulumi", plan, AZ)["__main__.py"]
    assert set(re.findall(r'computer_name="([^"]+)"', program)) == want


def test_bicep_machines_are_named_without_underscores():
    bicep = render("bicep", _plan(*ESTATE), AZ)["resources.bicep"]
    vms = re.findall(r"'Microsoft.Compute/virtualMachines@[^']+' = \{\n  name: '([^']+)'", bicep)
    assert sorted(vms) == sorted(["prod-web-01", "prod-web-02", "prod-sql-server-01", "prod-sql-server-02"])


def test_bicep_declares_subnets_inside_the_network():  # Azure refuses them created side by side
    bicep = render("bicep", _plan(*ESTATE), AZ)["resources.bicep"]
    vnet = re.search(r"'Microsoft.Network/virtualNetworks@[^']+' = \{(.*?)^}", bicep, re.S | re.M).group(1)
    assert vnet.count("addressPrefix:") == 4
    standalone = re.findall(r"'Microsoft.Network/virtualNetworks/subnets@[^']+'( existing)? = \{", bicep)
    assert standalone and all(s == " existing" for s in standalone)


def test_pulumi_names_every_named_azure_resource_itself():  # its own names break 24 characters
    program = render("pulumi", _plan(*ESTATE, name="a-rather-long-project-name"), AZ)["__main__.py"]
    calls = re.findall(r"^(?:\w+ = )?azure\.(\w+)\.(\w+)\(\n((?:    .*\n)*?)\)", program, re.M)
    unnamed = {f"{m}.{t}" for m, t, body in calls
               if "    name=" not in body and not t.endswith(("Association", "Attachment"))}
    assert not unnamed
    assert not re.search(r'    name="[^"]*_', program)


@pytest.mark.parametrize("target", ["azure", "gcp"])
@pytest.mark.parametrize("renderer", ["terraform", "terraform-foreach", "pulumi", "bicep"])
def test_machines_whose_names_clean_up_alike_still_get_their_own(target, renderer):
    if renderer == "bicep" and target != "azure":
        pytest.skip("Bicep is Azure only")
    t = get_target(target)
    vms = [_vm("DEV_WEB_01"), _vm("dev web 01"), _vm("prod-db-01")]
    files = render(renderer, build_migration_plan(vms, "acme", t), t)
    text = "\n".join(v for k, v in files.items() if k.endswith((".tf", ".py", ".bicep")))
    named = set(re.findall(r"""["']((?:dev-web|prod-db)-01(?:-2)?)["']""", text))
    assert named == {"dev-web-01", "dev-web-01-2", "prod-db-01"}, named


def test_names_that_never_clashed_do_not_change():  # existing deployments keep their machines
    from iactranslate.generator.renderer import machine_slugs
    plan = _plan(_vm("Prod Web 01"), _vm("prod-db-01"))
    assert machine_slugs(plan.compute) == {"prod_web_01": "prod-web-01", "prod_db_01": "prod-db-01"}


# --- second round: the gaps ADR 0099 left (ADR 0100) ---------------------------------

APP = [_vm("prod-app-01", "Red Hat Enterprise Linux 9"), _vm("prod-app-02")]


def test_bicep_and_pulumi_give_every_azure_machine_its_tiers_identity():
    plan = _plan(*ESTATE, _vm("prod-db-01"))
    bicep = render("bicep", plan, AZ)["resources.bicep"]
    tiers = {c.tier.value for c in plan.compute}
    assert bicep.count("Microsoft.ManagedIdentity/userAssignedIdentities@") == len(tiers)
    assert bicep.count("userAssignedIdentities: {") == len(plan.compute)
    program = render("pulumi", plan, AZ)["__main__.py"]
    assert program.count("azure.authorization.UserAssignedIdentity(") == len(tiers)
    assert program.count('identity={"type": "UserAssigned"') == len(plan.compute)


@pytest.mark.parametrize("renderer", ["terraform", "terraform-foreach", "bicep", "pulumi"])
def test_private_azure_machines_go_out_through_a_nat_gateway(renderer):
    plan = _plan(*APP)
    assert plan.network.nat_gateway
    text = "\n".join(render(renderer, plan, AZ).values())
    private = [s for s in plan.network.subnets if s.tier.value == "private"]
    if renderer == "bicep":
        assert text.count("natGateway: { id: natGateway.id }") == len(private)
    elif renderer == "pulumi":
        assert text.count("azure.network.NatGateway(") == 1
        assert text.count("azure.network.SubnetNatGatewayAssociation(") == len(private)
    else:
        assert text.count('resource "azurerm_nat_gateway" "main"') == 1
        assert text.count('resource "azurerm_subnet_nat_gateway_association"') == len(private)


@pytest.mark.parametrize("renderer", ["terraform", "bicep", "pulumi"])
def test_no_nat_gateway_when_no_machine_is_private(renderer):
    plan = _plan(_vm("prod-web-01"), _vm("prod-web-02"))
    assert not plan.network.nat_gateway
    text = "\n".join(render(renderer, plan, AZ).values())
    assert "azurerm_nat_gateway" not in text and "natGateways@" not in text and "NatGateway(" not in text


@pytest.mark.parametrize("cpu,memory,size", [(1, 1, "Standard_B1s"), (1, 2, "Standard_B1ms"), (2, 4, "Standard_B2s")])
def test_one_vcpu_machines_are_not_sized_up_on_azure(cpu, memory, size):
    vm = NormalizedVM(vm_name="prod-app-01", resource_name="prod_app_01", cpu=cpu, memory_gib=memory,
                      disks_gib=[30], os=UBUNTU)
    assert _plan(vm).compute[0].instance_type == size
