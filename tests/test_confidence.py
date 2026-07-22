"""Confidence Engine: deterministic per-decision certainty scoring."""
from iactranslate.agents import build_migration_plan
from iactranslate.confidence import score_plan
from iactranslate.models import ComputePlan, Environment, MigrationPlan, NetworkPlan, NormalizedVM, Tier
from iactranslate.normalize import normalize
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


def _plan(compute):
    return MigrationPlan(
        project_name="c", target="aws", region="us-east-1",
        network=NetworkPlan(), compute=compute,
    )


def _cp(name, **kw):
    base = dict(
        vm_name=name, resource_name=name, instance_type="t3.medium", image_key="ubuntu-22.04",
        vcpu=2, memory_gib=4, root_volume_gib=30, tier=Tier.WEB, environment=Environment.PRODUCTION,
        price_source="static", right_sized=False,
    )
    base.update(kw)
    return ComputePlan(**base)


def test_utilization_raises_sizing_confidence():
    alloc = _plan([_cp("a", right_sized=False)])
    used = _plan([_cp("a", right_sized=True)])
    vm = NormalizedVM(vm_name="a", cpu=2, memory_gib=4, os="Ubuntu 22.04")
    ca = score_plan(alloc, [vm])
    cu = score_plan(used, [vm])
    assert cu.overall > ca.overall
    assert cu.factor_averages["sizing"] > ca.factor_averages["sizing"]


def test_missing_os_lowers_image_confidence():
    plan = _plan([_cp("a")])
    known = score_plan(plan, [NormalizedVM(vm_name="a", cpu=2, memory_gib=4, os="Ubuntu 22.04")])
    blank = score_plan(plan, [NormalizedVM(vm_name="a", cpu=2, memory_gib=4)])
    assert known.factor_averages["image"] > blank.factor_averages["image"]


def test_other_tier_lowers_classification():
    strong = score_plan(_plan([_cp("a", tier=Tier.DATABASE, environment=Environment.PRODUCTION)]))
    weak = score_plan(_plan([_cp("a", tier=Tier.OTHER, environment=Environment.UNKNOWN)]))
    assert weak.factor_averages["classification"] < strong.factor_averages["classification"]


def test_live_price_raises_cost_confidence():
    static = score_plan(_plan([_cp("a", price_source="static")]))
    live = score_plan(_plan([_cp("a", price_source="live")]))
    assert live.factor_averages["cost"] > static.factor_averages["cost"]


def test_levels_and_bounds():
    # Weakest possible: other tier, no util, no os, static price.
    plan = _plan([_cp("a", tier=Tier.OTHER, environment=Environment.UNKNOWN,
                      right_sized=False, price_source="static")])
    c = score_plan(plan, [NormalizedVM(vm_name="a", cpu=2, memory_gib=4)])
    assert 0.0 <= c.overall <= 1.0
    assert c.workloads[0].level in {"high", "medium", "low"}
    # Each workload carries all four factors.
    assert {f.factor for f in c.workloads[0].factors} == {"sizing", "classification", "image", "cost"}


def test_empty_plan_is_safe():
    c = score_plan(_plan([]))
    assert c.overall == 0.0
    assert c.workloads == []


def test_score_on_real_fixture(rvtools_path):
    vms = normalize(resolve_source(rvtools_path).parse(rvtools_path))
    plan = build_migration_plan(vms, "rv", get_target("aws"))
    c = score_plan(plan, vms)
    assert len(c.workloads) == len(vms)
    assert 0.0 <= c.overall <= 1.0
    assert set(c.factor_averages) == {"sizing", "classification", "image", "cost"}
