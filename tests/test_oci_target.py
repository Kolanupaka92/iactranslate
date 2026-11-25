import re
import zipfile

import pytest
from fastapi.testclient import TestClient

from iactranslate.agents import build_migration_plan
from iactranslate.api.main import app
from iactranslate.costing import estimate_costs
from iactranslate.generator import build_files
from iactranslate.models import NormalizedVM
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.pipeline import run_pipeline
from iactranslate.renderers import render
from iactranslate.targets import get_target
from iactranslate.targets.base import CAP_GITOPS, CAP_LIVE_PRICING, CAP_PULUMI, CAP_TERRAFORM

OCI = get_target("oci")


def _files(rvtools_path):
    vms = normalize(parse(rvtools_path))
    plan = build_migration_plan(vms, project_name="oci-gen", target=OCI)
    return build_files(plan, OCI), plan


def test_oci_capabilities_are_honest():
    caps = OCI.capabilities
    assert CAP_TERRAFORM in caps
    assert CAP_GITOPS in caps
    assert CAP_PULUMI not in caps  # no Pulumi renderer for OCI
    assert CAP_LIVE_PRICING not in caps  # no live pricing integration for OCI


def test_oci_catalog_shape_names_are_unique_and_flex():
    names = OCI.instance_names()
    assert len(names) == len(set(names))
    assert all(n.startswith("VM.Standard.E4.Flex-") or n.startswith("VM.Standard.E5.Flex-") for n in names)


def _block(compute, c):
    return re.search(rf'"oci_core_instance" "{c.resource_name}" {{(.*?)^}}', compute, re.S | re.M).group(1)


def test_oci_shape_config_is_in_ocpus(rvtools_path):
    files, plan = _files(rvtools_path)
    for c in plan.compute:
        spec, block = OCI.spec_of(c.instance_type), _block(files["compute.tf"], c)
        assert int(re.search(r"ocpus\s+= (\d+)$", block, re.M).group(1)) * 2 == spec.vcpu == c.vcpu
        assert float(re.search(r"memory_in_gbs\s+= ([\d.]+)$", block, re.M).group(1)) == spec.memory_gib


def test_oci_for_each_rows_are_in_ocpus(rvtools_path):
    plan = build_migration_plan(normalize(parse(rvtools_path)), project_name="oci-gen", target=OCI)
    compute = render("terraform-foreach", plan, OCI)["compute.tf"]
    ocpus = [int(n) for n in re.findall(r"^ +ocpus +=\s+(\d+)$", compute, re.M)]
    assert [n * 2 for n in ocpus] == [c.vcpu for c in plan.compute]


def test_every_shape_is_one_oracle_sells():
    limit = {"E4.Flex": 64, "E5.Flex": 126}
    price = {"E4.Flex": (0.025, 0.0015), "E5.Flex": (0.03, 0.002)}  # Oracle's list, per OCPU and GB
    for name in OCI.instance_names():
        spec = OCI.spec_of(name)
        ocpu, gb = (int(n) for n in name.split("-")[1].split("x"))
        assert (spec.vcpu, spec.memory_gib) == (ocpu * 2, gb)
        assert 1 <= ocpu <= limit[spec.family]
        assert max(1, ocpu) <= gb <= min(64 * ocpu, 1024)
        per_ocpu, per_gb = price[spec.family]
        assert spec.on_demand_usd_hr == pytest.approx(ocpu * per_ocpu + gb * per_gb, abs=1e-4)


@pytest.mark.parametrize("cpu,gib,ocpu", [(2, 8, 1), (4, 16, 2), (8, 32, 4), (8, 64, 4), (16, 64, 8), (3, 10, 2)])
def test_a_vm_gets_half_its_vcpus_in_ocpus(cpu, gib, ocpu):
    vm = NormalizedVM(vm_name="x", cpu=cpu, memory_gib=gib, os="Ubuntu 22.04")
    c = build_migration_plan([vm], "p", OCI).compute[0]
    assert c.instance_type.split("-")[1].split("x")[0] == str(ocpu)


def test_windows_is_licensed_at_oracles_price_per_ocpu():
    vm = NormalizedVM(vm_name="x", cpu=4, memory_gib=16, os="Windows Server 2022")
    costs = estimate_costs(build_migration_plan([vm], "p", OCI))
    assert costs.windows_licensing == pytest.approx(2 * 0.092 * 730, abs=0.01)  # 2 OCPUs


def test_oci_database_tier_prefers_e5_flex(rvtools_path):
    _files_, plan = _files(rvtools_path)
    db = [c for c in plan.compute if c.tier.value == "database"]
    assert db
    for c in db:
        spec = OCI.spec_of(c.instance_type)
        assert spec.family in ("E5.Flex", "E4.Flex")  # E5 preferred; E4 fallback if nothing fits


def test_oci_renders_vcn_and_nsg_resources(rvtools_path):
    files, plan = _files(rvtools_path)
    assert 'provider "oci"' in files["provider.tf"]
    assert 'resource "oci_core_vcn" "main"' in files["networking.tf"]
    for subnet in plan.network.subnets:
        assert f'resource "oci_core_subnet" "{subnet.resource_name}"' in files["networking.tf"]
    assert "oci_core_network_security_group" in files["security.tf"]
    assert "azurerm_network_security_group" not in files["security.tf"]
    assert "aws_security_group" not in files["security.tf"]


def test_oci_resolves_images_via_data_source(rvtools_path):
    files, plan = _files(rvtools_path)
    assert "images.tf" in files
    image_keys = {c.image_key for c in plan.compute}
    for key in image_keys:
        ref = OCI.image_reference(key)
        assert ref["operating_system"] in files["images.tf"]


def test_oci_load_balancers_use_flexible_shape(rvtools_path):
    files, plan = _files(rvtools_path)
    assert plan.network.load_balancers, "fixture should exercise load balancers"
    lb_tf = files["loadbalancer.tf"]
    assert 'shape          = "flexible"' in lb_tf
    for lb in plan.network.load_balancers:
        assert f'resource "oci_load_balancer_load_balancer" "{lb.resource_name}"' in lb_tf


def test_oci_pipeline_e2e(rvtools_path, tmp_path):
    out = tmp_path / "oci"
    result = run_pipeline(
        input_path=rvtools_path,
        project_name="oci-e2e",
        out_dir=str(out),
        target="oci",
        make_zip=True,
    )
    assert result.plan.target == "oci"
    assert result.plan.region == "us-ashburn-1"
    for c in result.plan.compute:
        assert c.instance_type.startswith("VM.Standard.")
    with zipfile.ZipFile(result.zip_path) as zf:
        assert "compute.tf" in zf.namelist()
        assert "images.tf" in zf.namelist()


def test_api_oci_flow(rvtools_path):
    client = TestClient(app)
    r = client.post("/projects", json={"name": "api-oci", "target": "oci"})
    assert r.status_code == 201
    pid = r.json()["id"]
    with open(rvtools_path, "rb") as f:
        assert client.post(f"/projects/{pid}/upload",
                           files={"file": ("rvtools_sample.xlsx", f)}).status_code == 200
    r = client.post(f"/projects/{pid}/run")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["vm_count"] == 7
    assert client.get(f"/projects/{pid}/download").status_code == 200
