"""Utilization-based right-sizing: size to observed usage, not raw allocation."""
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.config import TARGET_UTILIZATION
from iactranslate.models import NormalizedVM
from iactranslate.normalize import normalize
from iactranslate.pipeline import run_pipeline
from iactranslate.sizing import Demand, effective_demand, fit
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target, list_targets
from iactranslate.targets.base import InstanceSpec, smallest_fit


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
    assert util.plan.total_estimated_monthly_cost_usd < alloc.plan.total_estimated_monthly_cost_usd * 0.7
    db = next(c for c in util.plan.compute if c.vm_name == "prod-db-01")
    assert db.source_vcpu == 16 and db.vcpu < 16            # shrank, and records the before


def test_allocation_path_unchanged(cmdb_path, tmp_path):
    # No utilization columns -> sized to the allocation, nothing added (ADR 0087).
    r = run_pipeline(input_path=cmdb_path, project_name="a", out_dir=str(tmp_path / "a"), target="aws")
    assert r.plan.total_estimated_monthly_cost_usd == 1490.36


# --- no usage data, nothing added (ADR 0087) ----------------------------------------------


@pytest.mark.parametrize("target", list_targets())
def test_a_machine_that_is_exactly_a_catalog_size_stays_that_size(target):
    t = get_target(target)
    for name in t.instance_names():
        spec = t.spec_of(name)
        vm = NormalizedVM(vm_name="x", cpu=spec.vcpu, memory_gib=spec.memory_gib, os="Ubuntu 22.04")
        c = build_migration_plan([vm], "p", t).compute[0]
        assert not c.undersized
        assert t.spec_of(c.instance_type).on_demand_usd_hr <= spec.on_demand_usd_hr


@pytest.mark.parametrize("cpu_pct,mem_pct", [(None, None), (40, None), (None, 40), (40, 40)])
def test_each_dimension_has_headroom_only_if_it_was_measured(cpu_pct, mem_pct):
    vm = NormalizedVM(vm_name="x", cpu=8, memory_gib=32, cpu_util_pct=cpu_pct, mem_util_pct=mem_pct)
    d = effective_demand(vm)
    h = 1 / TARGET_UTILIZATION
    assert d.target_vcpu == pytest.approx(8 if cpu_pct is None else 8 * 0.4 * h)
    assert d.target_memory_gib == pytest.approx(32 if mem_pct is None else 32 * 0.4 * h)
    assert d.right_sized is (cpu_pct is not None or mem_pct is not None)


@pytest.mark.parametrize("target", list_targets())
def test_cpu_usage_never_raises_the_memory(target):  # it used to add 54% to it
    t = get_target(target)
    plain = NormalizedVM(vm_name="x", cpu=8, memory_gib=32, os="Ubuntu 22.04")
    measured = plain.model_copy(update={"cpu_util_pct": 30.0})
    a, b = (build_migration_plan([v], "p", t).compute[0] for v in (plain, measured))
    assert b.memory_gib >= 32
    assert b.estimated_monthly_cost_usd <= a.estimated_monthly_cost_usd


def test_the_evidence_says_what_was_added_to_each():
    def assumptions(**kw):
        vm = NormalizedVM(vm_name="x", cpu=8, memory_gib=32, os="Ubuntu 22.04", **kw)
        return build_migration_plan([vm], "p", get_target("aws")).compute[0].decision.assumptions
    added = f"{round((1 / TARGET_UTILIZATION - 1) * 100)}% headroom above measured"
    assert "CPU is the source allocation, with nothing added" in assumptions()
    assert not any("headroom" in a for a in assumptions())
    mixed = assumptions(cpu_util_pct=40)
    assert f"{added} CPU" in mixed and "memory is the source allocation, with nothing added" in mixed


class _Catalog:
    def __init__(self, *specs):
        self._specs = list(specs)

    def smallest_fit(self, vcpu, memory_gib, steady_cpu=False):
        return smallest_fit(self._specs, vcpu, memory_gib, steady_cpu)


def test_measured_headroom_gives_way_before_the_demand_does():
    demand = Demand(10, 64, 1.5, 1.5, right_sized=True)
    small, large = InstanceSpec("s", 4, 16, "f", 1), InstanceSpec("l", 10, 64, "f", 5)
    assert fit(_Catalog(small, large), demand).name == "l"
    shared = InstanceSpec("shared", 10, 64, "f", 1, shared_cpu=True)
    assert fit(_Catalog(shared, large), demand, steady_cpu=True).name == "l"
