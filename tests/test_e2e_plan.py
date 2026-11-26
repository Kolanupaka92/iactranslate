"""Generated Terraform plans, and passes `fmt -check`, on every target (ADR 0084).

`validate` treats variables as unknown, so it never checks a real name against a
provider's limits. The console's own example project name produced Google code
that validated and could not be applied. `plan` evaluates everything; providers
are mocked, so no account is needed, but each provider's real schema and
argument checks still run.

Not covered: anything only the cloud knows, such as quotas, permissions, whether
an image exists, or a size being sold in a region.

Needs OpenTofu and the network for provider downloads: IACTRANSLATE_E2E_TOFU=1.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from iactranslate.cli import main as cli
from iactranslate.generator.hclfmt import format_hcl
from iactranslate.models import resource_prefix, stack_name

TOFU = shutil.which("tofu") or shutil.which("terraform")
needs_tofu = pytest.mark.skipif(os.getenv("IACTRANSLATE_E2E_TOFU") != "1" or TOFU is None,
                                reason="set IACTRANSLATE_E2E_TOFU=1 with tofu installed")
TARGETS = ["aws", "azure", "gcp", "oci", "digitalocean", "nutanix", "proxmox"]
LAYOUTS = ["terraform", "terraform-foreach"]

_RG = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg/providers"
_NET = f"{_RG}/Microsoft.Network"
_UUID = "00000000-0000-0000-0000-000000000001"


def _azure(kind, value):
    return f'  mock_resource "azurerm_{kind}" {{\n    defaults = {{ id = "{value}" }}\n  }}\n'


#: Stand-in values each provider's own checks accept. They say nothing about
#: the generated code; without them a plan fails on the mock's random strings.
MOCKS = {
    "aws": ('  mock_data "aws_availability_zones" {\n'
            '    defaults = { names = ["us-east-1a", "us-east-1b", "us-east-1c"] }\n  }\n'
            '  mock_resource "aws_lb" {\n    defaults = { arn = '
            '"arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/x/0123456789abcdef" }\n  }\n'
            '  mock_resource "aws_lb_target_group" {\n    defaults = { arn = '
            '"arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/x/0123456789abcdef" }\n  }\n'),
    "azurerm": "".join(_azure(k, v) for k, v in {
        "subnet": f"{_NET}/virtualNetworks/v/subnets/s",
        "virtual_network": f"{_NET}/virtualNetworks/v",
        "public_ip": f"{_NET}/publicIPAddresses/p",
        "lb": f"{_NET}/loadBalancers/l",
        "lb_backend_address_pool": f"{_NET}/loadBalancers/l/backendAddressPools/b",
        "lb_probe": f"{_NET}/loadBalancers/l/probes/p",
        "network_security_group": f"{_NET}/networkSecurityGroups/n",
        "network_interface": f"{_NET}/networkInterfaces/n",
        "managed_disk": f"{_RG}/Microsoft.Compute/disks/d",
        "linux_virtual_machine": f"{_RG}/Microsoft.Compute/virtualMachines/v",
        "windows_virtual_machine": f"{_RG}/Microsoft.Compute/virtualMachines/v",
        "user_assigned_identity": f"{_RG}/Microsoft.ManagedIdentity/userAssignedIdentities/i",
    }.items()),
    "oci": ('  mock_data "oci_identity_availability_domains" {\n'
            '    defaults = { availability_domains = [{ name = "AD-1", id = "a", compartment_id = "c" }] }\n  }\n'
            '  mock_data "oci_core_images" {\n    defaults = { images = [@OCI_IMAGE@] }\n  }\n'),
    "digitalocean": '  mock_resource "digitalocean_droplet" {\n    defaults = { id = "12345" }\n  }\n',
    "nutanix": (f'  mock_data "nutanix_cluster" {{\n    defaults = {{ id = "{_UUID}" }}\n  }}\n'
                f'  mock_data "nutanix_image" {{\n    defaults = {{ id = "{_UUID}" }}\n  }}\n'
                f'  mock_resource "nutanix_subnet" {{\n    defaults = {{ id = "{_UUID}" }}\n  }}\n'),
    "proxmox": ('  mock_resource "proxmox_virtual_environment_vm" {\n    defaults = { vm_id = 200 }\n  }\n'),
}


def _generate(tmp_path, target, layout, name, source="tests/fixtures/rvtools_realistic.xlsx"):
    out = tmp_path / "out"
    assert cli(["translate", source, "--target", target, "--renderer", layout, "--name", name,
                "--out", str(out)]) == 0
    return out


def _run(args, cwd):
    return subprocess.run([TOFU] + args, cwd=cwd, capture_output=True, text=True)


def _zero(type_):
    """A value of a provider schema type; a mock must fill every field."""
    if type_ in ("string", "number", "bool"):
        return {"string": "x", "number": 0, "bool": False}[type_]
    kind, inner = type_
    return {k: _zero(v) for k, v in inner.items()} if kind == "object" else ({} if kind == "map" else [])


def _oci_image(out):
    schema = json.loads(_run(["providers", "schema", "-json"], out).stdout)["provider_schemas"]
    provider = next(v for k, v in schema.items() if k.endswith("/oci"))
    images = provider["data_source_schemas"]["oci_core_images"]["block"]["attributes"]["images"]["type"]
    return json.dumps({**_zero(images[1]), "size_in_mbs": "47694"})


@lru_cache(maxsize=1)
def _ssh_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    return key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()


def _plan(out, runs=lambda out: 'run "plan" {\n  command = plan\n}\n'):
    text = "".join(p.read_text() for p in out.glob("*.tf"))
    block = re.search(r"required_providers\s*\{(.*?)\n  \}", text, re.S).group(1)
    providers = re.findall(r"^\s+(\w+)\s*=\s*\{", block, re.M)
    init = _run(["init", "-input=false", "-no-color"], out)
    assert init.returncode == 0, init.stderr
    mocks = "".join(f'mock_provider "{p}" {{\n{MOCKS.get(p, "")}}}\n' for p in providers)
    if "@OCI_IMAGE@" in mocks:
        mocks = mocks.replace("@OCI_IMAGE@", _oci_image(out))
    inputs = {}
    if "admin_ssh_public_key" in text:
        inputs["admin_ssh_public_key"] = _ssh_key()  # the one input the operator always supplies
    if 'check "https_certificate"' in text:  # so the HTTPS listeners are planned too
        inputs["acm_certificate_arn"] = f"arn:aws:acm:us-east-1:123456789012:certificate/{_UUID}"
    key = "variables {\n" + "".join(f'  {k} = "{v}"\n' for k, v in inputs.items()) + "}\n" if inputs else ""
    (out / "plan.tftest.hcl").write_text(mocks + key + runs(out))
    return _run(["test", "-no-color"], out)


@needs_tofu
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("target", TARGETS)
def test_the_consoles_example_name_plans_on_every_target(tmp_path, target, layout):
    result = _plan(_generate(tmp_path, target, layout, "acme-datacenter-migration"))
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]


@needs_tofu
@pytest.mark.parametrize("name", ["x", "n" * 128, "Acme Migration", "Contoso (EMEA) Datacentre Exit 2026"])
@pytest.mark.parametrize("target", TARGETS)
def test_awkward_project_names_plan(tmp_path, target, name):
    result = _plan(_generate(tmp_path, target, "terraform", name))
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]


@needs_tofu
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("target", TARGETS)
def test_generated_code_passes_the_pipelines_fmt_check(tmp_path, target, layout):
    out = _generate(tmp_path, target, layout, "acme-datacenter-migration")
    result = _run(["fmt", "-check", "-diff", "-recursive", "-no-color"], out)
    assert result.returncode == 0, result.stdout[-3000:]


@needs_tofu
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("target", ["aws", "oci"])
def test_a_newer_image_replaces_nothing_that_is_running(tmp_path, target, layout):
    """Apply (mocked), then plan with a newer image: the server keeps its own (ADR 0090)."""
    source = tmp_path / "in.csv"
    source.write_text("name,cpu,memory_gib,disk_gib,os\napp01,2,4,60,Ubuntu 22.04\n")
    out = _generate(tmp_path, target, layout, "acme", str(source))
    server = '.vm["app01"]' if layout == "terraform-foreach" else ".app01"

    def runs(out):
        if target == "aws":
            def image(n):
                return f'  variables {{\n    ami_overrides = {{ "ubuntu-22.04" = "{n}" }}\n  }}\n'
            kept = f"aws_instance{server}.ami"
        else:
            data = re.search(r'data "(oci_core_images" "\w+)"', (out / "images.tf").read_text()).group(1)
            fields = json.loads(_oci_image(out))

            def image(n):
                values = json.dumps({**fields, "id": n})
                return (f'  override_data {{\n    target = data.{data.replace(chr(34) + " " + chr(34), ".")}\n'
                        f"    values = {{ images = [{values}] }}\n  }}\n")
            kept = f"oci_core_instance{server}.source_details[0].source_id"
        check = f'  assert {{\n    condition     = {kept} == "ami-a"\n    error_message = "replaced"\n  }}\n'
        return (f'run "deploy" {{\n  command = apply\n{image("ami-a")}{check}}}\n'
                f'run "later" {{\n  command = plan\n{image("ami-b")}{check}}}\n')

    result = _plan(out, runs)
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]


@needs_tofu
@pytest.mark.parametrize("layout", LAYOUTS)
def test_an_adopted_server_is_never_moved(tmp_path, layout):
    """Deploy, then let the new network's subnets and security group change. An
    adopted server keeps its own; a new one follows (ADR 0091). Mocked providers
    can't import, so the adopted server is deployed instead."""
    source = tmp_path / "in.csv"
    source.write_text("Instance Name,InstanceId,InstanceType,Platform,VolumeSize\n"
                      "app01,i-0123456789abcdef0,m5.large,linux,40\napp02,,m5.large,linux,40\n")
    out = _generate(tmp_path, "aws", layout, "acme", str(source))
    (out / "imports.tf").unlink()

    def network(old_or_new):
        return "".join(f'  override_resource {{\n    target = {r}\n    values = {{ id = "{p}-0{old_or_new}" }}\n  }}\n'
                       for r, p in (("aws_subnet.private_0", "subnet"), ("aws_subnet.private_1", "subnet"),
                                    ("aws_security_group.app_sg", "sg")))

    def at(vm):
        return f'aws_instance.adopted["{vm}"]' if layout == "terraform-foreach" and vm == "app01" else (
            f'aws_instance.vm["{vm}"]' if layout == "terraform-foreach" else f"aws_instance.{vm}")

    checks = "".join(
        f'  assert {{\n    condition     = {at(vm)}.subnet_id == "subnet-0{want}" && '
        f'tolist({at(vm)}.vpc_security_group_ids)[0] == "sg-0{want}"\n    error_message = "{vm}"\n  }}\n'
        for vm, want in (("app01", "old"), ("app02", "new")))
    result = _plan(out, lambda out: f'run "deploy" {{\n  command = apply\n{network("old")}}}\n'
                                    f'run "later" {{\n  command = plan\n{network("new")}{checks}}}\n')
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]


# --- the aligner itself, without tofu --------------------------------------------------


def test_alignment_groups_follow_terraform_fmt():
    source = "\n".join([
        "a = 1", "long_name = 2", "",
        "x = 1", "# a comment ends the group", "longer = 2", "",
        "name = \"v\"", "opened    = {", "  k = 1", "  key2 = 2", "}", "",
        'eq = a == b ? "x=y" : "${var.z == 1 ? "q" : "r"}"', "lambda = { for k, v in m : k => v }", "",
        "one = 1 # first", "three = 333 # second",
    ])
    assert format_hcl(source) == "\n".join([
        "a         = 1", "long_name = 2", "",
        "x = 1", "# a comment ends the group", "longer = 2", "",
        "name = \"v\"", "opened = {", "  k    = 1", "  key2 = 2", "}", "",
        'eq     = a == b ? "x=y" : "${var.z == 1 ? "q" : "r"}"', "lambda = { for k, v in m : k => v }", "",
        "one   = 1   # first", "three = 333 # second",
    ])


def test_heredoc_text_is_left_alone_and_its_name_aligns():
    source = "a = <<-EOT\n  x=1\n  longer   = 2\nEOT\nb = 1\nlonger = 2"
    assert format_hcl(source) == "a      = <<-EOT\n  x=1\n  longer   = 2\nEOT\nb      = 1\nlonger = 2"


def test_formatting_is_stable():
    once = format_hcl("a = 1\nlonger = 2 # c\n")
    assert format_hcl(once) == once


# --- names -----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["acme-datacenter-migration", "Acme_Migration", "acme.prod", "x", "n" * 48])
def test_a_usable_name_is_never_changed(name):
    assert resource_prefix(name) == name  # renaming would replace live resources


@pytest.mark.parametrize("name,expected", [
    ("Acme Migration", "Acme-Migration"), ("Contoso (EMEA) Exit", "Contoso-EMEA-Exit"),
    ("  ", "project"), ("_acme_", "acme")])
def test_an_unusable_name_becomes_usable(name, expected):
    assert resource_prefix(name) == expected


def test_long_names_stay_short_and_distinct():
    a, b = resource_prefix("acme-" * 20 + "east"), resource_prefix("acme-" * 20 + "west")
    assert a != b and len(a) <= 48 and len(b) <= 48


@pytest.mark.parametrize("name,expected", [("Acme Migration", "Acme-Migration"), ("2026_exit", "n-2026-exit")])
def test_stack_names(name, expected):
    assert stack_name(name) == expected
