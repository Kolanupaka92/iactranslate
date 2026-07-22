"""Utilization-based right-sizing: size to observed usage, not raw allocation."""
from iactranslate.models import NormalizedVM
from iactranslate.normalize import normalize
from iactranslate.pipeline import run_pipeline
from iactranslate.sizing import effective_demand
from iactranslate.sources import resolve_source


def test_effective_demand_shrinks_with_utilization():
    alloc = NormalizedVM(vm_name="x", cpu=16, memory_gib=64)
    used = NormalizedVM(vm_name="x", cpu=16, memory_gib=64, cpu_util_pct=15, mem_util_pct=25)

    d_alloc = effective_demand(alloc)
    d_used = effective_demand(used)
    assert d_alloc.right_sized is False
    assert d_alloc.vcpu == 16 and d_alloc.memory_gib == 64  # unchanged
    assert d_used.right_sized is True
    assert d_used.vcpu < 16 and d_used.memory_gib < 64      # sized to demand


def test_generic_source_parses_utilization(cmdb_util_path):
    vms = {v.vm_name: v for v in normalize(resolve_source(cmdb_util_path).parse(cmdb_util_path))}
    db = vms["prod-db-01"]
    assert db.cpu_util_pct == 15.0 and db.mem_util_pct == 35.0
    assert db.cpu == 16 and db.memory_gib == 64.0            # allocation still read


def test_memory_used_gb_derives_utilization(tmp_path):
    import pandas as pd
    p = tmp_path / "used.csv"
    pd.DataFrame([{"Host": "h1", "Cores": 8, "RAM GB": 32, "Memory Used": 8}]).to_csv(p, index=False)
    vms = normalize(resolve_source(str(p)).parse(str(p)))
    assert vms[0].mem_util_pct == 25.0                       # 8 / 32 = 25%


def test_pipeline_rightsizes_and_is_cheaper(cmdb_util_path, cmdb_path, tmp_path):
    util = run_pipeline(input_path=cmdb_util_path, project_name="u", out_dir=str(tmp_path / "u"), target="aws")
    alloc = run_pipeline(input_path=cmdb_path, project_name="a", out_dir=str(tmp_path / "a"), target="aws")

    assert all(c.right_sized for c in util.plan.compute)
    assert not any(c.right_sized for c in alloc.plan.compute)
    # Sizing to low utilization is materially cheaper than translating allocation.
    assert util.plan.total_estimated_monthly_cost_usd < alloc.plan.total_estimated_monthly_cost_usd * 0.6
    db = next(c for c in util.plan.compute if c.vm_name == "prod-db-01")
    assert db.source_vcpu == 16 and db.vcpu < 16            # shrank, and records the before


def test_allocation_path_unchanged(cmdb_path, tmp_path):
    # No utilization columns -> identical to prior behavior (regression guard).
    r = run_pipeline(input_path=cmdb_path, project_name="a", out_dir=str(tmp_path / "a"), target="aws")
    assert r.plan.total_estimated_monthly_cost_usd == 3217.83
