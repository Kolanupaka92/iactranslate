"""Explainability — every instance decision carries a human-readable 'why'."""
import json

from iactranslate.agents import build_migration_plan
from iactranslate.models import NormalizedVM
from iactranslate.normalize import normalize
from iactranslate.pipeline import run_pipeline
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


def test_every_compute_has_a_reason(rvtools_path):
    vms = normalize(resolve_source(rvtools_path).parse(rvtools_path))
    plan = build_migration_plan(vms, "x", get_target("aws"))
    assert all(c.reason for c in plan.compute)


def test_reason_reflects_utilization_basis():
    used = NormalizedVM(vm_name="u", cpu=16, memory_gib=64, os="Ubuntu 22.04",
                        cpu_util_pct=15, mem_util_pct=25)
    alloc = NormalizedVM(vm_name="a", cpu=16, memory_gib=64, os="Ubuntu 22.04")
    plan = build_migration_plan([used, alloc], "x", get_target("aws"))
    by = {c.vm_name: c for c in plan.compute}
    assert "utilization" in by["u"].reason and "15% CPU" in by["u"].reason
    assert "allocation" in by["a"].reason
    # The reason names the chosen instance and the tier.
    assert by["u"].instance_type in by["u"].reason
    assert "tier" in by["u"].reason


def test_decisions_json_joins_reason_and_confidence(rvtools_path, tmp_path):
    r = run_pipeline(input_path=rvtools_path, project_name="x",
                     out_dir=str(tmp_path / "x"), target="aws")
    path = r.project_dir / "decisions.json"
    assert path.exists()
    decisions = json.loads(path.read_text())["decisions"]
    assert len(decisions) == r.plan.vm_count
    for d in decisions:
        assert d["reason"]
        assert d["instance_type"]
        assert d["confidence"]["level"] in {"high", "medium", "low"}
        assert 0.0 <= d["confidence"]["overall"] <= 1.0


def test_reason_in_migration_summary(rvtools_path, tmp_path):
    r = run_pipeline(input_path=rvtools_path, project_name="x",
                     out_dir=str(tmp_path / "x"), target="aws")
    summary = (r.project_dir / "documentation" / "migration-summary.md").read_text()
    assert "Why these instances" in summary
    # Each workload appears with its reason.
    assert r.plan.compute[0].vm_name in summary
