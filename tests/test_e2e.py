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
    from iactranslate.targets import get_target
    from iactranslate.targets.base import CAP_PRICED
    if CAP_PRICED in get_target(target).capabilities:
        assert body["result"]["estimated_monthly_cost_usd"] > 0
    else:
        # An on-premises target has no price an inventory can produce; anything
        # other than exactly zero here would be a fabricated number.
        assert body["result"]["estimated_monthly_cost_usd"] == 0

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


# Every target. This list is the source of truth for "provider-validated" —
# maturity.py derives its claim from it and test_maturity.py checks the two agree.
PROVIDER_VALIDATED_TARGETS = ["aws", "azure", "gcp", "oci", "digitalocean", "nutanix", "proxmox"]


@pytest.mark.skipif(not _ENABLED, reason="set IACTRANSLATE_E2E_TOFU=1 and install tofu/terraform")
@pytest.mark.parametrize("target", PROVIDER_VALIDATED_TARGETS)
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


# --- malformed uploads are the caller's problem, and say so ------------------

def _api_client(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("IACTRANSLATE_WORKSPACE_ROOT", str(tmp_path))
    from iactranslate.api import main as api_main
    importlib.reload(api_main)
    return TestClient(api_main.app), api_main


def _run_bytes(client, name, payload):
    pid = client.post("/v1/projects", json={"name": "malformed"}).json()["id"]
    client.post(f"/v1/projects/{pid}/upload", files={"file": (name, payload)})
    return client.post(f"/v1/projects/{pid}/run")


def test_an_html_page_renamed_xlsx_is_a_400_not_a_500(tmp_path, monkeypatch):
    """`zipfile.BadZipFile` is not a ValueError and used to escape the handler
    as a generic 500. A broken upload is the caller's file, not our server,
    and "internal server error" tells them nothing they can act on."""
    client, api_main = _api_client(tmp_path, monkeypatch)
    try:
        r = _run_bytes(client, "inv.xlsx", b"<html><script>alert(1)</script></html>")
        assert r.status_code == 400, r.text
        assert "not a valid .xlsx" in r.text
    finally:
        import importlib
        monkeypatch.undo()
        importlib.reload(api_main)


def test_error_messages_never_leak_the_server_path(tmp_path, monkeypatch):
    """"No workloads found in /workspaces/iactranslate_x/.plain-abc.csv" told
    an attacker the workspace root, the project directory and the naming scheme
    of the decrypted temporary file. Name the problem, not the path."""
    client, api_main = _api_client(tmp_path, monkeypatch)
    try:
        r = _run_bytes(client, "inv.csv", b"who,what\na,b\n")
        assert r.status_code == 400
        assert "/" not in r.json()["detail"], r.text
        assert ".plain-" not in r.text
        assert str(tmp_path) not in r.text
    finally:
        import importlib
        monkeypatch.undo()
        importlib.reload(api_main)


def test_binary_junk_as_csv_is_a_400_with_a_usable_message(tmp_path, monkeypatch):
    client, api_main = _api_client(tmp_path, monkeypatch)
    try:
        r = _run_bytes(client, "inv.csv", bytes([0, 255, 1, 254, 0x89, 0x50, 0x4E, 0x47]))
        assert r.status_code == 400
        assert "UTF-8" in r.text
    finally:
        import importlib
        monkeypatch.undo()
        importlib.reload(api_main)
