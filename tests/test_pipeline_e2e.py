import zipfile

from fastapi.testclient import TestClient

from iactranslate.api.main import app
from iactranslate.pipeline import run_pipeline
from iactranslate.targets import get_target
from iactranslate.validation import validate_plan


def test_pipeline_produces_valid_project(rvtools_path, tmp_path):
    out = tmp_path / "proj"
    result = run_pipeline(
        input_path=rvtools_path,
        project_name="e2e",
        out_dir=str(out),
        make_zip=True,
    )

    # Plan is valid and every instance type is real.
    aws = get_target("aws")
    assert validate_plan(result.plan, aws) == []
    for c in result.plan.compute:
        assert aws.instance_exists(c.instance_type)

    # Full project tree written.
    for name in ("main.tf", "compute.tf", "networking.tf", "README.md"):
        assert (out / name).exists()
    assert (out / "documentation" / "migration-summary.md").exists()

    # ZIP contains the terraform files.
    assert result.zip_path and result.zip_path.exists()
    with zipfile.ZipFile(result.zip_path) as zf:
        names = zf.namelist()
    assert "compute.tf" in names
    assert "documentation/migration-summary.md" in names


def test_api_full_flow(rvtools_path, tmp_path):
    client = TestClient(app)

    r = client.post("/projects", json={"name": "api-e2e", "target": "aws"})
    assert r.status_code == 201
    pid = r.json()["id"]

    with open(rvtools_path, "rb") as f:
        r = client.post(
            f"/projects/{pid}/upload",
            files={"file": ("rvtools_sample.xlsx", f, "application/vnd.ms-excel")},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "uploaded"

    r = client.post(f"/projects/{pid}/run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["result"]["vm_count"] == 7

    r = client.get(f"/projects/{pid}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert len(r.content) > 0


def test_api_unsupported_target():
    client = TestClient(app)
    r = client.post("/projects", json={"name": "x", "target": "oracle"})
    assert r.status_code == 400
