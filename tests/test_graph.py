"""Infrastructure Graph — the renderer-neutral topology IR."""
from iactranslate.agents import build_migration_plan
from iactranslate.diagram import (
    architecture_svg,
    architecture_svg_from_graph,
)
from iactranslate.graph import EdgeKind, NodeKind, build_graph
from iactranslate.normalize import normalize
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


def _graph(path="tests/fixtures/rvtools_sample.xlsx", target="aws"):
    vms = normalize(resolve_source(path).parse(path))
    plan = build_migration_plan(vms, "g", get_target(target))
    return plan, build_graph(plan)


def test_graph_has_expected_node_kinds():
    plan, g = _graph()
    assert len(g.nodes_of(NodeKind.VPC)) == 1
    assert len(g.nodes_of(NodeKind.SUBNET)) == len(plan.network.subnets)
    assert len(g.nodes_of(NodeKind.SECURITY_GROUP)) == len(plan.network.security_groups)
    assert len(g.nodes_of(NodeKind.INSTANCE)) == plan.vm_count


def test_edges_are_referentially_valid():
    _plan, g = _graph()
    ids = {n.id for n in g.nodes}
    for e in g.edges:
        assert e.source in ids and e.target in ids


def test_vpc_contains_every_subnet():
    _plan, g = _graph()
    contained = {e.target for e in g.out_edges("vpc", EdgeKind.CONTAINS)}
    assert contained == {n.id for n in g.nodes_of(NodeKind.SUBNET)}


def test_every_instance_placed_and_secured():
    _plan, g = _graph()
    for inst in g.nodes_of(NodeKind.INSTANCE):
        assert g.out_edges(inst.id, EdgeKind.PLACED_IN), f"{inst.name} not placed"
        assert g.out_edges(inst.id, EdgeKind.SECURED_BY), f"{inst.name} not secured"


def test_instance_attributes_carry_topology():
    _plan, g = _graph()
    inst = g.nodes_of(NodeKind.INSTANCE)[0]
    assert "instance_type" in inst.attributes
    assert inst.attributes["subnet_tier"] in {"public", "private"}
    assert inst.attributes["tier"] in {"web", "app", "database", "cache", "other"}


def test_diagram_consumes_graph_identically():
    plan, g = _graph()
    # The convenience wrapper builds the graph; both paths must agree.
    assert architecture_svg(plan) == architecture_svg_from_graph(g)


def test_graph_is_deterministic():
    _p, g1 = _graph()
    _p2, g2 = _graph()
    assert g1.model_dump() == g2.model_dump()


def test_graph_serializes():
    _plan, g = _graph()
    data = g.model_dump(mode="json")
    assert data["schema_version"] == 1
    assert data["nodes"] and data["edges"]
