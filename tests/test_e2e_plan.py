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
    return json.dumps(_zero(images[1]))


@lru_cache(maxsize=1)
def _ssh_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    return key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()


def _plan(out):
    text = "".join(p.read_text() for p in out.glob("*.tf"))
    block = re.search(r"required_providers\s*\{(.*?)\n  \}", text, re.S).group(1)
    providers = re.findall(r"^\s+(\w+)\s*=\s*\{", block, re.M)
    init = _run(["init", "-input=false", "-no-color"], out)
    assert init.returncode == 0, init.stderr
    mocks = "".join(f'mock_provider "{p}" {{\n{MOCKS.get(p, "")}}}\n' for p in providers)
    if "@OCI_IMAGE@" in mocks:
        mocks = mocks.replace("@OCI_IMAGE@", _oci_image(out))
    # The one input the operator always supplies.
    key = f'  variables {{\n    admin_ssh_public_key = "{_ssh_key()}"\n  }}\n' if "admin_ssh_public_key" in text else ""
    (out / "plan.tftest.hcl").write_text(mocks + f'run "plan" {{\n  command = plan\n{key}}}\n')
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
