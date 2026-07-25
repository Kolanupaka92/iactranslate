import zipfile

from fastapi.testclient import TestClient

from iactranslate.agents import build_migration_plan
from iactranslate.api.main import app
from iactranslate.generator import build_files
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.pipeline import run_pipeline
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


def test_oci_shape_config_matches_catalog_vcpu_and_memory(rvtools_path):
    files, plan = _files(rvtools_path)
    compute = files["compute.tf"]
    for c in plan.compute:
        spec = OCI.spec_of(c.instance_type)
        assert spec is not None
        assert f"ocpus         = {spec.vcpu}" in compute
    assert "shape_config {" in compute


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
