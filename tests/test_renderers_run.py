"""Pulumi, CDK and Bicep output run in their own tools, not just read as text.

The Azure and Google Pulumi programs could not run with a load balancer in them,
and nothing noticed: every test read them as text (ADR 0090, 0093). Needs the
tools, so it runs in its own CI job: IACTRANSLATE_RUN_RENDERERS=1.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from iactranslate.cli import main as cli

RUN = os.getenv("IACTRANSLATE_RUN_RENDERERS") == "1"
needs_tools = pytest.mark.skipif(not RUN, reason="set IACTRANSLATE_RUN_RENDERERS=1 with the tools installed")
PYTHON = os.getenv("IACTRANSLATE_RENDERER_PYTHON") or sys.executable
HARNESS = Path(__file__).parent / "support" / "pulumi_mock_run.py"
SOURCES = ["tests/fixtures/rvtools_realistic.xlsx", "tests/fixtures/cmdb_sample.csv", "tests/fixtures/cloud_sample.csv"]


def _generate(tmp_path, target, renderer, source):
    out = tmp_path / "out"
    assert cli(["translate", source, "--target", target, "--renderer", renderer, "--name", "acme",
                "--out", str(out)]) == 0
    return out


@needs_tools
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("target", ["aws", "azure", "gcp"])
def test_pulumi_programs_run(tmp_path, target, source):
    out = _generate(tmp_path, target, "pulumi", source)
    result = subprocess.run([PYTHON, str(HARNESS), str(out / "__main__.py")], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.rstrip().endswith("OK"), result.stderr[-3000:]
    counts = {kind: int(n) for kind, n in (line.rsplit(" ", 1) for line in result.stdout.splitlines()[:-1])}
    program = (out / "__main__.py").read_text()
    for kind, call in {"aws": [("aws:ec2/instance:Instance", "aws.ec2.Instance("),
                               ("aws:ebs/volume:Volume", "aws.ebs.Volume(")],
                       "azure": [("azure:compute/managedDisk:ManagedDisk", "azure.compute.ManagedDisk(")],
                       "gcp": [("gcp:compute/instance:Instance", "gcp.compute.Instance("),
                               ("gcp:compute/disk:Disk", "gcp.compute.Disk(")]}[target]:
        assert counts.get(kind, 0) == program.count(call)


@needs_tools
@pytest.mark.skipif(shutil.which("node") is None, reason="CDK needs Node")
@pytest.mark.parametrize("source", SOURCES)
def test_cdk_app_synthesizes(tmp_path, source):
    out = _generate(tmp_path, "aws", "cdk", source)
    result = subprocess.run([PYTHON, "app.py"], cwd=out, capture_output=True, text=True,
                            env={**os.environ, "CDK_OUTDIR": str(out / "cdk.out")})
    assert result.returncode == 0, result.stderr[-3000:]
    template = json.loads(next((out / "cdk.out").glob("*.template.json")).read_text())
    servers = [r for r in template["Resources"].values() if r["Type"] == "AWS::EC2::Instance"]
    assert servers and all(r["UpdateReplacePolicy"] == "Retain" for r in servers)
    for server in servers:
        disks = [d["Ebs"] for d in server["Properties"]["BlockDeviceMappings"]]
        assert all(d["Encrypted"] for d in disks) and all(d["DeleteOnTermination"] is False for d in disks[1:])


@needs_tools
@pytest.mark.skipif(shutil.which("bicep") is None, reason="needs the bicep CLI")
@pytest.mark.parametrize("source", SOURCES)
def test_bicep_builds(tmp_path, source):
    out = _generate(tmp_path, "azure", "bicep", source)
    for name in ("main.bicep", "resources.bicep"):
        result = subprocess.run(["bicep", "build", str(out / name), "--stdout"], capture_output=True, text=True)
        assert result.returncode == 0, (name, result.stderr[-3000:])
        assert "Error" not in result.stderr, (name, result.stderr[-3000:])
