"""Infrastructure Graph — the renderer-neutral topology IR."""
from iactranslate.agents import build_migration_plan
from iactranslate.diagram import (
    architecture_svg,
    architecture_svg_from_graph,
)
from iactranslate.generator.renderer import _assign_subnets as terraform_assign_subnets
from iactranslate.graph import EdgeKind, NodeKind, assign_subnets, build_graph
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


def test_instance_attributes_carry_render_detail():
    plan, g = _graph()
    for c, inst in zip(plan.compute, g.nodes_of(NodeKind.INSTANCE)):
        assert inst.attributes["image_key"] == c.image_key
        assert inst.attributes["root_volume_gib"] == c.root_volume_gib
        assert inst.attributes["extra_volumes_gib"] == list(c.extra_volumes_gib)


def test_security_group_attributes_carry_ingress_rules():
    plan, g = _graph()
    for sg, node in zip(plan.network.security_groups, g.nodes_of(NodeKind.SECURITY_GROUP)):
        assert len(node.attributes["ingress"]) == len(sg.ingress)
        for rule, r in zip(node.attributes["ingress"], sg.ingress):
            assert rule["protocol"] == r.protocol
            assert rule["from_port"] == r.from_port
            assert rule["to_port"] == r.to_port
            assert rule["cidr_blocks"] == r.cidr_blocks


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


def test_instances_spread_across_subnets_of_the_same_tier():
    """Regression: instances of one tier must not all collapse onto a single
    subnet when more than one subnet of that tier exists (ADR 0016)."""
    plan, g = _graph()
    public_subnets = {n.id for n in g.nodes_of(NodeKind.SUBNET) if n.attributes["tier"] == "public"}
    used_public_subnets = {
        e.target
        for inst in g.nodes_of(NodeKind.INSTANCE)
        if inst.attributes["subnet_tier"] == "public"
        for e in g.out_edges(inst.id, EdgeKind.PLACED_IN)
    }
    assert len(public_subnets) > 1, "fixture should exercise multiple public subnets"
    assert len(used_public_subnets) > 1, "public instances all landed on the same subnet"
    assert used_public_subnets <= public_subnets


def test_terraform_and_graph_agree_on_subnet_placement():
    """Terraform/Pulumi's subnet_of must exactly match the graph's placed_in
    edges — one placement decision, not two independent ones (ADR 0016)."""
    plan, g = _graph()
    from_graph = assign_subnets(plan)
    from_terraform = terraform_assign_subnets(plan)
    assert from_graph == from_terraform


def test_load_balancer_nodes_front_and_are_placed_and_secured():
    plan, g = _graph()
    lbs = g.nodes_of(NodeKind.LOAD_BALANCER)
    assert lbs, "fixture should have at least one load balancer"
    subnet_ids = {n.id for n in g.nodes_of(NodeKind.SUBNET)}
    sg_ids = {n.id for n in g.nodes_of(NodeKind.SECURITY_GROUP)}
    instance_ids = {n.id for n in g.nodes_of(NodeKind.INSTANCE)}
    for lb in lbs:
        placed = g.out_edges(lb.id, EdgeKind.PLACED_IN)
        secured = g.out_edges(lb.id, EdgeKind.SECURED_BY)
        fronted = g.out_edges(lb.id, EdgeKind.FRONTS)
        assert placed and {e.target for e in placed} <= subnet_ids
        assert secured and {e.target for e in secured} <= sg_ids
        assert len(fronted) >= 2, "a load balancer should front more than one instance"
        assert {e.target for e in fronted} <= instance_ids


def test_load_balancer_spans_every_subnet_of_its_tier():
    plan, g = _graph()
    for lb in g.nodes_of(NodeKind.LOAD_BALANCER):
        expected = {
            n.id for n in g.nodes_of(NodeKind.SUBNET)
            if n.attributes["tier"] == lb.attributes["subnet_tier"]
        }
        actual = {e.target for e in g.out_edges(lb.id, EdgeKind.PLACED_IN)}
        assert actual == expected
