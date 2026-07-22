"""Multi-renderer: the same plan rendered as Terraform or Pulumi."""
import py_compile

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.normalize import normalize
from iactranslate.renderers import (
    UnknownRendererError,
    list_renderers,
    render,
)
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


def _plan(path, target="aws"):
    vms = normalize(resolve_source(path).parse(path))
    return build_migration_plan(vms, "r", get_target(target)), vms


def test_registry_lists_both():
    assert set(list_renderers()) == {"terraform", "pulumi"}


def test_unknown_renderer_raises(rvtools_path):
    plan, _ = _plan(rvtools_path)
    with pytest.raises(UnknownRendererError):
        render("cloudformation", plan, get_target("aws"))


@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp"])
def test_pulumi_program_compiles(rvtools_path, tmp_path, cloud):
    plan, _ = _plan(rvtools_path, target=cloud)
    files = render("pulumi", plan, get_target(cloud))
    assert set(files) >= {"__main__.py", "Pulumi.yaml", "requirements.txt", "README.md"}
    assert f"pulumi-{cloud}" in files["requirements.txt"]
    main = tmp_path / "main.py"
    main.write_text(files["__main__.py"])
    py_compile.compile(str(main), doraise=True)  # raises on a syntax error


def test_pulumi_covers_core_resources_aws(rvtools_path):
    plan, _ = _plan(rvtools_path)
    main = render("pulumi", plan, get_target("aws"))["__main__.py"]
    for res in ("aws.ec2.Vpc(", "aws.ec2.Subnet(", "aws.ec2.SecurityGroup(",
                "aws.ec2.Instance(", "aws.ec2.get_ami("):
        assert res in main
    assert main.count("aws.ec2.Instance(") == plan.vm_count


def test_pulumi_covers_core_resources_azure(rvtools_path):
    plan, _ = _plan(rvtools_path, target="azure")
    main = render("pulumi", plan, get_target("azure"))["__main__.py"]
    for res in ("azure.core.ResourceGroup(", "azure.network.VirtualNetwork(",
                "azure.network.NetworkSecurityGroup(", "azure.network.NetworkInterface("):
        assert res in main
    # One VM (linux or windows) per workload.
    vms = main.count("azure.compute.LinuxVirtualMachine(") + main.count("azure.compute.WindowsVirtualMachine(")
    assert vms == plan.vm_count


def test_pulumi_covers_core_resources_gcp(rvtools_path):
    plan, _ = _plan(rvtools_path, target="gcp")
    main = render("pulumi", plan, get_target("gcp"))["__main__.py"]
    for res in ("gcp.compute.Network(", "gcp.compute.Subnetwork(",
                "gcp.compute.Firewall(", "gcp.compute.Instance("):
        assert res in main
    assert main.count("gcp.compute.Instance(") == plan.vm_count


def test_terraform_renderer_matches_generator(rvtools_path):
    from iactranslate.generator import build_files

    plan, _ = _plan(rvtools_path)
    assert render("terraform", plan, get_target("aws")) == build_files(plan, get_target("aws"))
