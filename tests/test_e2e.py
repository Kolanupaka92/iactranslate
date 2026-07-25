"""End-to-end tests: drive the whole product through the real API across the
full source x target matrix, and (opt-in) validate the generated Terraform with
OpenTofu/Terraform against the real cloud providers.

The matrix test runs everywhere (in-process, no network). The tofu-validate test
runs only when a `tofu`/`terraform` binary is present AND IACTRANSLATE_E2E_TOFU=1,
so the default suite stays fast and offline; it downloads real providers.
"""
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iactranslate.api.main import app
from iactranslate.pipeline import run_pipeline
from iactranslate.targets import list_targets

client = TestClient(app, raise_server_exceptions=False)

# Which fixture feeds each source (all encode the same 7-workload estate).
SOURCE_FIXTURE = {
    "vmware": "rvtools_sample.xlsx",
    "hyperv": "hyperv_sample.csv",
    "generic": "cmdb_sample.csv",
    "cloud": "cloud_sample.csv",
}
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MATRIX = [(s, t) for s in SOURCE_FIXTURE for t in list_targets()]


@pytest.mark.parametrize("source,target", MATRIX)
def test_api_source_target_matrix(source, target):
    """create -> upload -> run -> download, for every source x cloud pair."""
    pid = client.post(
        "/projects", json={"name": f"e2e-{source}-{target}", "target": target, "source": source}
    ).json()["id"]

    fixture = FIXTURES / SOURCE_FIXTURE[source]
    with open(fixture, "rb") as f:
        up = client.post(f"/projects/{pid}/upload", files={"file": (fixture.name, f)})
    assert up.status_code == 200, up.text

    run = client.post(f"/projects/{pid}/run")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "completed"
    assert body["source"] == source
    assert body["result"]["vm_count"] == 7
    assert body["result"]["estimated_monthly_cost_usd"] > 0

    dl = client.get(f"/projects/{pid}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/zip"
    # ZIP is a real terraform project.
    zpath = FIXTURES.parent / f"_e2e_{source}_{target}.zip"
    zpath.write_bytes(dl.content)
    try:
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
        for expected in ("main.tf", "compute.tf", "networking.tf", "variables.tf",
                         "documentation/migration-summary.md"):
            assert expected in names
    finally:
        zpath.unlink(missing_ok=True)

    client.delete(f"/projects/{pid}")


@pytest.mark.parametrize("source", list(SOURCE_FIXTURE))
def test_api_recommend_every_source(source):
    pid = client.post("/projects", json={"name": f"rec-{source}", "source": source}).json()["id"]
    fixture = FIXTURES / SOURCE_FIXTURE[source]
    with open(fixture, "rb") as f:
        client.post(f"/projects/{pid}/upload", files={"file": (fixture.name, f)})
    r = client.post(f"/projects/{pid}/recommend")
    assert r.status_code == 200, r.text
    assert r.json()["recommended"] in list_targets()
    client.delete(f"/projects/{pid}")


# --------------------------------------------------------------------------- #
# Opt-in: validate generated Terraform against the real providers.
# --------------------------------------------------------------------------- #

_TOFU = shutil.which("tofu") or shutil.which("terraform")
_ENABLED = os.getenv("IACTRANSLATE_E2E_TOFU") == "1" and _TOFU is not None
_CACHE = Path(os.getenv("TF_PLUGIN_CACHE_DIR", "/tmp/iactranslate_tf_plugin_cache"))


@pytest.mark.skipif(not _ENABLED, reason="set IACTRANSLATE_E2E_TOFU=1 and install tofu/terraform")
@pytest.mark.parametrize("target", ["aws", "azure", "gcp", "oci", "digitalocean"])
def test_generated_terraform_validates(target, rvtools_path, tmp_path):
    out = tmp_path / target
    run_pipeline(input_path=rvtools_path, project_name="tofu", out_dir=str(out), target=target)

    _CACHE.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TF_PLUGIN_CACHE_DIR": str(_CACHE), "TF_IN_AUTOMATION": "1"}
    init = subprocess.run(
        [_TOFU, "init", "-backend=false", "-no-color", "-input=false"],
        cwd=out, env=env, capture_output=True, text=True, timeout=300,
    )
    assert init.returncode == 0, init.stderr
    val = subprocess.run(
        [_TOFU, "validate", "-no-color"],
        cwd=out, env=env, capture_output=True, text=True, timeout=120,
    )
    assert val.returncode == 0, val.stdout + val.stderr
    assert "configuration is valid" in val.stdout.lower()
