"""Multi-renderer: the same plan rendered as Terraform, Pulumi, or CloudFormation."""
import json
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


def test_registry_lists_all_three():
    assert set(list_renderers()) == {"terraform", "pulumi", "cloudformation"}


def test_unknown_renderer_raises(rvtools_path):
    plan, _ = _plan(rvtools_path)
    with pytest.raises(UnknownRendererError):
        render("bicep", plan, get_target("aws"))


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


def test_cloudformation_unsupported_targets_raise(rvtools_path):
    from iactranslate.renderers.cloudformation import RendererNotSupportedError

    for cloud in ("azure", "gcp"):
        plan, _ = _plan(rvtools_path, target=cloud)
        with pytest.raises(RendererNotSupportedError):
            render("cloudformation", plan, get_target(cloud))


def test_cloudformation_template_is_valid_json(rvtools_path):
    plan, _ = _plan(rvtools_path)
    files = render("cloudformation", plan, get_target("aws"))
    assert set(files) == {"template.json", "README.md"}
    template = json.loads(files["template.json"])
    assert template["AWSTemplateFormatVersion"] == "2010-09-09"
    assert "Resources" in template and "Outputs" in template


def test_cloudformation_resources_have_type_and_properties(rvtools_path):
    plan, _ = _plan(rvtools_path)
    template = json.loads(render("cloudformation", plan, get_target("aws"))["template.json"])
    for logical_id, res in template["Resources"].items():
        assert "Type" in res, logical_id
        assert res["Type"].startswith("AWS::"), logical_id
        assert "Properties" in res, logical_id


def test_cloudformation_covers_core_resources(rvtools_path):
    plan, _ = _plan(rvtools_path)
    template = json.loads(render("cloudformation", plan, get_target("aws"))["template.json"])
    types = [res["Type"] for res in template["Resources"].values()]
    for t in ("AWS::EC2::VPC", "AWS::EC2::Subnet", "AWS::EC2::SecurityGroup", "AWS::EC2::Instance"):
        assert t in types
    assert types.count("AWS::EC2::Instance") == plan.vm_count


def test_cloudformation_instances_reference_valid_logical_ids(rvtools_path):
    plan, _ = _plan(rvtools_path)
    template = json.loads(render("cloudformation", plan, get_target("aws"))["template.json"])
    resources = template["Resources"]
    for logical_id, res in resources.items():
        if res["Type"] != "AWS::EC2::Instance":
            continue
        props = res["Properties"]
        assert props["SubnetId"]["Ref"] in resources
        for sg_ref in props["SecurityGroupIds"]:
            assert sg_ref["Ref"] in resources
        assert props["BlockDeviceMappings"][0]["Ebs"]["VolumeSize"] >= 8


def test_cloudformation_per_os_image_resolution(rvtools_path):
    """SSM-backed OSes need no parameter; others get an AWS::EC2::Image::Id parameter."""
    plan, _ = _plan(rvtools_path)
    files = render("cloudformation", plan, get_target("aws"))
    template = json.loads(files["template.json"])
    image_keys = {c.image_key for c in plan.compute}

    from iactranslate.renderers.cloudformation import _ami_dynamic_ref, _ami_parameter_name

    ssm_keys = {k for k in image_keys if _ami_dynamic_ref(k) is not None}
    param_keys = image_keys - ssm_keys

    for iid, res in template["Resources"].items():
        if res["Type"] != "AWS::EC2::Instance":
            continue
        image_id = res["Properties"]["ImageId"]
        if isinstance(image_id, str):
            assert image_id.startswith("{{resolve:ssm:")
        else:
            assert image_id["Ref"] in template.get("Parameters", {})

    if param_keys:
        for key in param_keys:
            pname = _ami_parameter_name(key)
            assert template["Parameters"][pname]["Type"] == "AWS::EC2::Image::Id"
            assert pname in files["README.md"]
    if ssm_keys:
        assert not any(_ami_parameter_name(k) in template.get("Parameters", {}) for k in ssm_keys)


def test_cloudformation_is_deterministic(rvtools_path):
    plan, _ = _plan(rvtools_path)
    a = render("cloudformation", plan, get_target("aws"))
    b = render("cloudformation", plan, get_target("aws"))
    assert a == b
