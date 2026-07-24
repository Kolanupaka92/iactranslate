"""Architecture diagram generators — deterministic SVG + Mermaid."""
from iactranslate.agents import build_migration_plan
from iactranslate.diagram import architecture_mermaid, architecture_svg
from iactranslate.models import (
    ComputePlan,
    Environment,
    MigrationPlan,
    NetworkPlan,
    SubnetTier,
    Tier,
)
from iactranslate.normalize import normalize
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


def _plan(compute):
    return MigrationPlan(
        project_name="d", target="aws", region="us-east-1",
        network=NetworkPlan(), compute=compute,
    )


def _cp(name, tier, subnet):
    return ComputePlan(
        vm_name=name, resource_name=name, instance_type="t3.medium", image_key="ubuntu-22.04",
        vcpu=2, memory_gib=4, root_volume_gib=30, tier=tier, environment=Environment.PRODUCTION,
        subnet_tier=subnet,
    )


def test_svg_is_wellformed():
    plan = _plan([
        _cp("web-1", Tier.WEB, SubnetTier.PUBLIC),
        _cp("db-1", Tier.DATABASE, SubnetTier.PRIVATE),
    ])
    svg = architecture_svg(plan)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "web-1" in svg and "db-1" in svg
    assert "Public subnet" in svg and "Private subnet" in svg
    assert plan.network.vpc_cidr in svg


def test_svg_is_deterministic():
    plan = _plan([_cp("a", Tier.WEB, SubnetTier.PUBLIC)])
    assert architecture_svg(plan) == architecture_svg(plan)


def test_svg_caps_large_lanes():
    # 20 public instances — only a capped number are drawn, rest summarised.
    plan = _plan([_cp(f"w{i}", Tier.WEB, SubnetTier.PUBLIC) for i in range(20)])
    svg = architecture_svg(plan)
    assert "more instance(s)" in svg


def test_mermaid_structure():
    plan = _plan([
        _cp("web-1", Tier.WEB, SubnetTier.PUBLIC),
        _cp("db-1", Tier.DATABASE, SubnetTier.PRIVATE),
    ])
    mer = architecture_mermaid(plan)
    assert mer.startswith("graph TD")
    assert "subgraph VPC" in mer
    assert "web-1" in mer and "db-1" in mer


def test_diagram_on_real_fixture(rvtools_path):
    vms = normalize(resolve_source(rvtools_path).parse(rvtools_path))
    plan = build_migration_plan(vms, "rv", get_target("aws"))
    svg = architecture_svg(plan)
    assert svg.startswith("<svg")
    # Every instance name that fits the cap should be labelled.
    assert any(c.vm_name in svg for c in plan.compute)


def _real_plan(path="tests/fixtures/rvtools_sample.xlsx", target="aws"):
    vms = normalize(resolve_source(path).parse(path))
    return build_migration_plan(vms, "d", get_target(target))


def test_svg_shows_load_balancers():
    plan = _real_plan()
    svg = architecture_svg(plan)
    for lb in plan.network.load_balancers:
        assert lb.name in svg


def test_mermaid_shows_load_balancers_and_fronting_edges():
    plan = _real_plan()
    mer = architecture_mermaid(plan)
    for lb in plan.network.load_balancers:
        assert lb.name in mer
        assert mer.count("-->") >= len(lb.targets)
