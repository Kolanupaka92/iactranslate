import zipfile

from fastapi.testclient import TestClient

from iactranslate.agents import build_migration_plan
from iactranslate.api.main import app
from iactranslate.generator import build_files
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.pipeline import run_pipeline
from iactranslate.targets import get_target

GCP = get_target("gcp")


def _files(rvtools_path):
    vms = normalize(parse(rvtools_path))
    plan = build_migration_plan(vms, project_name="gcp-gen", target=GCP)
    return build_files(plan, GCP), plan


def test_gcp_renders_google_resources(rvtools_path):
    files, plan = _files(rvtools_path)
    assert 'provider "google"' in files["provider.tf"]
    assert 'resource "google_compute_network" "main"' in files["networking.tf"]
    for subnet in plan.network.subnets:
        assert f'resource "google_compute_subnetwork" "{subnet.resource_name}"' in files["networking.tf"]
    assert "google_compute_firewall" in files["security.tf"]
    assert "google_compute_instance" in files["compute.tf"]
    # web tier gets an external IP
    assert "access_config" in files["compute.tf"]


def test_gcp_names_are_rfc1035(rvtools_path):
    files, _ = _files(rvtools_path)
    # No underscores in google resource *name* arguments (RFC1035).
    for line in files["compute.tf"].splitlines():
        if line.strip().startswith("name "):
            value = line.split("=", 1)[1].strip().strip('"')
            assert "_" not in value and value == value.lower()


def test_gcp_pipeline_e2e(rvtools_path, tmp_path):
    result = run_pipeline(
        input_path=rvtools_path,
        project_name="gcp-e2e",
        out_dir=str(tmp_path / "gcp"),
        target="gcp",
        make_zip=True,
    )
    assert result.plan.target == "gcp"
    assert result.plan.region == "us-central1"
    for c in result.plan.compute:
        assert c.instance_type.startswith(("e2-", "n2-"))
    with zipfile.ZipFile(result.zip_path) as zf:
        assert "compute.tf" in zf.namelist()


def test_api_gcp_flow(rvtools_path):
    client = TestClient(app)
    r = client.post("/projects", json={"name": "api-gcp", "target": "gcp"})
    assert r.status_code == 201
    pid = r.json()["id"]
    with open(rvtools_path, "rb") as f:
        assert client.post(f"/projects/{pid}/upload",
                           files={"file": ("rvtools_sample.xlsx", f)}).status_code == 200
    r = client.post(f"/projects/{pid}/run")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["vm_count"] == 7
    assert client.get(f"/projects/{pid}/download").status_code == 200
