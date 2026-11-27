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


@pytest.mark.parametrize("renderer", ["cloudformation", "cdk"])
def test_machines_whose_names_differ_only_in_punctuation_are_all_kept(renderer):
    names = ["uat cache 01", "uat-cache-01", "UAT_CACHE_01", "uat.cache.01", "uatcache01"]
    files = _files("aws", renderer, *(_vm(n) for n in names))
    if renderer == "cloudformation":
        template = json.loads(next(c for n, c in files.items() if n.endswith(".json")))
        servers = [k for k, r in template["Resources"].items() if r["Type"] == "AWS::EC2::Instance"]
        assert servers[0] == "InstanceUatCache01"
    else:
        code = _code(files)
        servers = re.findall(r"ec2\.CfnInstance\(\s*self, '(\w+)'", code)
        assert len(set(re.findall(r"^\s*(inst_\w+) = ec2\.CfnInstance\(", code, re.M))) == len(names)
    assert len(set(servers)) == len(servers) == len(names)


@pytest.mark.parametrize("renderer", ["pulumi", "cloudformation", "cdk"])
def test_every_aws_disk_is_encrypted_and_data_outlives_its_server(renderer):
    files = _files("aws", renderer, _vm("db01", disks=(60, *SIZES)))
    code = _code(files)
    if renderer == "pulumi":
        assert re.search(r'root_block_device=\{[^}]*"encrypted": True', code)
        assert code.count("encrypted=True") == len(SIZES)  # separate volumes, never deleted with it
    elif renderer == "cloudformation":
        template = json.loads(next(c for n, c in files.items() if n.endswith(".json")))
        (server,) = [r for r in template["Resources"].values() if r["Type"] == "AWS::EC2::Instance"]
        disks = [d["Ebs"] for d in server["Properties"]["BlockDeviceMappings"]]
        assert all(d["Encrypted"] is True for d in disks) and len(disks) == 1 + len(SIZES)
        assert all(d["DeleteOnTermination"] is False for d in disks[1:])
    else:
        assert code.count("encrypted=True") == 1 + len(SIZES)
        assert code.count("delete_on_termination=False") == len(SIZES)


def test_suse_on_oracle_gets_an_image_that_exists_and_says_so():
    c = build_migration_plan([_vm("s", "SUSE Linux Enterprise Server 15 SP5")], "x", get_target("oci")).compute[0]
    assert c.image_key == "oracle-linux-9" and "OS substituted" in c.reason
    images = _files("oci", "terraform", _vm("s", "SUSE Linux Enterprise Server 15 SP5"))["images.tf"]
    assert "SUSE" not in images and 'operating_system         = "Oracle Linux"' in images


def _web():
    return [_vm("prod-web-01"), _vm("prod-web-02")]


@pytest.mark.parametrize("layout", ["terraform", "terraform-foreach"])
def test_terraform_creates_https_only_with_a_certificate(layout):
    files = _files("aws", layout, *_web())
    lb = files["loadbalancer.tf"]
    https = lb.split('"aws_lb_listener" "production_web_lb_443" {')[1].split("\n}")[0]
    assert 'count = var.acm_certificate_arn == "" ? 0 : 1' in https
    assert "ELBSecurityPolicy-TLS13-1-2-2021-06" in https and "2016-08" not in lb
    assert "to   = aws_lb_listener.production_web_lb_443[0]" in lb
    assert 'check "https_certificate"' in lb and "acm_certificate_arn" in files["README.md"]
    http = lb.split('"aws_lb_listener" "production_web_lb_80" {')[1].split("\n}")[0]
    assert "count" not in http


def test_no_certificate_check_without_https():
    files = _files("aws", "terraform", _vm("prod-app-01"), _vm("prod-app-02"))
    assert "https_certificate" not in files["loadbalancer.tf"]


def test_pulumi_creates_https_only_with_a_certificate():
    program = _files("aws", "pulumi", *_web())["__main__.py"]
    compile(program, "__main__.py", "exec")
    block = program.split("if acm_certificate_arn:\n")[1].split("\nelse:")[0]
    assert "port=443" in block and "TLS13" in block and "pulumi.log.warn(" in program


@pytest.mark.parametrize("renderer", ["cloudformation", "cdk"])
def test_cloudformation_and_cdk_create_https_only_with_a_certificate(renderer):
    files = _files("aws", renderer, *_web())
    if renderer == "cloudformation":
        t = json.loads(next(c for n, c in files.items() if n.endswith(".json")))
        assert t["Parameters"]["AcmCertificateArn"]["Default"] == ""
        https = [r for r in t["Resources"].values()
                 if r["Type"] == "AWS::ElasticLoadBalancingV2::Listener" and r["Properties"]["Protocol"] == "HTTPS"]
        assert https and all(r["Condition"] == "HasCertificate" for r in https)
        assert "HasCertificate" in t["Conditions"]
        http = [r for r in t["Resources"].values()
                if r["Type"] == "AWS::ElasticLoadBalancingV2::Listener" and r["Properties"]["Protocol"] == "HTTP"]
        assert http and all("Condition" not in r for r in http)
    else:
        code = _code(files)
        assert code.count("CfnCondition(self, 'HasCertificate'") == 1
        assert code.count(".cfn_options.condition = has_cert") == 1


def _estate():
    # Two web machines behind a load balancer, one public on its own, one private.
    return [_vm("prod-web-01"), _vm("prod-web-02"), _vm("dev-web-01"), _vm("prod-app-01")]


@pytest.mark.parametrize("layout", ["terraform", "terraform-foreach"])
def test_aws_gives_a_public_address_only_to_an_unfronted_public_machine(layout):
    files = _files("aws", layout, *_estate())
    assert "map_public_ip_on_launch = false" in files["networking.tf"]
    assert "map_public_ip_on_launch = true" not in files["networking.tf"]
    compute = files["compute.tf"]
    if layout == "terraform":
        def public(vm):
            block = compute.split(f'"aws_instance" "{vm}" {{')[1].split("\n}")[0]
            return re.search(r"associate_public_ip_address = (\w+)", block).group(1)
    else:
        def public(vm):
            return re.search(rf'"{vm}" = \{{[^}}]*?public_ip\s+= (\w+)', compute, re.S).group(1)
    assert [public(v) for v in ("prod_web_01", "prod_web_02", "dev_web_01", "prod_app_01")] == \
        ["false", "false", "true", "false"]


@pytest.mark.parametrize("renderer", ["pulumi", "cloudformation", "cdk"])
def test_every_aws_renderer_follows_the_same_rule(renderer):
    files = _files("aws", renderer, *_estate())
    code = _code(files)
    if renderer == "pulumi":
        assert "map_public_ip_on_launch=False" in code and "map_public_ip_on_launch=True" not in code
        assert sorted(re.findall(r"associate_public_ip_address=(\w+)", code)) == ["False", "False", "False", "True"]
    elif renderer == "cloudformation":
        t = json.loads(next(c for n, c in files.items() if n.endswith(".json")))
        subnets = [r for r in t["Resources"].values() if r["Type"] == "AWS::EC2::Subnet"]
        assert all(s["Properties"]["MapPublicIpOnLaunch"] is False for s in subnets)
        public = {r["Properties"]["Tags"][0]["Value"] for r in t["Resources"].values()
                  if r["Type"] == "AWS::EC2::Instance" and "NetworkInterfaces" in r["Properties"]}
        assert public == {"dev-web-01"}
    else:
        assert "map_public_ip_on_launch=False" in code and code.count("associate_public_ip_address=True") == 1


def test_aws_puts_machines_behind_a_public_load_balancer_in_private_subnets():
    from iactranslate.graph import assign_subnets
    plan = build_migration_plan([_vm("prod-web-01"), _vm("prod-web-02"), _vm("dev-web-01")], "x", get_target("aws"))
    subnet_of = assign_subnets(plan)
    assert {subnet_of["prod-web-01"], subnet_of["prod-web-02"]} == {"private_0", "private_1"}  # still across zones
    assert subnet_of["dev-web-01"].startswith("public")  # on its own: public, with its own address
    assert plan.network.nat_gateway  # their way out
    lb = _files("aws", "terraform", _vm("prod-web-01"), _vm("prod-web-02"))["loadbalancer.tf"]
    assert re.search(r"subnets = \[\s*aws_subnet\.public_0\.id,\s*aws_subnet\.public_1\.id,", lb)
    other = build_migration_plan([_vm("prod-web-01"), _vm("prod-web-02")], "x", get_target("azure"))
    assert all(s.startswith("public") for s in assign_subnets(other).values())  # other clouds unchanged


@pytest.mark.parametrize("os,device", [("Ubuntu 22.04", "/dev/sda1"), ("Windows Server 2022", "/dev/sda1"),
                                       ("Red Hat Enterprise Linux 9", "/dev/sda1"), ("SUSE 15", "/dev/sda1"),
                                       ("CentOS 7", "/dev/sda1"), (None, "/dev/xvda")])
@pytest.mark.parametrize("renderer", ["cloudformation", "cdk"])
def test_the_boot_disk_is_mapped_where_the_image_boots(renderer, os, device):  # else it's an extra disk
    files = _files("aws", renderer, _vm("db01", os, (60, 10)))
    if renderer == "cloudformation":
        t = json.loads(next(c for n, c in files.items() if n.endswith(".json")))
        (server,) = [r for r in t["Resources"].values() if r["Type"] == "AWS::EC2::Instance"]
        disks = server["Properties"]["BlockDeviceMappings"]
        assert disks[0]["DeviceName"] == device and disks[0]["Ebs"]["VolumeSize"] == 60
        assert [d["DeviceName"] for d in disks[1:]] == ["/dev/xvdf"]
    else:
        assert f"device_name='{device}'" in _code(files)


def test_centos_on_aws_gets_rocky_linux_and_says_so():
    c = build_migration_plan([_vm("c", "CentOS Linux 7 (64-bit)")], "x", get_target("aws")).compute[0]
    assert c.image_key == "rocky-9" and "OS substituted" in c.reason
    images = _files("aws", "terraform", _vm("c", "CentOS Linux 7 (64-bit)"))["images.tf"]
    assert "Rocky-9-EC2-Base-9.*x86_64" in images and "CentOS Linux 7" not in images


def test_suse_on_aws_takes_the_standard_image():
    import fnmatch

    from iactranslate.targets.aws.mapping import AMI_FILTERS
    pattern = AMI_FILTERS["sles-15"]["name"]
    assert fnmatch.fnmatch("suse-sles-15-sp6-v20261116-hvm-ssd-x86_64", pattern)
    for other in ("suse-sles-15-sp7-chost-byos-v20261126-hvm-ssd-x86_64",
                  "suse-sles-15-sp5-v20261116-ecs-hvm-ssd-x86_64"):
        assert not fnmatch.fnmatch(other, pattern)


def test_pulumi_aws_load_balancer_names_are_ones_aws_accepts():
    program = _files("aws", "pulumi", _vm("prod-web-01"), _vm("prod-web-02"), _vm("prod-app-01"),
                     _vm("prod-app-02"))["__main__.py"]
    for kind in ("aws.lb.LoadBalancer(", "aws.lb.TargetGroup("):
        names = re.findall(r'\n    name="([^"]+)",', "".join(b.split("\n)")[0] for b in program.split(kind)[1:]))
        assert names and all(re.fullmatch(r"[A-Za-z0-9-]{1,32}", n) for n in names), (kind, names)
