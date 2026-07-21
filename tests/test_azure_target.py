import zipfile

from fastapi.testclient import TestClient

from iactranslate.agents import build_migration_plan
from iactranslate.api.main import app
from iactranslate.generator import build_files
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.pipeline import run_pipeline
from iactranslate.targets import get_target

AZURE = get_target("azure")


def _files(rvtools_path):
    vms = normalize(parse(rvtools_path))
    plan = build_migration_plan(vms, project_name="az-gen", target=AZURE)
    return build_files(plan, AZURE), plan


def test_azure_renders_azurerm_resources(rvtools_path):
    files, plan = _files(rvtools_path)
    assert 'provider "azurerm"' in files["provider.tf"]
    assert 'resource "azurerm_resource_group" "main"' in files["networking.tf"]
    assert 'resource "azurerm_virtual_network" "main"' in files["networking.tf"]
    for subnet in plan.network.subnets:
        assert f'resource "azurerm_subnet" "{subnet.resource_name}"' in files["networking.tf"]
    # NSGs, not AWS security groups.
    assert "azurerm_network_security_group" in files["security.tf"]
    assert "aws_security_group" not in files["security.tf"]


def test_azure_windows_and_linux_vms(rvtools_path):
    files, _ = _files(rvtools_path)
    compute = files["compute.tf"]
    assert "azurerm_windows_virtual_machine" in compute  # Windows source VMs
    assert "azurerm_linux_virtual_machine" in compute     # Linux source VMs
    assert "azurerm_network_interface" in compute
    assert "azurerm_public_ip" in compute                 # web tier


def test_azure_pipeline_e2e(rvtools_path, tmp_path):
    out = tmp_path / "az"
    result = run_pipeline(
        input_path=rvtools_path,
        project_name="az-e2e",
        out_dir=str(out),
        target="azure",
        make_zip=True,
    )
    assert result.plan.target == "azure"
    assert result.plan.region == "eastus"
    for c in result.plan.compute:
        assert c.instance_type.startswith("Standard_")
    with zipfile.ZipFile(result.zip_path) as zf:
        assert "compute.tf" in zf.namelist()


def test_api_azure_flow(rvtools_path):
    client = TestClient(app)
    r = client.post("/projects", json={"name": "api-az", "target": "azure"})
    assert r.status_code == 201
    pid = r.json()["id"]
    with open(rvtools_path, "rb") as f:
        assert client.post(f"/projects/{pid}/upload",
                           files={"file": ("rvtools_sample.xlsx", f)}).status_code == 200
    r = client.post(f"/projects/{pid}/run")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["vm_count"] == 7
    assert client.get(f"/projects/{pid}/download").status_code == 200
