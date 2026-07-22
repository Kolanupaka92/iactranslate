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
        gitops=True,
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
    # Pre-migration assessment shipped alongside the Terraform.
    assert (out / "assessment.json").exists()
    assert (out / "documentation" / "assessment.html").exists()
    # Confidence scoring shipped too.
    assert (out / "confidence.json").exists()
    # Explainability: per-decision reasons joined with confidence.
    assert (out / "decisions.json").exists()
    # Executive report bundled for the client.
    assert (out / "documentation" / "executive-report.html").exists()
    # Architecture diagram (SVG + mermaid doc).
    assert (out / "documentation" / "architecture.svg").exists()
    assert (out / "documentation" / "architecture.md").exists()
    # GitOps workflow bundled when requested.
    assert (out / ".github" / "workflows" / "terraform.yml").exists()
    assert (out / ".gitignore").exists()

    # ZIP contains the terraform files.
    assert result.zip_path and result.zip_path.exists()
    with zipfile.ZipFile(result.zip_path) as zf:
        names = zf.namelist()
    assert "compute.tf" in names
    assert "documentation/migration-summary.md" in names
    assert "assessment.json" in names


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
    conf = body["result"]["confidence"]
    assert 0.0 <= conf["overall"] <= 1.0
    assert conf["level"] in {"high", "medium", "low"}

    r = client.post(f"/projects/{pid}/report?include_recommendation=false")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    assert "Cloud Migration Report" in r.text

    r = client.post(f"/projects/{pid}/assess")
    assert r.status_code == 200, r.text
    assessment = r.json()
    assert assessment["total_workloads"] == 7
    assert 0 <= assessment["readiness"]["score"] <= 100
    assert isinstance(assessment["findings"], list)

    r = client.get(f"/projects/{pid}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert len(r.content) > 0


def test_api_unsupported_target():
    client = TestClient(app)
    r = client.post("/projects", json={"name": "x", "target": "oracle"})
    assert r.status_code == 400
