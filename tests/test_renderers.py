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


def test_registry_lists_all_six():
    assert set(list_renderers()) == {
        "terraform", "pulumi", "cloudformation", "bicep", "cdk", "kubernetes",
    }


def test_unknown_renderer_raises(rvtools_path):
    plan, _ = _plan(rvtools_path)
    with pytest.raises(UnknownRendererError):
        render("crossplane", plan, get_target("aws"))


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


def test_pulumi_emits_load_balancers_aws(rvtools_path):
    plan, _ = _plan(rvtools_path, target="aws")
    main = render("pulumi", plan, get_target("aws"))["__main__.py"]
    assert main.count("aws.lb.LoadBalancer(") == len(plan.network.load_balancers)
    assert "aws.lb.TargetGroup(" in main
    assert "aws.lb.Listener(" in main


def test_pulumi_emits_load_balancers_azure(rvtools_path):
    plan, _ = _plan(rvtools_path, target="azure")
    main = render("pulumi", plan, get_target("azure"))["__main__.py"]
    assert main.count("azure.lb.LoadBalancer(") == len(plan.network.load_balancers)
    assert "azure.lb.BackendAddressPool(" in main
    fronted = {vm for lb in plan.network.load_balancers for vm in lb.targets}
    for c in plan.compute:
        if c.vm_name in fronted and c.subnet_tier.value == "public":
            assert f'pip_{c.resource_name} = azure.network.PublicIp(' not in main


def test_pulumi_emits_load_balancers_gcp(rvtools_path):
    plan, _ = _plan(rvtools_path, target="gcp")
    main = render("pulumi", plan, get_target("gcp"))["__main__.py"]
    has_internal = any(not lb.internet_facing for lb in plan.network.load_balancers)
    has_external = any(lb.internet_facing for lb in plan.network.load_balancers)
    if has_external:
        assert "gcp.compute.TargetPool(" in main
    if has_internal:
        assert "gcp.compute.RegionBackendService(" in main
        assert "gcp.compute.InstanceGroup(" in main
    assert "gcp.compute.ForwardingRule(" in main


def test_terraform_renderer_matches_generator(rvtools_path):
    from iactranslate.generator import build_files

    plan, _ = _plan(rvtools_path)
    assert render("terraform", plan, get_target("aws")) == build_files(plan, get_target("aws"))


@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp"])
def test_terraform_emits_load_balancers(rvtools_path, cloud):
    plan, _ = _plan(rvtools_path, target=cloud)
    files = render("terraform", plan, get_target(cloud))
    assert plan.network.load_balancers, f"fixture should exercise load balancers for {cloud}"
    assert "loadbalancer.tf" in files
    lb_tf = files["loadbalancer.tf"]
    for lb in plan.network.load_balancers:
        assert lb.resource_name in lb_tf


def test_terraform_aws_instances_have_no_individual_eip(rvtools_path):
    # AWS assigns public IPs at the subnet level (map_public_ip_on_launch), not
    # per-instance - confirm compute.tf never declares an aws_eip of its own
    # (the only aws_eip in the project is the NAT gateway's, in networking.tf).
    plan, _ = _plan(rvtools_path, target="aws")
    files = render("terraform", plan, get_target("aws"))
    assert "aws_eip" not in files["compute.tf"]


def test_terraform_azure_skips_individual_public_ip_for_fronted_instances(rvtools_path):
    plan, _ = _plan(rvtools_path, target="azure")
    compute_tf = render("terraform", plan, get_target("azure"))["compute.tf"]
    fronted = {vm for lb in plan.network.load_balancers for vm in lb.targets}
    assert "azurerm_network_interface_backend_address_pool_association" in compute_tf
    for c in plan.compute:
        if c.vm_name in fronted and c.subnet_tier.value == "public":
            assert f'resource "azurerm_public_ip" "{c.resource_name}"' not in compute_tf


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
    for _logical_id, res in resources.items():
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

    for _iid, res in template["Resources"].items():
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


def test_cloudformation_emits_load_balancers(rvtools_path):
    plan, _ = _plan(rvtools_path)
    template = json.loads(render("cloudformation", plan, get_target("aws"))["template.json"])
    types = [res["Type"] for res in template["Resources"].values()]
    assert types.count("AWS::ElasticLoadBalancingV2::LoadBalancer") == len(plan.network.load_balancers)
    assert "AWS::ElasticLoadBalancingV2::TargetGroup" in types
    assert "AWS::ElasticLoadBalancingV2::Listener" in types
    for res in template["Resources"].values():
        if res["Type"] == "AWS::ElasticLoadBalancingV2::TargetGroup":
            for target in res["Properties"]["Targets"]:
                assert target["Id"]["Ref"] in template["Resources"]


def _assert_balanced_braces(text: str) -> None:
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        assert depth >= 0, "unbalanced closing brace"
    assert depth == 0, "unbalanced braces"


def test_bicep_unsupported_targets_raise(rvtools_path):
    from iactranslate.renderers.bicep import RendererNotSupportedError

    for cloud in ("aws", "gcp"):
        plan, _ = _plan(rvtools_path, target=cloud)
        with pytest.raises(RendererNotSupportedError):
            render("bicep", plan, get_target(cloud))


def test_bicep_files_and_structure(rvtools_path):
    plan, _ = _plan(rvtools_path, target="azure")
    files = render("bicep", plan, get_target("azure"))
    assert set(files) == {"main.bicep", "resources.bicep", "README.md"}
    for name in ("main.bicep", "resources.bicep"):
        _assert_balanced_braces(files[name])
        assert files[name].count("[") == files[name].count("]")

    main = files["main.bicep"]
    assert "targetScope = 'subscription'" in main
    assert "Microsoft.Resources/resourceGroups" in main
    assert "module resources 'resources.bicep'" in main


def test_bicep_covers_core_resources(rvtools_path):
    plan, _ = _plan(rvtools_path, target="azure")
    resources = render("bicep", plan, get_target("azure"))["resources.bicep"]
    for res_type in (
        "Microsoft.Network/virtualNetworks@",
        "Microsoft.Network/virtualNetworks/subnets@",
        "Microsoft.Network/networkSecurityGroups@",
        "Microsoft.Network/networkInterfaces@",
        "Microsoft.Compute/virtualMachines@",
    ):
        assert res_type in resources
    assert resources.count("Microsoft.Compute/virtualMachines@") == plan.vm_count
    assert resources.count("Microsoft.Network/networkInterfaces@") == plan.vm_count


def test_bicep_uses_azure_image_reference(rvtools_path):
    plan, _ = _plan(rvtools_path, target="azure")
    resources = render("bicep", plan, get_target("azure"))["resources.bicep"]
    ref = get_target("azure").image_reference(plan.compute[0].image_key)
    assert ref["publisher"] in resources
    assert ref["offer"] in resources
    assert ref["sku"] in resources


def test_bicep_secrets_have_no_insecure_default(rvtools_path):
    plan, _ = _plan(rvtools_path, target="azure")
    resources = render("bicep", plan, get_target("azure"))["resources.bicep"]
    assert "@secure()" in resources
    assert "param adminPassword string = ''" in resources
    assert "REPLACE_ME" not in resources


def test_bicep_is_deterministic(rvtools_path):
    plan, _ = _plan(rvtools_path, target="azure")
    a = render("bicep", plan, get_target("azure"))
    b = render("bicep", plan, get_target("azure"))
    assert a == b


def test_bicep_emits_load_balancers_and_skips_individual_public_ip(rvtools_path):
    plan, _ = _plan(rvtools_path, target="azure")
    resources = render("bicep", plan, get_target("azure"))["resources.bicep"]
    assert resources.count("Microsoft.Network/loadBalancers@") == len(plan.network.load_balancers)
    assert "loadBalancerBackendAddressPools" in resources
    fronted = {vm for lb in plan.network.load_balancers for vm in lb.targets}
    for c in plan.compute:
        if c.vm_name in fronted and c.subnet_tier.value == "public":
            pip_name = f"'{c.resource_name}-pip'"
            assert pip_name not in resources, f"{c.vm_name} should not get its own public IP"


def test_cdk_unsupported_targets_raise(rvtools_path):
    from iactranslate.renderers.cdk import RendererNotSupportedError

    for cloud in ("azure", "gcp"):
        plan, _ = _plan(rvtools_path, target=cloud)
        with pytest.raises(RendererNotSupportedError):
            render("cdk", plan, get_target(cloud))


def test_cdk_files_are_valid_python(rvtools_path):
    plan, _ = _plan(rvtools_path)
    files = render("cdk", plan, get_target("aws"))
    assert set(files) == {"app.py", "stack.py", "requirements.txt", "cdk.json", "README.md"}
    compile(files["app.py"], "app.py", "exec")
    compile(files["stack.py"], "stack.py", "exec")
    json.loads(files["cdk.json"])


def test_cdk_covers_core_constructs(rvtools_path):
    plan, _ = _plan(rvtools_path)
    stack = render("cdk", plan, get_target("aws"))["stack.py"]
    for construct in (
        "ec2.CfnVPC(",
        "ec2.CfnSubnet(",
        "ec2.CfnSecurityGroup(",
        "ec2.CfnInstance(",
    ):
        assert construct in stack
    assert stack.count("ec2.CfnInstance(") == plan.vm_count


def test_cdk_uses_ami_resolution_matching_cloudformation(rvtools_path):
    from iactranslate.renderers.cloudformation import _ami_dynamic_ref, _ami_parameter_name

    plan, _ = _plan(rvtools_path)
    stack = render("cdk", plan, get_target("aws"))["stack.py"]
    image_keys = {c.image_key for c in plan.compute}
    for key in image_keys:
        dyn = _ami_dynamic_ref(key)
        if dyn is not None:
            assert dyn in stack
        else:
            assert _ami_parameter_name(key) in stack
            assert "CfnParameter(" in stack


def test_cdk_is_deterministic(rvtools_path):
    plan, _ = _plan(rvtools_path)
    a = render("cdk", plan, get_target("aws"))
    b = render("cdk", plan, get_target("aws"))
    assert a == b


def test_cdk_emits_load_balancers(rvtools_path):
    plan, _ = _plan(rvtools_path)
    stack = render("cdk", plan, get_target("aws"))["stack.py"]
    assert stack.count("elbv2.CfnLoadBalancer(") == len(plan.network.load_balancers)
    assert "elbv2.CfnTargetGroup(" in stack
    assert "elbv2.CfnListener(" in stack


def _k8s_items(files, name):
    return json.loads(files[name])["items"]


def test_kubernetes_files_are_valid_json(rvtools_path):
    plan, _ = _plan(rvtools_path)
    files = render("kubernetes", plan, get_target("aws"))
    assert set(files) == {
        "namespace.json", "networkpolicies.json", "virtualmachines.json",
        "services.json", "README.md",
    }
    for name in ("namespace.json", "networkpolicies.json", "virtualmachines.json", "services.json"):
        doc = json.loads(files[name])
        assert "apiVersion" in doc and "kind" in doc


def test_kubernetes_works_for_every_cloud(rvtools_path):
    """Unlike CloudFormation/Bicep/CDK, Kubernetes has no target restriction."""
    for cloud in ("aws", "azure", "gcp"):
        plan, _ = _plan(rvtools_path, target=cloud)
        files = render("kubernetes", plan, get_target(cloud))
        assert json.loads(files["virtualmachines.json"])["items"]


def test_kubernetes_one_vm_per_instance(rvtools_path):
    plan, _ = _plan(rvtools_path)
    files = render("kubernetes", plan, get_target("aws"))
    vms = _k8s_items(files, "virtualmachines.json")
    assert len(vms) == plan.vm_count
    for vm in vms:
        assert vm["apiVersion"] == "kubevirt.io/v1"
        assert vm["kind"] == "VirtualMachine"
        assert vm["spec"]["template"]["spec"]["domain"]["cpu"]["cores"] > 0
        assert vm["spec"]["template"]["spec"]["volumes"]
        # every volume must have a matching dataVolumeTemplate
        dv_names = {dv["metadata"]["name"] for dv in vm["spec"]["dataVolumeTemplates"]}
        for vol in vm["spec"]["template"]["spec"]["volumes"]:
            assert vol["dataVolume"]["name"] in dv_names


def test_kubernetes_network_policies_cover_every_security_group(rvtools_path):
    plan, _ = _plan(rvtools_path)
    files = render("kubernetes", plan, get_target("aws"))
    policies = _k8s_items(files, "networkpolicies.json")
    assert len(policies) == len(plan.network.security_groups)
    for pol in policies:
        assert pol["kind"] == "NetworkPolicy"
        assert pol["spec"]["ingress"]


def test_kubernetes_service_type_matches_subnet_tier(rvtools_path):
    plan, _ = _plan(rvtools_path)
    files = render("kubernetes", plan, get_target("aws"))
    svc_by_name = {s["metadata"]["name"]: s for s in _k8s_items(files, "services.json")}
    for c in plan.compute:
        svc = svc_by_name.get(c.resource_name.replace("_", "-"))
        if svc is None:
            continue
        expected = "LoadBalancer" if c.subnet_tier.value == "public" else "ClusterIP"
        assert svc["spec"]["type"] == expected


def test_kubernetes_is_deterministic(rvtools_path):
    plan, _ = _plan(rvtools_path)
    a = render("kubernetes", plan, get_target("aws"))
    b = render("kubernetes", plan, get_target("aws"))
    assert a == b


def test_kubernetes_groups_fronted_instances_into_one_service(rvtools_path):
    """Instances behind a load balancer share one Service (by tier+environment
    label), not one Service each — mirroring the real LB fronting the group."""
    plan, _ = _plan(rvtools_path)
    files = render("kubernetes", plan, get_target("aws"))
    services = _k8s_items(files, "services.json")
    lb_names = {lb.name.replace("_", "-") for lb in plan.network.load_balancers}
    lb_services = [s for s in services if s["metadata"]["name"] in lb_names]
    assert len(lb_services) == len(plan.network.load_balancers)
    for svc in lb_services:
        assert "iactranslate.io/tier" in svc["spec"]["selector"]
        assert "iactranslate.io/environment" in svc["spec"]["selector"]

    fronted = {vm for lb in plan.network.load_balancers for vm in lb.targets}
    per_instance_names = {c.resource_name.replace("_", "-") for c in plan.compute if c.vm_name in fronted}
    service_names = {s["metadata"]["name"] for s in services}
    assert not (per_instance_names & service_names), "fronted instances should not also get their own Service"
